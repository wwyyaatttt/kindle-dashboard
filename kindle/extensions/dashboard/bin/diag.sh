#!/bin/sh
# Collects what the dashboard needs to know about this Kindle: tools, screen, power, network, TLS, decryption.
# Written for BusyBox 1.17 ash: no 'command -v', no 'grep -E'.
DIR=/mnt/us/extensions/dashboard
OUT=$DIR/diag.txt
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
exec > "$OUT" 2>&1

section() { echo; echo "=== $1"; }
have() {
	for d in $(echo "$PATH" | tr ':' ' '); do [ -x "$d/$1" ] && { echo "$d/$1"; return 0; }; done
	return 1
}

section "basics"
date; date -u; uname -a; cat /etc/prettyversion.txt 2>/dev/null; id
section "tools"
for t in curl openssl gunzip dd cmp mkfifo setsid nohup which hwclock lipc-get-prop lipc-set-prop lipc-wait-event eips; do
	printf "%-16s %s\n" "$t" "$(have "$t" || echo -)"
done
section "curl -V"
curl -V
section "openssl"
openssl version 2>&1
echo "kindle-dashboard" > /tmp/diag.plain
echo "diag-test-key" > /tmp/diag.key
openssl enc -aes-256-cbc -md sha256 -salt -pass file:/tmp/diag.key -in /tmp/diag.plain -out /tmp/diag.enc 2>/dev/null &&
openssl enc -d -aes-256-cbc -md sha256 -pass file:/tmp/diag.key -in /tmp/diag.enc -out /tmp/diag.dec 2>/dev/null &&
cmp /tmp/diag.plain /tmp/diag.dec && echo "aes-256-cbc -md sha256 round trip: ok"
rm -f /tmp/diag.plain /tmp/diag.key /tmp/diag.enc /tmp/diag.dec
section "date parsing"
date -u -d "2026-10-24 22:43:30" +%s 2>&1 | head -1
section "screen"
eips -i 2>&1 | head -30
section "power"
lipc-get-prop com.lab126.powerd status 2>&1 | head -20
for p in state preventScreenSaver battLevel isCharging; do
	printf "%-20s %s\n" "$p" "$(lipc-get-prop com.lab126.powerd $p 2>&1)"
done
section "wifi"
lipc-get-prop com.lab126.cmd wirelessEnable 2>&1
lipc-get-prop com.lab126.wifid cmState 2>&1
ifconfig wlan0 2>&1 | head -3
section "fetch tests"
for u in https://example.com/ https://api.weather.gov/ https://wwyyaatttt.github.io/; do
	rk=$(curl -k -L -s -o /dev/null -m 20 -w "%{http_code}" "$u" 2>&1); ek=$?
	echo "$u  exit=$ek code=$rk"
done
section "dashboard state"
ls -la /tmp/dashboard 2>&1 | head -30
tail -n 30 /tmp/dashboard/dashboard.log 2>&1
section "done"
date
