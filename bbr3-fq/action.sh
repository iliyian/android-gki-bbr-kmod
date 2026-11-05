#!/system/bin/sh
# KernelSU module Action button: show health summary and recent log.
MODDIR=/data/adb/modules/bbr3_fq
exec /system/bin/sh "$MODDIR/bbr3-fq" status
