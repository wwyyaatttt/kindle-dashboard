#!/bin/sh
# Collects what the dashboard needs to know about this Kindle: tools, screen, network, TLS.
OUT=/mnt/us/extensions/dashboard/diag.txt
exec > "$OUT" 2>&1

section() { echo; echo "=== $1"; }

section "basics"
date; date -u; uname -a; cat /etc/prettyversion.txt 2>/dev/null; id
section "tools"
for t in curl wget openssl eips fbink lipc-get-prop lipc-set-prop ntpdate rdate busybox python python3 sntp; do
	printf "%-14s %s\n" "$t" "$(command -v $t || echo -)"
done
ls /mnt/us/extensions /mnt/us/usbnet /mnt/us/python* 2>/dev/null
section "curl -V"
curl -V
section "openssl"
openssl version 2>&1
section "screen"
eips -i 2>&1 | head -30
cat /sys/class/graphics/fb0/virtual_size /sys/class/graphics/fb0/bits_per_pixel 2>/dev/null
section "power/wifi"
lipc-get-prop com.lab126.powerd status 2>&1 | head -20
lipc-get-prop com.lab126.cmd wirelessEnable 2>&1
lipc-get-prop com.lab126.wifid cmState 2>&1
ifconfig wlan0 2>&1 | head -3
cat /etc/resolv.conf 2>&1
section "fetch tests"
for u in http://neverssl.com/ https://example.com/ https://api.weather.gov/ https://wwyyaatttt.github.io/ https://raw.githubusercontent.com/ https://calendar.google.com/ ; do
	r=$(curl -s -o /dev/null -m 20 -w "%{http_code} %{ssl_verify_result}" "$u" 2>&1); e=$?
	rk=$(curl -k -s -o /dev/null -m 20 -w "%{http_code}" "$u" 2>&1); ek=$?
	echo "$u  verify: exit=$e code=$r   insecure(-k): exit=$ek code=$rk"
done
section "verbose tls to github.io"
curl -k -v -s -o /dev/null -m 20 https://wwyyaatttt.github.io/ 2>&1 | grep -iE 'SSL|TLS|cipher|error|subject|issuer|Connected' | head -20
section "http date header"
curl -s -I -m 20 http://neverssl.com/ | grep -i '^date'
section "done"
date
