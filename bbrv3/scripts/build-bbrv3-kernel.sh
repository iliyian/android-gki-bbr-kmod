#!/bin/sh
# Build a BBRv3-enabled GKI Image from an ACK android14-6.1 tree that has
# the patches applied.
#
# Usage: build-bbrv3-kernel.sh <kernel-tree> [out-dir]
#
# Toolchain used for the verified build:
#   Ubuntu clang 17.0.6 / LLD 17.0.6 / pahole 1.31
set -eu

KERNEL_DIR=${1:?usage: $0 <kernel-tree> [out-dir]}
OUT=${2:-"$KERNEL_DIR/out-bbrv3"}

export PATH=/usr/lib/llvm-17/bin:$PATH

make -C "$KERNEL_DIR" ARCH=arm64 LLVM=1 O="$OUT" gki_defconfig

"$KERNEL_DIR/scripts/config" --file "$OUT/.config" \
    -e TCP_CONG_ADVANCED \
    -e TCP_CONG_BBR \
    -e DEFAULT_CUBIC \
    -d DEFAULT_BBR \
    -e MODVERSIONS \
    -d MODULE_SIG_PROTECT \
    --set-str LOCALVERSION "-android14-11" \
    -d LOCALVERSION_AUTO

# Keep LTO_NONE (matches the verified build; gki_defconfig default).
"$KERNEL_DIR/scripts/config" --file "$OUT/.config" -e LTO_NONE

make -C "$KERNEL_DIR" ARCH=arm64 LLVM=1 O="$OUT" olddefconfig
make -C "$KERNEL_DIR" ARCH=arm64 LLVM=1 O="$OUT" -j"$(nproc)" Image

echo "Image: $OUT/arch/arm64/boot/Image"
