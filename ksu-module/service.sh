#!/system/bin/sh
# Late-start: ensure kmod loaded, BBR default, and fq qdisc on network devices.
MODDIR=${0%/*}
LOG=/data/local/tmp/tcp_bbr_kmod.log
TC=/system/bin/tc

i=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ "$i" -lt 120 ]; do
    /system/bin/sleep 1
    i=$((i + 1))
done

if [ -f "$MODDIR/tcp_bbr.ko" ] && ! /system/bin/grep -q '^tcp_bbr ' /proc/modules; then
    /data/adb/ksud insmod "$MODDIR/tcp_bbr.ko" >>"$LOG" 2>&1
fi

if /system/bin/grep -q '^tcp_bbr ' /proc/modules; then
    /system/bin/sysctl -w net.ipv4.tcp_congestion_control=bbr >>"$LOG" 2>&1
fi
/system/bin/sysctl -w net.core.default_qdisc=fq >>"$LOG" 2>&1

apply_fq() {
    for sysdev in /sys/class/net/wlan* /sys/class/net/rmnet_data* /sys/class/net/rmnet_ipa*; do
        [ -e "$sysdev" ] || continue
        dev=${sysdev##*/}
        case "$($TC qdisc show dev "$dev" 2>/dev/null)" in
            "qdisc fq "*) continue ;;
        esac
        $TC qdisc replace dev "$dev" root fq 2>>"$LOG"
    done
}

apply_fq

# netd may recreate devices/qdiscs on network changes; keep fq attached.
(
    while true; do
        /system/bin/sleep 30
        apply_fq
    done
) >>"$LOG" 2>&1 &
