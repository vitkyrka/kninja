import numpy


# MurmurHash2 (MurmurHash64A()) by Austin Appleby.  Public domain.
def _hash64(key, seed=0):
    aligned = (len(key) // 8) * 8

    m = numpy.uint64(0xc6a4a7935bd1e995)
    r = numpy.uint64(47)

    h = numpy.uint64(seed) ^ numpy.uint64((numpy.uint64(len(key)) * m))

    data = numpy.frombuffer(key[:aligned], dtype=numpy.uint64)
    for k in data:
        k *= m
        k ^= k >> r
        k *= m

        h ^= k
        h *= m

    left = len(key) - aligned
    if left:
        data2 = key[aligned:]
        for i in reversed(range(left)):
            h ^= numpy.uint64(data2[i]) << numpy.uint64(i * 8)
        h *= m

    h ^= h >> r
    h *= m
    h ^= h >> r

    return h


def hash64(key, seed=0):
    with numpy.errstate(over='ignore'):
        return _hash64(key, seed)
