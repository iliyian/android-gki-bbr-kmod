#!/system/bin/sh
# Early boot: set bbr3 + fq as soon as /data and procfs are available.
# Interfaces are handled by service.sh once the network comes up.

MODDIR=/data/adb/modules/bbr3_fq
. "$MODDIR/common.sh"

ensure_sysctls
log "post-fs-data: cc=$(/system/bin/cat /proc/sys/net/ipv4/tcp_congestion_control 2>/dev/null) qdisc=$(/system/bin/cat /proc/sys/net/core/default_qdisc 2>/dev/null)"
