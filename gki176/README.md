# K80/zorn on ACK 6.1.176: valid-clones fix + dual BBR

Target: ACK `android14-6.1-2026-09` (`8bc4db3e2`), kernel `6.1.176`.
Device: Redmi K80 (`zorn`, `24117RK2CC`), HyperOS 3 / Android 16,
KMI `android14-6.1`, vendor modules built at 6.1.68 / 6.1.138.

Status: boot, display, audio, root, module loading and dual-BBR switching
verified on hardware. `Image`/boot hashes below.

## 1. Display: 6.1.138 was not a hard ceiling

6.1.145/6.1.176 builds booted but the panel flickered continuously. The actual
cause is a stable backport, not a general "newer GKI is incompatible":

- stable **6.1.141** added upstream commit
  `41b4b11da02157c7474caf41d56baae0e941d01a` ("drm: Add valid clones check").
- `drm_atomic_helper_check_modeset()` then requires every encoder on a CRTC to
  list the others in `possible_clones`.
- zorn's vendor `msm_drm.ko` (built at 6.1.68) puts DSI and Virtual encoders on
  CRTC 203. Their `possible_clones` are not reciprocal, so every affected
  modeset fails:

```
[drm:drm_atomic_helper_check_modeset] crtc203 failed valid clone check for mask 0x6
SDM: drmModeAtomicCommit failed with error 22 (Invalid argument). crtc=203
SDM: COMMIT failed: 11
```

- `patch 0001` reverts the function and its call, restoring pre-6.1.141
  behavior. After that, `COMMIT failed` and `drmModeAtomicCommit failed` are 0.

Version boundary: 6.1.138/139/140 have no such check; 6.1.141+ do.

## 2. Dual BBR

Both algorithms are built into the kernel:

| Algorithm | File | Registered name |
|---|---|---|
| BBRv1 | `net/ipv4/tcp_bbr.c` (stock) | `bbr` |
| BBRv3 | `net/ipv4/tcp_bbr3.c` (new) | `bbr3` |

BBRv3 is the 2025-03 Google `google/bbr` `v3` backport already used on 6.1.138,
adapted to the 6.1.176 core. BBRv1 is kept and only adapted to the new
`tso_segs(sk, mss_now)` callback introduced by the BBRv3 core patch.

Defaults:

```
CONFIG_DEFAULT_TCP_CONG="bbr3"
CONFIG_DEFAULT_NET_SCH="fq"
CONFIG_DEFAULT_BBR3=y
CONFIG_DEFAULT_FQ=y
```

The `bbr3_fq` KernelSU userspace module can still enforce `bbr3` + `fq` per
interface after boot; the old `tcp_bbr_kmod` must stay disabled.

## 3. Patch series

Apply in order from the kernel tree root (ACK `android14-6.1-2026-09`):

```bash
patch -p1 < gki176/patches/0001-revert-valid-clones-check.patch
patch -p1 < gki176/patches/0002-tcp-core-bbrv3.patch
patch -p1 < gki176/patches/0003-module-tolerate-crc-mismatch.patch
patch -p1 < gki176/patches/0004-tcp-bbr3-algorithm.patch
patch -p1 < gki176/patches/0005-bbr1-tso-segs-and-defaults.patch
# optional: only needed when building with Ubuntu clang 17
patch -p1 < gki176/patches/0006-libbpf-clang17-cast.patch
```

Notes:

- `0002` applies to 6.1.176 with offsets; `net/ipv4/tcp_input.c` has one fuzz 1
  at the `tcp_process_tlp_ack(..., &rs)` call. The resulting code is correct.
- `0003` is the deliberate module CRC tolerance; see risks below.
- `0004` adds `net/ipv4/tcp_bbr3.c` as a new file.
- `0005` adds the BBRv3 Kconfig/Makefile entries, the `DEFAULT_BBR3` choice,
  and the BBRv1 `tso_segs` adaptation.
- `0006` touches only the host tool `tools/lib/bpf/libbpf.c`; it does not enter
  the phone kernel.

## 4. Build

```bash
export PATH=/usr/lib/llvm-17/bin:$PATH
make ARCH=arm64 LLVM=1 O=out gki_defconfig
cat gki176/config/k80-6.1.176.fragment >> out/.config
make ARCH=arm64 LLVM=1 O=out olddefconfig
make ARCH=arm64 LLVM=1 O=out -j10 Image
```

The result is `out/arch/arm64/boot/Image`, release
`6.1.176-android14-11+`. Repack into the stock zorn boot image:

```bash
magiskboot unpack stock-boot.img
cp out/arch/arm64/boot/Image kernel
magiskboot repack stock-boot.img gki176-bbr3-dual-boot.img
```

The bootloader ignores `fastboot boot`; flash `boot_a` and keep a stock
rollback image.

## 5. Verification (2026-11-05, real device)

| Item | Result |
|---|---|
| `uname -r` | `6.1.176-android14-11+` |
| `sys.boot_completed` | 1 |
| `tcp_available_congestion_control` | `reno bbr bbr3 cubic` |
| `tcp_congestion_control` | `bbr3` (default) |
| switch `bbr` / `bbr3` | both succeed |
| `net.core.default_qdisc` | `fq` |
| `rmnet_data0` qdisc | `fq` |
| modules / `Failed to insmod` | 501 / 0 |
| CRC tolerance log | 1 (`tolerating symbol version mismatch`) |
| sound card | `pineapple-mtp-snd-card` |
| root | KernelSU LKM, `u:r:ksu:s0` |
| display atomic failures | 0 after wake/swipe |
| HTTP with `bbr3` | 200; `ss -tin` shows `bbr3` |
| HTTP with `bbr` | 200; `ss -tin` shows `bbr` |

## 6. Artifacts

```
263d14815f0d9f58a2e37dff0b8d67a58baa5ca8046f61f1eeb4b7532bb0c47f  Image
827f8865666e709059230b76c640a1cc35cdea10d49f7253faf9935499440475  gki176-bbr3-dual-boot.img
d34718c8fdade3deb08d94319024f451847703a34c1878ca155fd3015bcd609e  gki176-clonefix-boot.img (display fix only)
a1a041eb90226266244844c45adf650f665e930f622cc1db56afb95aba38affd  stock boot_a
```

Binaries are not committed. Rebuild from the patches or use the locally
verified images referenced by hash.

## 7. Risks / trade-offs

- `CONFIG_MODULE_SIG_PROTECT=n`: required for stock 6.1.68/6.1.138 vendor
  modules to load on a custom GKI. It disables Android's protected-export
  gate; `CONFIG_MODULE_SIG=y` remains but `MODULE_SIG_FORCE` is off.
- `0003` makes `check_version()` accept any symbol CRC mismatch. This is a
  blanket relaxation needed because the BBRv3 header changes shift genksyms
  CRCs for ~691 symbols. It does not bypass cryptographic module signatures or
  SELinux, but it is broader than a whitelist. Tighten to a known-CRC
  whitelist if required.
- `0001` removes a DRM validation check. It restores the behavior zorn shipped
  with (6.1.138-6.1.140); it is not a security boundary and the atomic ioctl
  requires DRM master.
- No syscall/SELinux/binder/cred/netfilter/debugfs interfaces were added or
  modified.

See `bbrv3/docs/BBRV3_PHONE_JOURNAL.md` sections 19-21 for the full log,
kprobe evidence and experiment history.
