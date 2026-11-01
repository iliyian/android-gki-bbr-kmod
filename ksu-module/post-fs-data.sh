#!/system/bin/sh
# Load tcp_bbr.ko as early as possible so new TCP sockets use BBR from boot.
MODDIR=${0%/*}
LOG=/data/local/tmp/tcp_bbr_kmod.log

if [ -f "$MODDIR/tcp_bbr.ko" ] && ! /system/bin/grep -q '^tcp_bbr ' /proc/modules; then
    /data/adb/ksud insmod "$MODDIR/tcp_bbr.ko" >>"$LOG" 2>&1
fi
