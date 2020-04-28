#!/usr/bin/env python3

import argparse
import logging
import os
import re
import shlex
import subprocess
import fnmatch
import sys
import re

import ninja_syntax

import ninja_internal

# These objects either get modified in the final link stage (which is opaque to
# ninja) or generate files that are later used in some rules which are not
# currently handled by ninja.  We ignore these to ensure that interchangably
# running make and ninja without any changes to the tree does not cause any
# rebuilds.  Use "make V=2" and "ninja -d explain -v" to debug this.

IGNORES = [
    'init/version.o',
    'lib/gen_crc32table',
    'vmlinux.o',
    'usr/gen_init_cpio',
]

WILDCARD_IGNORES = [
    'arch/arm/boot/*',
    'arch/*/vdso/*',

    'arch/x86/boot/*',
    'arch/x86/entry/vdso/*',
    'arch/x86/realmode/*',
    'arch/x86/tools/*',

    '*/lib-ksyms.o',

    # These depend on include/generated/utsrelease.h which make changes
    '*.mod.o',

    'scripts/*',
]


IGNORE_UNHANDLED = [
        'System.map',
        'Module.symvers',
        'arch/x86/boot/voffset.h',
        'usr/.initramfs_data.cpio.d',
        '.version',
]


class Kninja(object):

    def __init__(self):
        pass

    def fixname(self, name):
        return name.replace('/', '_')

    def fixup_obj(self, obj):
        if obj in IGNORES:
            logging.debug('Ignoring %s', obj)
            return None

        for ign in WILDCARD_IGNORES:
            if fnmatch.fnmatch(obj, ign):
                logging.debug('Ignoring %s', obj)
                return None

        # ARM oprofile objects have .. in their paths
        if '..' in obj:
            return os.path.relpath(obj)

        return obj

    def _parse_tokens(self, line):
        tokens = []
        generated = []
        for token in re.split('\s', line):
            if not token:
                continue

            if token.endswith(':') and line.startswith(token) and len(line.split(' ')) > 1:
                generated.append(token.rstrip(':'))
                continue

            if not re.match('^[-_./A-Za-z0-9]+$', token):
                continue

            if token[0] == '/' or \
                    token[0] == '-' or \
                    token.endswith('..') or \
                    token.startswith('CONFIG_') or \
                    token.startswith('cmd_') or \
                    token.startswith('deps_') or \
                    token.endswith('.cmd') or \
                    token.endswith('modules.order') or \
                    token.endswith('.conf') or \
                    any(t for t in IGNORE_UNHANDLED if token.endswith(t)) or \
                    len(token) < 4:
                continue

            tokens.append(token)

        return tokens, generated

    def convert(self, makedb):
        gotvmlinux = False

        rulenames = []
        builds = []
        rules = []
        alldeps = []
        cmds = []
        srctree = ''
        objtree = ''
        tokens = set()
        generated = set()
        handledfiles = set(['vmlinux'])

        rules.append({'name': 'KNINJA',
                      'command': sys.argv[0],
                      'generator': 1,
                      'pool': 'console'})

        for line in makedb:
            newtokens, newgenerated = self._parse_tokens(line)
            tokens.update(newtokens)
            generated.update(newgenerated)

            if not gotvmlinux and line.startswith('vmlinux: '):
                deps = line.rstrip().replace('vmlinux: ', '').split(' ')
                deps = [d for d in deps
                        if d not in ('autoksyms_recursive', 'vmlinux_prereq', 'FORCE')]

                # This makes make ignore the dependencies for vmlinux, since
                # those are already taken care of by ninja by the time this
                # gets run.
                makeall = r"cat %s | sed -e '/^$(vmlinux-dirs)/,+1d' " \
                    r"| make -f - all"
                makefile = 'Makefile'
                if srctree and objtree:
                    makefile = os.path.join(srctree, makefile)
                    makeall += ' -C%s O=%s' % (srctree, objtree)

                makeall = makeall % makefile
                logging.debug('vmlinux make command: %s', makeall)

                rules.append({'name': 'cmd_vmlinux',
                              'command': makeall.replace('$', '$$'),
                              'pool': 'console'})

                builds.append({'outputs': 'vmlinux',
                               'rule': 'cmd_vmlinux',
                               'inputs': deps})

                cmds.append(('vmlinux', os.stat('vmlinux').st_mtime_ns, makeall))
                gotvmlinux = True
                continue
            elif line.startswith('KBUILD_SRC = '):
                srctree = line.rstrip().replace('KBUILD_SRC = ', '')
                continue
            elif line.startswith('O = '):
                objtree = line.rstrip().replace('O = ', '')
                continue

            # built-in.o and other mod/obj files which just combine obj files
            if ('.o: ' in line or '.ko: ' in line or '.a:' in line) \
                    and 'FORCE' in line \
                    and '%' not in line \
                    and '.h' not in line \
                    and '.S' not in line \
                    and '.c' not in line \
                    and 'objcopy.o' not in line: # lkdtm_ro_objcopy.o
                obj, deps = line.rstrip().split(': ')

                obj = self.fixup_obj(obj)
                if not obj:
                    continue

                deps = [d for d in deps.split(' ') if d != 'FORCE' and d not in IGNORES]
                handledfiles.update(deps)
                fixed = self.fixname(obj)

                builds.append({'outputs': obj,
                               'rule': 'cmd_' + fixed,
                               'inputs': deps})
                continue

            try:
                var, val = line.rstrip('\n').split(' := ')
            except ValueError:
                continue

            if var.startswith('cmd_'):
                obj = var.replace('cmd_', '', 1)
                if obj in ('files', 'vmlinux'):
                    continue
                obj = self.fixup_obj(obj)
                if not obj:
                    continue

                cmdname = 'cmd_' + self.fixname(obj)
                args = shlex.split(val)
                md = [arg for arg in args if '-MD' in arg]
                if md:
                    depfile = md[0].split('-MD,')[1]
                    deps = 'gcc'
                else:
                    depfile = None
                    deps = None

                if cmdname in rulenames:
                    logging.debug('Ignoring duplicate rule %s', var)
                    continue

                cmd = ' '.join(val.split())

                rulenames.append(cmdname)
                rules.append({'name': cmdname,
                              'command': cmd,
                              'deps': deps,
                              'depfile': depfile})

                if deps:
                    handledfiles.update(deps)
                cmds.append((obj, os.stat(obj).st_mtime_ns, cmd))

            elif var.startswith('deps_'):
                obj = var.replace('deps_', '', 1)
                obj = self.fixup_obj(obj)
                if not obj:
                    continue

                try:
                    mtime = os.stat(obj).st_mtime_ns
                except OSError:
                    continue

                val = re.sub(r'\$\(subst[^)]+\)', '', val)
                val = re.sub(r'\$\(wildcard[^)]+\)', '', val)

                deps = [p for p in val.split(' ')
                        if p and not p.startswith('include/config/')]
                handledfiles.update(deps)
                alldeps.append((obj, mtime, deps))

            elif var.startswith('source_'):
                obj = var.replace('source_', '', 1)
                obj = self.fixup_obj(obj)
                if not obj:
                    continue

                name = self.fixname(obj)
                builds.append({'outputs': obj,
                               'rule': 'cmd_' + name,
                               'inputs': val.split(' ')})
                handledfiles.update(val.split(' '))


        logging.info('Finding unhandled files (%d tokens)', len(tokens))

        tokens = set([os.path.realpath(t) for t in tokens])
        tokens.difference_update([os.path.realpath(f) for f in generated])
        tokens.difference_update([os.path.realpath(f) for f in handledfiles])

        if objtree:
            # Every make invocation results in a regeneration of the Makefile
            # in the objdir
            tokens.remove(os.path.realpath('Makefile'))

        unhandledfiles = sorted([f for f in tokens if os.path.isfile(f)])

        builds.append({'outputs': 'build.ninja',
                       'rule': 'KNINJA',
                       'inputs': unhandledfiles})

        return rules, builds, alldeps, cmds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', action='store_true')
    parser.add_argument('--verbose', '-v', default=0, action='count')
    parser.add_argument('--path', default='')
    args = parser.parse_args()

    if args.verbose >= 1:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(format='[%(levelname)s] %(message)s', level=level)

    makedb = []
    cachefile = os.path.join(args.path, '.makedb')

    if args.cache:
        if os.path.exists(cachefile):
            logging.info('Using cached make database %s', cachefile)
            with open(cachefile, 'r') as f:
                makedb = f.readlines()

    if not makedb:
        makeargs = ['-C', args.path] if args.path else []

        cmd = ['make', '-j', '%d' % os.cpu_count()] + makeargs
        logging.info('Ensuring full build: %s', ' '.join(cmd))
        subprocess.check_call(cmd)

        cmd = ['make', '-p'] + makeargs
        logging.info('Generating make database: %s', ' '.join(cmd))
        out = subprocess.check_output(cmd).decode('utf-8')
        makedb = [l for l in out.splitlines() if l and l[0] != '#']

        logging.info('Caching make database to %s', cachefile)
        with open(cachefile, 'w+') as f:
            f.write('\n'.join(makedb))

    kn = Kninja()
    logging.info('Parsing make database (%d lines)', len(makedb))
    rules, builds, alldeps, cmds = kn.convert(makedb)

    ninjafile = os.path.join(args.path, 'build.ninja')
    with open(ninjafile, 'w+') as f:
        w = ninja_syntax.Writer(output=f)

        for rule in rules:
            w.rule(**rule)

        for build in builds:
            w.build(**build)

    logging.info('Wrote build.ninja (%d rules, %d build statements)',
                 len(rules), len(builds))

    depsfile = os.path.join(args.path, '.ninja_deps')
    with open(depsfile, 'wb') as f:
        ninja_internal.write_deps(f, alldeps)

    logging.info('Wrote .ninja_deps (%d targets, %d deps)',
                 len(alldeps), sum([len(d) for _, _, d in alldeps]))

    logfile = os.path.join(args.path, '.ninja_log')
    with open(logfile, 'w') as f:
        ninja_internal.write_log(f, cmds)

    logging.info('Wrote .ninja_log (%d commands)', len(cmds))

    cmd = ['ninja', '-d', 'explain', '-n']
    logging.info('Checking ninja status: %s', ' '.join(cmd))
    out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode('utf-8')
    if 'no work' in out:
        logging.info('All OK')
    else:
        logging.error('%s\n...', '\n'.join(out.splitlines()[:25]))
        logging.error('ninja should be clean, but has work!')
        sys.exit(1)

if __name__ == '__main__':
    main()
