#!/system/bin/sh
for i in 1 2 3 4 5 6 7 8; do
    S=$(dumpsys telephony.registry 2>/dev/null | grep -m1 'mSignalStrength=')
    R=$(echo "$S" | grep -o 'ssRsrp = [-0-9]*' | head -1)
    Q=$(echo "$S" | grep -o 'ssSinr = [-0-9]*' | head -1)
    echo "$R $Q"
    sleep 2
done
