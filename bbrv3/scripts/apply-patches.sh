#!/bin/sh
# Apply the BBRv3 backport to an ACK android14-6.1 tree.
# Usage: apply-patches.sh <kernel-tree>
set -eu

KERNEL_DIR=${1:?usage: $0 <kernel-tree>}
HERE=$(cd "$(dirname "$0")/.." && pwd)

cd "$KERNEL_DIR"

for p in \
    "$HERE/patches/0001-tcp-core-bbrv3.patch" \
    "$HERE/patches/0002-tcp-bbr-v3-algorithm.patch" \
    "$HERE/patches/0003-module-tolerate-crc-mismatch.patch"
do
    echo "applying $(basename "$p")"
    git apply --check "$p"
    git apply "$p"
done

echo "done"
