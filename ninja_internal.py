import array
import struct

import mmh2


def write_deps(f, alldeps):
    signature = '# ninjadeps\n'
    version = 4

    paths = []
    for out, mtime, deps in alldeps:
        paths.append(out)
        paths.extend(deps)

    paths = set(paths)
    pathids = {path: _id for _id, path, in enumerate(paths)}

    f.write(signature.encode('utf-8'))
    f.write(struct.pack('i', version))

    for _id, path in enumerate(paths):
        data = path.encode('utf-8')
        if len(data) % 4:
            data += b'\x00' * (len(data) % 4)

        f.write(struct.pack('I', len(data) + 4))
        f.write(data)
        f.write(struct.pack('i', ~_id))

    for out, mtime, deps in alldeps:
        size = (1 + 2 + len(deps)) * 4
        f.write(struct.pack('I', size | (1 << 31)))
        f.write(struct.pack('iII', pathids[out], mtime & 0xffffffff, (mtime >> 32) & 0xffffffff))
        f.write(array.array('I', [pathids[d] for d in deps]).tobytes())


def write_log(f, cmds):
    seed = 0xDECAFBADDECAFBAD

    f.write('# ninja log v5\n')
    for obj, cmd in cmds:
        hsh = mmh2.hash64(cmd.encode('utf-8'), seed)
        f.write('%d\t%d\t%d\t%s\t%x\n' % (0, 0, 0, obj, hsh))
