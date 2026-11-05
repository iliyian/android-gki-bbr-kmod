# BBR3 + qdisc Control

SukiSU Ultra / KernelSU module that keeps a user-selected TCP congestion control
and qdisc applied, with a module WebUI for switching both at runtime.

It is the persistence companion to the BBRv3 kernel in [`../gki176/`](../gki176/).
The module does **not** load a kernel module: it assumes the kernel already
registers the algorithms (for example `reno bbr bbr3 cubic`).

Tested on Redmi K80 (zorn), HyperOS 3 / Android 16, kernel
`6.1.176-android14-11+`, SukiSU Ultra v4.3.0.

## Features

- Switch congestion control: `bbr3`, `bbr`, `cubic`, `reno` (only algorithms
  reported by the running kernel are offered).
- Switch root qdisc: `fq`, `fq_codel`, `codel`, `sfq`, `pfifo_fast`, `pfifo`.
  Availability is probed against `dummy0`, so unsupported qdiscs never appear.
- Applies immediately and persists the choice in `/data/adb/bbr3_fq.conf`
  (mode `600`).
- A 15-second watchdog re-applies the configured values after netd recreates
  interfaces or another service resets sysctls.
- WebUI shows the live values, configured values, daemon health, per-interface
  qdisc, and recent events. It also offers a one-tap reset to `bbr3 + fq`.
- CLI for scripting and diagnostics.

## Install

Package the **contents** of this directory (not the directory itself) as a zip:

```sh
cd bbr3-fq
zip -r ../bbr3_fq-v1.3.zip . -x '*.DS_Store'
```

The zip must contain `module.prop` at its root. Install it in SukiSU Ultra /
KernelSU and reboot. Alternatively, while rooted:

```sh
su -c 'mkdir -p /data/adb/modules/bbr3_fq && cp -a . /data/adb/modules/bbr3_fq/'
su -c 'chown -R root:root /data/adb/modules/bbr3_fq'
# reboot, or run:
su -c 'sh /data/adb/modules/bbr3_fq/service.sh >/dev/null 2>&1 &'
```

The manager adds an **Open** button to the module card when it sees the
`webroot/` directory.

## WebUI

- `当前生效` shows the live kernel values (`/proc/sys/...`).
- Pick a CC and qdisc, then tap **应用配置**. The module writes the config and
  applies it in one step (`bbr3-fq set-pair <cc> <qdisc>`).
- **恢复推荐配置** sets `bbr3 + fq`.
- **立即同步** re-applies the configured value if something changed it
  externally.
- **重启守护** restarts `service.sh` if the watchdog is not running.

Notes:

- Changing `tcp_congestion_control` affects **new** connections; existing
  sockets keep the algorithm they were created with.
- Replacing the root qdisc rebuilds the queue on managed interfaces
  (`wlan*`, `rmnet_data*`, `utun*`, `tun*`); in-flight traffic can see a brief
  jitter. `fq` is recommended because it provides pacing for BBR.
- The UI only offers values the kernel actually supports; the list is read at
  runtime, not hard-coded.

## CLI

The module creates `/data/adb/ksu/bin/bbr3-fq`, which is on root's `PATH`:

```sh
su -c bbr3-fq status              # human-readable health summary
su -c bbr3-fq detail              # sysctl, qdisc, dmesg, log
su -c bbr3-fq set cc cubic        # switch CC and persist
su -c bbr3-fq set qdisc fq_codel  # switch qdisc and persist
su -c bbr3-fq set-pair bbr3 fq    # switch both atomically
su -c bbr3-fq apply               # re-apply the configured values
su -c bbr3-fq restart             # restart the watchdog
su -c bbr3-fq ccs                 # list available CC algorithms
su -c bbr3-fq qdiscs              # list supported qdiscs
su -c bbr3-fq ui-state            # JSON consumed by the WebUI
su -c bbr3-fq reset               # back to bbr3 + fq
```

Configuration:

```sh
cat /data/adb/bbr3_fq.conf
# CC=bbr3
# QDISC=fq
```

Invalid or unavailable values in the config fall back to `bbr3` (or `cubic`)
and `fq`; the watchdog logs the fallback in
`/data/local/tmp/bbr3_fq.log`.

## Files

| File | Purpose |
|---|---|
| `module.prop` | Module metadata |
| `post-fs-data.sh` | Early boot: apply the configured sysctls |
| `service.sh` | 15-second watchdog: enforce CC/qdisc and attach the qdisc to interfaces |
| `common.sh` | Config loading, validation, qdisc probing, apply helpers |
| `bbr3-fq` | CLI and JSON state endpoint |
| `action.sh` | KernelSU action button -> `bbr3-fq status` |
| `webroot/` | Module WebUI (`index.html`, `style.css`, `app.js`) |
| `uninstall.sh` | Stops the watchdog and removes the CLI/config |

## Uninstall

Disable or remove the module in the manager, then reboot. `uninstall.sh` kills
the watchdog and removes `/data/adb/bbr3_fq.conf`, the log, and the
`/data/adb/ksu/bin/bbr3-fq` symlink.

The module does not restore a previous CC/qdisc automatically. To go back
manually:

```sh
su -c 'sysctl -w net.ipv4.tcp_congestion_control=cubic'
su -c 'sysctl -w net.core.default_qdisc=fq'
# or remove the module and reboot; the kernel defaults apply afterwards
```

## License

GPL-2.0, matching the rest of this repository.
