#!/bin/sh -ex

fail() {
	echo Failed.
	exit 1
}

mypath=${0%/*}

config=allnoconfig
log=$mypath/testlog
src=$PWD

runtest() {
	$src/scripts/config --enable MODULES
	$src/scripts/config --enable BLOCK
	$src/scripts/config --module BLK_DEV_NULL_BLK
	make olddefconfig

	$mypath/kninja.py | tee $log
	[ -e vmlinux ] || fail

	vmlinux_before=$(stat -c%Y vmlinux)
	ninja -d explain
	vmlinux_after=$(stat -c%Y vmlinux)
	[ "$vmlinux_before" = "$vmlinux_after" ] || fail

	ninja -d explain | tee $log
	grep -q 'no work' $log || fail
	! grep -q 'kninja' $log || fail

	touch $src/init/calibrate.c
	ninja -d explain | tee $log
	! grep -q 'no work' $log || fail
	! grep -q 'kninja' $log || fail

	vmlinux_before=$(stat -c%Y vmlinux)
	make
	vmlinux_after=$(stat -c%Y vmlinux)
	[ "$vmlinux_before" = "$vmlinux_after" ] || fail

	ninja -d explain | tee $log
	grep -q 'no work' $log || fail
	! grep -q 'kninja' $log || fail

	touch $obj/.config
	ninja -d explain | tee $log
	grep -q 'kninja' $log || fail

	touch $src/init/Kconfig
	ninja -d explain | tee $log
	grep -q 'kninja' $log || fail

	touch $src/arch/arm/boot/compressed/vmlinux.lds.S
	touch $src/arch/arm64/kernel/vdso/sigreturn.S
	touch $src/arch/x86/realmode/init.c
	ninja -d explain | tee $log
	grep -q 'kninja' $log || fail

	vmlinux_before=$(stat -c%Y vmlinux)
	make
	vmlinux_after=$(stat -c%Y vmlinux)
	[ "$vmlinux_before" = "$vmlinux_after" ] || fail

	touch $src/drivers/block/null_blk.h
	ninja -d explain | tee $log
	grep -q 'null_blk' $log || fail
	! grep -q 'no work' $log || fail
	! grep -q 'vmlinux' $log || fail

	before=$(stat -c%Y drivers/block/null_blk.ko)
	make
	after=$(stat -c%Y drivers/block/null_blk.ko)
	[ "$before" = "$after" ] || fail

	make clean

	# Broken after clean
	# ninja -d explain | tee $log
	# grep -q 'kninja' $log || fail
}

intree() {
	cd $src
	git reset --hard
	rm -f build.ninja
	make mrproper

	make $config

	obj=$src
	runtest
}

outoftree() {
	cd $src
	git reset --hard
	rm -f build.ninja
	make mrproper

	obj=$PWD/out
	rm -rf $obj
	mkdir -p $obj

	make O=$obj $config
	cd $obj
	runtest
	cd $src
}

unset ARCH CROSS_COMPILE
intree
outoftree

export ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf-
intree
outoftree

export ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-
intree
outoftree

echo OK.
