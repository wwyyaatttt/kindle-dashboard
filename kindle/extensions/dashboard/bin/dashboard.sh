#!/bin/sh
# Wall dashboard loop for a jailbroken Kindle Paperwhite (5th gen, firmware 5.6.1.1, BusyBox 1.17 ash).
#  - every minute: copy clock/HHMM.gz into the clock band and refresh it (GC16, no flash)
#  - every 5 minutes (2 while failing): fetch the encrypted dashboard.bin + meta.bin
#  - hourly: full-flash redraw to clear e-ink ghosting
#  - footer banner when the data is stale, the battery is low, or nothing has arrived yet
# Images are raw framebuffer rows (8-bit gray, 768-byte stride), dd'd onto /dev/fb0.
# Press the power button to exit back to the normal Kindle UI.
#
# Everything runs from /tmp/dashboard: /mnt/us disappears while the Kindle is plugged into a computer.
DIR=${DASH_DIR:-/mnt/us/extensions/dashboard}   # overridable for testing off-device
STATE=${DASH_STATE:-/tmp/dashboard}
FB=${DASH_FB:-/dev/fb0}
LOG=$STATE/dashboard.log
STRIDE=768
FRAME_BYTES=786432          # 1024 rows * 768
CLOCK_Y=72
CLOCK_H=182
BANNER_Y=974
BANNER_H=50
FETCH_EVERY=300
RETRY_EVERY=120
STALE_AFTER=2700            # 45 minutes without a new render
FULL_EVERY=3600
LOW_BATTERY=15
export PATH=${DASH_PATH_PREFIX:+$DASH_PATH_PREFIX:}/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

mkdir -p "$STATE"
cd /
log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
have() {   # BusyBox 1.17 has no 'command -v', and 'which' may be missing too
	for d in $(echo "$PATH" | tr ':' ' '); do [ -x "$d/$1" ] && return 0; done
	return 1
}
rotate_log() {
	[ -f "$LOG" ] && [ "$(($(wc -c < "$LOG")))" -gt 200000 ] && mv "$LOG" "$LOG.old"
	[ -d "$DIR/bin" ] && cp "$LOG" "$DIR/dashboard.log" 2>/dev/null
}

# Single instance (/tmp is tmpfs, so a pid file never survives a reboot).
if [ -f "$STATE/pid" ] && kill -0 "$(cat "$STATE/pid")" 2>/dev/null; then
	log "already running as $(cat "$STATE/pid")"
	exit 0
fi
echo $$ > "$STATE/pid"
rotate_log
log "starting (pid $$)"

# ---- preflight: check everything before touching the UI ----
fail_preflight() {
	log "cannot start: $1"
	eips 0 3 "Dashboard: $1" >/dev/null 2>&1
	rm -f "$STATE/pid"
	rotate_log
	exit 1
}
cp "$DIR/bin/fbink" "$STATE/fbink" 2>/dev/null && chmod +x "$STATE/fbink" && "$STATE/fbink" -q -e >/dev/null 2>&1 \
	|| fail_preflight "fbink missing or broken"
FBINK=$STATE/fbink
for tool in gunzip openssl curl dd lipc-set-prop; do
	have "$tool" || fail_preflight "$tool not found"
done
[ -w "$FB" ] || fail_preflight "framebuffer not writable"
rm -rf "$STATE/clock" "$STATE/banner"
mkdir -p "$STATE/clock" "$STATE/banner"
cp "$DIR"/clock/*.gz "$STATE/clock/" && cp "$DIR"/banner/*.gz "$STATE/banner/" || fail_preflight "clock or banner files missing"
# url.txt and key.txt are re-read on every fetch, so a rotated key or path applies without a restart.
# The key is reduced to its hex digits so a Windows-edited key.txt (CRLF, BOM) still works.
load_config() {   # skipped while /mnt/us is USB-mounted (keeps the last values)
	[ -f "$DIR/url.txt" ] || return 0
	BASE=$(head -n 1 "$DIR/url.txt" 2>/dev/null | tr -d '\r ')
	head -n 1 "$DIR/key.txt" 2>/dev/null | tr -cd '0-9a-fA-F' > "$STATE/key.txt"
	if [ -z "$BASE" ] || [ ! -s "$STATE/key.txt" ]; then BASE=; fi
	return 0
}
BASE=
load_config
[ -n "$BASE" ] || log "url.txt or key.txt missing: showing the clock only"

# ---- time ----
days_from_civil() {   # y m d -> days since 1970-01-01 (proleptic Gregorian)
	y=$1; m=$2; d=$3
	[ "$m" -le 2 ] && y=$((y - 1))
	era=$((y / 400)); yoe=$((y - era * 400))
	if [ "$m" -gt 2 ]; then mp=$((m - 3)); else mp=$((m + 9)); fi
	doy=$(( (153 * mp + 2) / 5 + d - 1 ))
	echo $(( era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468 ))
}

# US Central offset for the current UTC year: CDT from the 2nd Sunday of March 08:00Z
# to the 1st Sunday of November 07:00Z. Independent of the server, so DST is never late.
OFFSET_YEAR=
central_offset() {
	y=$(date -u +%Y)
	if [ "$y" != "$OFFSET_YEAR" ]; then
		first=$(days_from_civil "$y" 3 1)
		DST_START=$(( (first + (7 - (first + 4) % 7) % 7 + 7) * 86400 + 28800 ))
		first=$(days_from_civil "$y" 11 1)
		DST_END=$(( (first + (7 - (first + 4) % 7) % 7) * 86400 + 25200 ))
		OFFSET_YEAR=$y
	fi
	if [ "$1" -ge "$DST_START" ] && [ "$1" -lt "$DST_END" ]; then OFFSET=-18000; else OFFSET=-21600; fi
}

month_number() {
	case $1 in
		Jan) echo 1;; Feb) echo 2;; Mar) echo 3;; Apr) echo 4;; May) echo 5;; Jun) echo 6;;
		Jul) echo 7;; Aug) echo 8;; Sep) echo 9;; Oct) echo 10;; Nov) echo 11;; Dec) echo 12;;
	esac
}

# Correct the device clock from the HTTP Date header of a successful, decryptable fetch.
# The header is only trusted within 6 hours of the (authenticated) render time.
sync_clock() {
	set -- $(grep -i '^date:' "$STATE/hdr" | tail -n 1 | tr -d '\r')   # Date: Sat, 24 Oct 2026 22:43:30 GMT
	[ $# -ge 6 ] || return
	# Validate every field before any arithmetic: an ash arithmetic error would kill the loop.
	m=$(month_number "$4"); [ -n "$m" ] || return
	case "$3" in [1-9]|[0-3][0-9]) ;; *) return;; esac
	case "$5" in 20[0-9][0-9]) ;; *) return;; esac
	case "$6" in [0-2][0-9]:[0-5][0-9]:[0-6][0-9]) ;; *) return;; esac
	t=$6
	hh=${t%%:*}; rest=${t#*:}; mm=${rest%%:*}; ss=${rest#*:}
	day=${3#0}
	server=$(( $(days_from_civil "$5" "$m" "$day") * 86400 + (1$hh - 100) * 3600 + (1$mm - 100) * 60 + (1$ss - 100) ))
	# Renders are minutes apart; a Date far from the render time is a stale cache or a forgery.
	[ $((server - GEN)) -gt -300 ] && [ $((server - GEN)) -lt 7200 ] || return
	skew=$((server - $(date -u +%s)))
	if [ "$skew" -gt 30 ] || [ "$skew" -lt -30 ]; then
		if date -u -s "$(printf '%04d-%02d-%02d %s' "$5" "$m" "$day" "$t")" >/dev/null 2>&1; then
			hwclock -u -w >/dev/null 2>&1
			log "clock corrected by ${skew}s"
			batt_checked=0; last_full=0
		fi
	fi
}

# ---- drawing ----
blit() {   # blit FILE.gz ROW: copy raw rows into the framebuffer without refreshing
	gunzip -c "$1" > "$STATE/blit.raw" 2>/dev/null &&
	dd if="$STATE/blit.raw" of="$FB" conv=notrunc bs=$STRIDE seek="$2" 2>/dev/null
}
refresh() {   # refresh full|partial|TOP HEIGHT
	case "$1" in
		full) "$FBINK" -q -f -W GC16 -s >> "$LOG" 2>&1 ;;
		partial) "$FBINK" -q -W GC16 -s >> "$LOG" 2>&1 ;;
		*) "$FBINK" -q -W GC16 -s "top=$1,left=0,width=758,height=$2" >> "$LOG" 2>&1 ;;
	esac
}
clock_file() {   # 12-hour local time as clock/HHMM.gz
	sod=$(( ($1 + OFFSET) % 86400 ))
	h=$((sod / 3600 % 12)); [ "$h" -eq 0 ] && h=12
	printf '%s/clock/%02d%02d.gz' "$STATE" "$h" $((sod % 3600 / 60))
}
draw_footer() {   # banner, or the dashboard's own footer rows when there's nothing to say
	if [ -n "$BANNER" ]; then
		blit "$STATE/banner/$BANNER.gz" $BANNER_Y
	elif [ -f "$STATE/dashboard.raw" ]; then
		dd if="$STATE/dashboard.raw" of="$FB" conv=notrunc bs=$STRIDE skip=$BANNER_Y seek=$BANNER_Y count=$BANNER_H 2>/dev/null
	else
		blit "$STATE/banner/blank.gz" $BANNER_Y
	fi
}
compose() {   # compose NOW full|partial: whole screen in the framebuffer, then one refresh
	if [ -f "$STATE/dashboard.raw" ]; then
		dd if="$STATE/dashboard.raw" of="$FB" conv=notrunc bs=$STRIDE 2>/dev/null
	else
		"$FBINK" -q -k -b >> "$LOG" 2>&1
	fi
	blit "$(clock_file "$1")" $CLOCK_Y
	draw_footer
	refresh "$2"
}

# ---- data ----
decrypt() { openssl enc -d -aes-256-cbc -md sha256 -pass "file:$STATE/key.txt" -in "$1" -out "$2" 2>/dev/null; }

load_meta() {   # load_meta FILE: sets GEN from a validated generated= line
	g=$(sed -n 's/^generated=\([0-9]\{9,10\}\)$/\1/p' "$1" | head -n 1)
	[ -n "$g" ] && GEN=$g
}

fails=0
net_fails=0
fetch_failed() {   # fetch_failed REASON [net]: only network failures count toward a Wi-Fi restart
	fails=$((fails + 1))
	log "fetch failed ($fails in a row): $1"
	[ "$2" = net ] || return 1
	net_fails=$((net_fails + 1))
	if [ $((net_fails % 3)) -eq 0 ]; then
		log "restarting Wi-Fi"
		lipc-set-prop com.lab126.cmd wirelessEnable 0 >> "$LOG" 2>&1
		sleep 3
		lipc-set-prop com.lab126.cmd wirelessEnable 1 >> "$LOG" 2>&1
	fi
	return 1
}

# fetch: returns 0 when a new, valid dashboard replaced the old one.
curl_failed() {   # curl exit 22 is an HTTP error (the server answered); anything else is the network
	if [ "$2" -eq 22 ]; then fetch_failed "$1: HTTP error"; else fetch_failed "$1: curl exit $2" net; fi
}
fetch() {
	load_config
	[ -n "$BASE" ] || return 1
	curl -k -L --max-redirs 3 -s -f --connect-timeout 10 -m 30 -o "$STATE/new.bin" "$BASE/dashboard.bin" \
		|| { curl_failed dashboard $?; return 1; }
	curl -k -L --max-redirs 3 -s -f --connect-timeout 10 -m 15 -D "$STATE/hdr" -o "$STATE/meta.bin" "$BASE/meta.bin" \
		|| { curl_failed meta $?; return 1; }
	net_fails=0
	decrypt "$STATE/meta.bin" "$STATE/meta.new" || { fetch_failed "meta.bin did not decrypt (wrong key?)"; return 1; }
	old_gen=$GEN
	load_meta "$STATE/meta.new" || { fetch_failed "meta has no generated="; return 1; }
	if [ "$GEN" -lt "$old_gen" ]; then   # never go back to an older render (replayed files)
		GEN=$old_gen; fetch_failed "render is older than the one on screen"; return 1
	fi
	decrypt "$STATE/new.bin" "$STATE/new.gz" && gunzip -c "$STATE/new.gz" > "$STATE/new.raw" 2>/dev/null \
		|| { GEN=$old_gen; fetch_failed "dashboard.bin did not decrypt/unzip"; return 1; }
	if [ "$(($(wc -c < "$STATE/new.raw")))" -ne $FRAME_BYTES ]; then
		GEN=$old_gen; fetch_failed "dashboard is $(($(wc -c < "$STATE/new.raw"))) bytes, expected $FRAME_BYTES"; return 1
	fi
	fails=0
	sync_clock
	if [ -d "$DIR/bin" ]; then   # keep the last good render for the next start (skipped while USB-mounted)
		mkdir -p "$DIR/cache" && cp "$STATE/new.gz" "$DIR/cache/dashboard.gz" && cp "$STATE/meta.new" "$DIR/cache/meta.txt"
	fi
	if [ -f "$STATE/dashboard.raw" ] && cmp -s "$STATE/new.raw" "$STATE/dashboard.raw"; then
		return 1
	fi
	mv "$STATE/new.raw" "$STATE/dashboard.raw"
	return 0
}

BATT_LOW=
check_battery() {
	level=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
	charging=$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)
	case "$level$charging" in ''|*[!0-9]*) BATT_LOW=; return;; esac
	if [ "$level" -le $LOW_BATTERY ] && [ "$charging" -eq 0 ]; then BATT_LOW=1; else BATT_LOW=; fi
}

pick_banner() {
	stale=
	if [ -n "$BASE" ] && [ "$GEN" -gt 0 ]; then
		[ $(($1 - GEN)) -gt $STALE_AFTER ] && stale=1
		# a render from an earlier local day (yesterday's date and TODAY list), 15 min after midnight
		[ $(( ($1 + OFFSET) / 86400 )) -ne $(( (GEN + OFFSET) / 86400 )) ] && [ $(( ($1 + OFFSET) % 86400 )) -gt 900 ] && stale=1
	fi
	if [ -n "$BASE" ] && [ "$GEN" -eq 0 ]; then BANNER=nodata
	elif [ -n "$stale" ] && [ -n "$BATT_LOW" ]; then BANNER=stale_battery
	elif [ -n "$stale" ]; then BANNER=stale
	elif [ -n "$BATT_LOW" ]; then BANNER=battery
	else BANNER=
	fi
}

# ---- power button: exit to the normal Kindle UI ----
start_power_watch() {
	old=$(cat "$STATE/watch.pid" 2>/dev/null)   # only if that pid is still our lipc-wait-event
	if [ -n "$old" ] && grep -q lipc-wait-event "/proc/$old/cmdline" 2>/dev/null; then kill "$old" 2>/dev/null; fi
	rm -f "$STATE/power.fifo"
	if ! have lipc-wait-event || ! mkfifo "$STATE/power.fifo" 2>/dev/null; then
		log "power button exit unavailable"; return
	fi
	lipc-wait-event -m com.lab126.powerd goingToScreenSaver > "$STATE/power.fifo" 2>/dev/null &
	echo $! > "$STATE/watch.pid"
	(
		read -r _ < "$STATE/power.fifo" || exit 0
		log "power button pressed: exiting to the Kindle UI"
		lipc-set-prop com.lab126.powerd preventScreenSaver 0 >> "$LOG" 2>&1
		lipc-set-prop com.lab126.cmd wirelessEnable 1 >> "$LOG" 2>&1   # in case a Wi-Fi restart was mid-way
		kill "$MAIN_PID" 2>/dev/null
		sleep 1
		rm -f "$STATE/pid"
		kill "$(cat "$STATE/watch.pid")" 2>/dev/null
		rotate_log
		start lab126_gui >> "$LOG" 2>&1
	) &
}

# ---- start ----
lipc-set-prop com.lab126.powerd preventScreenSaver 1 >> "$LOG" 2>&1
# Stop the Kindle UI, status bar included. Same approach as KOReader's launcher:
# on upstart firmware the job SIGTERMs everything it can, so ignore TERM meanwhile.
if [ -d /etc/upstart ]; then
	trap "" TERM
	stop lab126_gui >> "$LOG" 2>&1
	sleep 2
	trap - TERM
else
	/etc/init.d/framework stop >> "$LOG" 2>&1
fi
sleep 1
MAIN_PID=$$
SLEEP_PID=
trap '[ -n "$SLEEP_PID" ] && kill "$SLEEP_PID" 2>/dev/null; rm -f "$STATE/pid"; exit 0' TERM INT
start_power_watch

GEN=0
if [ -n "$BASE" ] && [ -f "$DIR/cache/dashboard.gz" ] && [ -f "$DIR/cache/meta.txt" ]; then
	gunzip -c "$DIR/cache/dashboard.gz" > "$STATE/dashboard.raw" 2>/dev/null
	if [ "$(($(wc -c < "$STATE/dashboard.raw")))" -eq $FRAME_BYTES ]; then
		load_meta "$DIR/cache/meta.txt"
		log "showing cached dashboard rendered at $GEN"
	else
		rm -f "$STATE/dashboard.raw"
	fi
fi

check_battery
batt_checked=$(date -u +%s)
next_fetch=0
last_full=0
drawn_min=-1
shown_banner=-
redraw=full

while :; do
	now=$(date -u +%s)
	central_offset "$now"
	if [ $((now - batt_checked)) -ge 300 ] || [ $((now - batt_checked)) -lt 0 ]; then
		check_battery; batt_checked=$now
		lipc-set-prop com.lab126.powerd preventScreenSaver 1 >/dev/null 2>&1
	fi
	pick_banner "$now"
	if [ $((now - last_full)) -ge $FULL_EVERY ] || [ $((now - last_full)) -lt 0 ]; then redraw=full; fi

	if [ -n "$redraw" ]; then
		compose "$now" $redraw
		[ "$redraw" = full ] && { last_full=$now; rotate_log; }
		redraw=; drawn_min=$((now / 60)); shown_banner=$BANNER
	else
		if [ $((now / 60)) -ne "$drawn_min" ]; then
			blit "$(clock_file "$now")" $CLOCK_Y && refresh $CLOCK_Y $CLOCK_H
			drawn_min=$((now / 60))
		fi
		if [ "$BANNER" != "$shown_banner" ]; then
			draw_footer && refresh $BANNER_Y $BANNER_H
			shown_banner=$BANNER
		fi
	fi

	if [ -n "$BASE" ] && { [ "$now" -ge "$next_fetch" ] || [ $((next_fetch - now)) -gt $FETCH_EVERY ]; }; then
		if fetch; then
			redraw=partial
		fi
		if [ "$fails" -eq 0 ]; then next_fetch=$(($(date -u +%s) + FETCH_EVERY)); else next_fetch=$(($(date -u +%s) + RETRY_EVERY)); fi
		continue   # draw right away: a new image, or a minute that ticked over during the fetch
	fi

	s=$((61 - $(date -u +%s) % 60))
	[ "$s" -gt 60 ] && s=1
	sleep "$s" &   # backgrounded so the TERM trap runs at once instead of after the sleep
	SLEEP_PID=$!
	wait "$SLEEP_PID"
	SLEEP_PID=
done
