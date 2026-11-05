#!/system/bin/sh
# Stop the background enforcement loop and remove the CLI symlink/config.
MODDIR=/data/adb/modules/bbr3_fq
[ -f "$MODDIR/service.pid" ] && kill "$(cat "$MODDIR/service.pid")" 2>/dev/null
rm -f "$MODDIR/service.pid"
rm -f /data/adb/ksu/bin/bbr3-fq
rm -f /data/adb/bbr3_fq.conf /data/local/tmp/bbr3_fq.log /data/local/tmp/bbr3_fq.log.old
exit 0
