#!/bin/bash -ex
#
# Run in a kernel source git with something like:
#
# $ ../kninja/benchmark.sh >log.txt 2>&1
#

mypath=${0%%/*}

git reset --hard
make clean
rm -f .ninja_log .ninja_deps

make defconfig
make -j8

$mypath/kninja.py

# ninja will rebuild here to get dependency information
ninja

# This should be equivalent to a null build if we got our ignores right, but
# let's run it once before the actual benchmarking just in case we didn't.
make -j8

echo '========== No changes, make'
time make -j8

echo '========== No changes, make with filename'
time make -j8 arch/x86/kernel/hw_breakpoint.o

echo '========== No changes, ninja'
time ninja

echo '========== One file change and error, make'
echo error > arch/x86/kernel/hw_breakpoint.c
time make -j8 || :

echo '========== One file change and error, make with filename'
echo error > arch/x86/kernel/hw_breakpoint.c
time make arch/x86/kernel/hw_breakpoint.o || :

echo '========== One file change and error, ninja'
echo error > arch/x86/kernel/hw_breakpoint.c
time ninja || :

git reset --hard

echo '========== One file change and link, make'
touch arch/x86/kernel/hw_breakpoint.c
time make -j8

echo '========== One file change and link, ninja'
touch arch/x86/kernel/hw_breakpoint.c
time ninja
