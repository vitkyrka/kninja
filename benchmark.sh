#!/bin/bash -ex
#
# Run in a kernel source git with something like:
#
# $ ../kninja/benchmark.sh >log.txt 2>&1
#

mypath=${0%/*}

git reset --hard
make clean

make defconfig
$mypath/kninja.py

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
