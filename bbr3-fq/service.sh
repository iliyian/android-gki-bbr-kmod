#!/system/bin/sh
# Late-start: keep the configured CC + qdisc across network/interface changes.
# fq gives BBR qdisc-based pacing; without it the kernel falls back to
# per-burst hrtimer pacing. The user can change both values from the module
# WebUI; the watchdog enforces whatever /data/adb/bbr3_fq.conf says.

MODDIR=/data/adb/modules/bbr3_fq
. "$MODDIR/common.sh"

echo $$ >"$MODDIR/service.pid"
trap 'rm -f "$MODDIR/service.pid"' EXIT

# Wait for boot completion (max ~120s), then start enforcing.
i=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ "$i" -lt 120 ]; do
    /system/bin/sleep 1
    i=$((i + 1))
done

load_config
ensure_sysctls
apply_qdisc
log "service started: cc=$(/system/bin/cat /proc/sys/net/ipv4/tcp_congestion_control 2>/dev/null) default_qdisc=$(/system/bin/cat /proc/sys/net/core/default_qdisc 2>/dev/null)"

n=0
while true; do
    /system/bin/sleep 15
    n=$((n + 1))
    rotate_log
    ensure_sysctls
    apply_qdisc

    # Heartbeat every 5 minutes so `bbr3-fq status` can confirm liveness.
    if [ $((n % 20)) -eq 0 ]; then
        load_config
        log "heartbeat: cc=$(/system/bin/cat /proc/sys/net/ipv4/tcp_congestion_control 2>/dev/null) qdisc=$(/system/bin/cat /proc/sys/net/core/default_qdisc 2>/dev/null) configured=$CFG_CC/$CFG_QDISC"
    fi
done
