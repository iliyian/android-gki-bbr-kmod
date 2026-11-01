#!/usr/bin/env bash
#
# Build tcp_bbr.ko for an Android GKI kernel and package it as a KernelSU module.
#
# The kernel must be built from Android Common Kernel (ACK), not mainline stable.
# The compiler should match the device kernel's CC_VERSION_TEXT.
#
# Required inputs:
#   CONFIG_GZ=/path/to/config.gz   (or CONFIG_FROM_ADB=1 with a rooted device)
#
# Optional environment:
#   ACK_TAG      default android14-6.1.138_r00
#   CLANG_VER    default clang-r487747c
#   CLANG_BRANCH default android14-release
#   MIRROR       default TUNA AOSP mirror
#   WORK         default ~/.cache/android-gki-bbr
#   OUT_DIR      default ./out
#   JOBS         default nproc
#
set -euo pipefail

ACK_TAG="${ACK_TAG:-android14-6.1.138_r00}"
CLANG_VER="${CLANG_VER:-clang-r487747c}"
CLANG_BRANCH="${CLANG_BRANCH:-android14-release}"
MIRROR="${MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/git/AOSP}"
WORK="${WORK:-$HOME/.cache/android-gki-bbr}"
JOBS="${JOBS:-$(nproc)}"
CONFIG_GZ="${CONFIG_GZ:-}"
CONFIG_FROM_ADB="${CONFIG_FROM_ADB:-0}"
OUT_DIR="${OUT_DIR:-$(pwd)/out}"
ADB="${ADB:-adb}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACK_DIR="$WORK/ack-$ACK_TAG"
CLANG_DIR="$WORK/clang-$CLANG_VER"
MOD_DIR="$WORK/module-$ACK_TAG"
KSU_TEMPLATE="$REPO_ROOT/ksu-module"

log() { printf '\n=== %s ===\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

log "checking host dependencies"
for c in git curl make bc flex bison python3 zcat; do need "$c"; done

mkdir -p "$WORK" "$OUT_DIR"

# ---------------------------------------------------------------------------
# 1. ACK source at the matching GKI release tag
# ---------------------------------------------------------------------------
if [ ! -f "$ACK_DIR/Makefile" ]; then
    log "fetching ACK $ACK_TAG"
    rm -rf "$ACK_DIR"
    mkdir -p "$ACK_DIR"
    git -C "$ACK_DIR" init -q
    git -C "$ACK_DIR" remote add origin "$MIRROR/kernel/common.git"
    git -C "$ACK_DIR" fetch --depth 1 origin "refs/tags/$ACK_TAG"
    git -C "$ACK_DIR" checkout -q FETCH_HEAD
else
    log "reusing ACK source at $ACK_DIR"
fi

grep -q '^VERSION = 6$' "$ACK_DIR/Makefile" || die "unexpected ACK source tree"
grep -q 'CONFIG_ANDROID_KABI_RESERVE' "$ACK_DIR/include/linux/android_kabi.h" \
    || die "this tree is not an ACK tree (android_kabi.h missing)"

# ---------------------------------------------------------------------------
# 2. AOSP clang matching the device kernel's CC_VERSION_TEXT
# ---------------------------------------------------------------------------
if [ ! -x "$CLANG_DIR/bin/clang" ]; then
    log "fetching AOSP $CLANG_VER (sparse checkout, several GB)"
    rm -rf "$CLANG_DIR"
    mkdir -p "$CLANG_DIR"
    git -C "$CLANG_DIR" init -q
    git -C "$CLANG_DIR" remote add origin "$MIRROR/platform/prebuilts/clang/host/linux-x86.git"
    git -C "$CLANG_DIR" fetch --depth 1 origin "$CLANG_BRANCH"
    git -C "$CLANG_DIR" sparse-checkout init --cone
    git -C "$CLANG_DIR" sparse-checkout set "$CLANG_VER"
    git -C "$CLANG_DIR" checkout -q FETCH_HEAD
else
    log "reusing clang at $CLANG_DIR"
fi
export PATH="$CLANG_DIR/bin:$PATH"
clang --version | head -1

# ---------------------------------------------------------------------------
# 3. Device kernel config
# ---------------------------------------------------------------------------
if [ -n "$CONFIG_GZ" ]; then
    [ -f "$CONFIG_GZ" ] || die "CONFIG_GZ not found: $CONFIG_GZ"
    log "using config: $CONFIG_GZ"
elif [ "$CONFIG_FROM_ADB" = "1" ]; then
    need "$ADB"
    log "pulling /proc/config.gz from device (requires root)"
    "$ADB" exec-out su -c 'cat /proc/config.gz' > "$WORK/config.gz"
    CONFIG_GZ="$WORK/config.gz"
else
    die "provide CONFIG_GZ=/path/to/config.gz or CONFIG_FROM_ADB=1"
fi
zcat "$CONFIG_GZ" > "$ACK_DIR/.config" || die "cannot decompress $CONFIG_GZ"

log "configuring kernel tree"
cd "$ACK_DIR"
# Keep BTF options exactly as the device has them: CONFIG_DEBUG_INFO_BTF_MODULES
# changes the layout of struct module.  Do NOT disable them here.
scripts/config --disable WERROR
scripts/config --enable TCP_CONG_ADVANCED
scripts/config --module TCP_CONG_BBR
# GKI config references a generated whitelist that is absent from the tag tree.
scripts/config --disable TRIM_UNUSED_KSYMS
scripts/config --set-str UNUSED_KSYMS_WHITELIST ""
# ksud insmod needs an uncompressed .ko
scripts/config --disable MODULE_COMPRESS_ZSTD
scripts/config --disable MODULE_COMPRESS_XZ
scripts/config --disable MODULE_COMPRESS_GZIP

make ARCH=arm64 LLVM=1 olddefconfig >/dev/null
grep -q '^CONFIG_TCP_CONG_BBR=m' .config || die "CONFIG_TCP_CONG_BBR was not enabled"

DEV_CC="$(zcat "$CONFIG_GZ" | sed -n 's/^CONFIG_CC_VERSION_TEXT="\(.*\)"/\1/p')"
OUR_CC="$(grep '^CONFIG_CC_VERSION_TEXT=' .config | sed 's/^CONFIG_CC_VERSION_TEXT="\(.*\)"/\1/')"
if [ "$DEV_CC" != "$OUR_CC" ]; then
    printf 'WARNING: compiler mismatch.\n  device: %s\n  build : %s\n' "$DEV_CC" "$OUR_CC" >&2
    printf 'kCFI hashes are usually stable, but matching clang is recommended.\n' >&2
fi

log "preparing build tree"
make ARCH=arm64 LLVM=1 -j"$JOBS" prepare >/dev/null
make ARCH=arm64 LLVM=1 arch/arm64/kernel/asm-offsets.s >/dev/null
make ARCH=arm64 LLVM=1 scripts/module.lds >/dev/null

# ---------------------------------------------------------------------------
# 4. Build tcp_bbr.ko as an external module
# ---------------------------------------------------------------------------
log "building tcp_bbr.ko"
rm -rf "$MOD_DIR"
mkdir -p "$MOD_DIR"
cp net/ipv4/tcp_bbr.c "$MOD_DIR/"
printf 'obj-m := tcp_bbr.o\n' > "$MOD_DIR/Makefile"

# The empty make variables only skip BTF encoding/linking steps that require a
# locally built vmlinux.  autoconf.h still defines the macros, so struct module
# keeps the same layout as the device kernel.
make ARCH=arm64 LLVM=1 -j"$JOBS" M="$MOD_DIR" \
    CONFIG_DEBUG_INFO_BTF= CONFIG_DEBUG_INFO_BTF_MODULES= modules

KO="$MOD_DIR/tcp_bbr.ko"
[ -f "$KO" ] || die "build failed: $KO not found"
cp "$KO" "$OUT_DIR/tcp_bbr.ko"

# ---------------------------------------------------------------------------
# 5. Package a KernelSU module
# ---------------------------------------------------------------------------
log "packaging KernelSU module"
PKG="$WORK/pkg-$ACK_TAG"
rm -rf "$PKG"
mkdir -p "$PKG"
cp "$KSU_TEMPLATE/module.prop" "$KSU_TEMPLATE/post-fs-data.sh" \
   "$KSU_TEMPLATE/service.sh" "$KSU_TEMPLATE/uninstall.sh" "$PKG/"
cp "$KO" "$PKG/tcp_bbr.ko"

ZIP="$OUT_DIR/tcp_bbr_kmod.zip"
python3 - "$PKG" "$ZIP" <<'PY'
import os, sys, zipfile
src, out = sys.argv[1], sys.argv[2]
files = ["module.prop", "post-fs-data.sh", "service.sh", "uninstall.sh", "tcp_bbr.ko"]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in files:
        info = zipfile.ZipInfo(name)
        mode = 0o755 if name.endswith(".sh") else 0o644
        info.external_attr = (mode | 0o100000) << 16
        with open(os.path.join(src, name), "rb") as fh, z.open(info, "w") as zo:
            zo.write(fh.read())
print("wrote", out)
PY

log "done"
printf 'ko : %s\nzip: %s\n\n' "$OUT_DIR/tcp_bbr.ko" "$ZIP"
printf 'Next: verify ABI (docs/verification.md), then install the zip with the KernelSU manager.\n'
printf 'Load manually: su -c "ksud insmod %s"\n' "$KO"
