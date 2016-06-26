#!/usr/bin/env python3

import argparse
import logging
import os
import re
import shlex
import subprocess
import fnmatch

import ninja_syntax

import ninja_internal

# These objects either get modified in the final link stage (which is opaque to
# ninja) or generate files that are later used in some rules which are not
# currently handled by ninja.  We ignore these to ensure that interchangably
# running make and ninja without any changes to the tree does not cause any
# rebuilds.  Use "make V=2" and "ninja -d explain -v" to debug this.

IGNORES = [
    'arch/arm/boot/compressed/piggy.o',

    'arch/x86/boot/cpu.o',
    'arch/x86/boot/compressed/misc.o',
    'arch/x86/boot/compressed/piggy.o',
    'arch/x86/boot/header.o',
    'arch/x86/boot/version.o',
    'arch/x86/realmode/rmpiggy.o',

    'init/version.o',

    'lib/gen_crc32table',
    'scripts/basic/bin2c',
    'scripts/basic/fixdep',
    'scripts/mod/empty.o',
    'scripts/mod/mk_elfconfig',
    'scripts/pnmtologo',
    'usr/gen_init_cpio',

    'vmlinux.o'
]

WILDCARD_IGNORES = [
    'arch/x86/realmode/rm/*',
    'arch/x86/entry/vdso/*',
    'arch/arm/vdso/*',
    'arch/x86/tools/*',

    'scripts/mod/*',
]


class Kninja(object):

    def __init__(self):
        pass

    def fixname(self, name):
        return name.replace('/', '_')

    def should_ignore(self, obj):
        if obj in IGNORES:
            logging.debug('Ignoring %s', obj)
            return True

        for ign in WILDCARD_IGNORES:
            if fnmatch.fnmatch(obj, ign):
                logging.debug('Ignoring %s', obj)
                return True

        return False

    def convert(self, makedb):
        gotvmlinux = False

        rulenames = []
        builds = []
        rules = []
        alldeps = []
        cmds = []
        srctree = ''
        objtree = ''

        for line in makedb:
            if not gotvmlinux and line.startswith('vmlinux: '):
                deps = line.rstrip().replace('vmlinux: ', '').split(' ')
                deps = [d for d in deps
                        if d not in ('vmlinux_prereq', 'FORCE')]

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

                cmds.append(('vmlinux', makeall))
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
                    and '.c' not in line:
                obj, deps = line.rstrip().split(': ')

                if self.should_ignore(obj):
                    continue

                deps = [d for d in deps.split(' ') if d != 'FORCE']
                fixed = self.fixname(obj)

                builds.append({'outputs': obj,
                               'rule': 'cmd_' + fixed,
                               'inputs': deps})
                continue

            try:
                var, val = line.rstrip().split(' := ')
            except ValueError:
                continue

            if var.startswith('cmd_'):
                obj = var.replace('cmd_', '')
                if obj in ('files', 'vmlinux'):
                    continue
                cmdname = self.fixname(var)
                args = shlex.split(val)
                md = [arg for arg in args if '-MD' in arg]
                if md:
                    depfile = md[0].split('-MD,')[1]
                    deps = 'gcc'
                else:
                    depfile = None
                    deps = None

                if self.should_ignore(obj):
                    continue

                if cmdname in rulenames:
                    logging.debug('Ignoring duplicate rule %s', var)
                    continue

                rulenames.append(cmdname)
                rules.append({'name': cmdname,
                              'command': val,
                              'deps': deps,
                              'depfile': depfile})

                cmds.append((obj, val))

            elif var.startswith('deps_'):
                obj = var.replace('deps_', '')
                if self.should_ignore(obj):
                    continue

                try:
                    mtime = os.stat(obj).st_mtime
                except OSError:
                    continue

                val = re.sub(r'\$\(subst[^)]+\)', '', val)
                val = re.sub(r'\$\(wildcard[^)]+\)', '', val)

                deps = [p for p in val.split(' ')
                        if p and not p.startswith('include/config/')]
                alldeps.append((obj, mtime, deps))

            elif var.startswith('source_'):
                obj = var.replace('source_', '')
                if self.should_ignore(obj):
                    continue

                name = self.fixname(obj)
                builds.append({'outputs': obj,
                               'rule': 'cmd_' + name,
                               'inputs': val.split(' ')})

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

        logging.info('Caching make database to %s', cachefile)
        with open(cachefile, 'w+') as f:
            f.write(out)

        makedb = out.split('\n')

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

if __name__ == '__main__':
    main()
