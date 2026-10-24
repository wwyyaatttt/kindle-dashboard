#!/bin/sh
# Wall dashboard loop for a jailbroken Kindle Paperwhite (5th gen).
#  - every minute: blit clock/HHMM.png into the clock band (no flash)
#  - every 10 minutes: fetch dashboard.png + meta.txt from the URL in url.txt
#  - every hour: full-flash redraw to clear e-ink ghosting
# To exit: hold the power button ~20s; the Kindle reboots into its normal UI.
DIR=/mnt/us/extensions/dashboard
STATE=/tmp/dashboard
LOG=$DIR/dashboard.log
CLOCK_Y=72
FETCH_EVERY=600
FULL_EVERY=3600
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

mkdir -p "$STATE"
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ] && mv "$LOG" "$LOG.old"
log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

# Single instance.
if [ -f "$STATE/pid" ] && kill -0 "$(cat "$STATE/pid")" 2>/dev/null; then
	log "already running as $(cat "$STATE/pid")"
	exit 0
fi
echo $$ > "$STATE/pid"
log "starting (pid $$)"

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
sleep 2

# FBInk (from KOReader) gives clean, flash-free GC16 updates; eips is the fallback.
FBINK=
if cp "$DIR/bin/fbink" "$STATE/fbink" 2>/dev/null && chmod +x "$STATE/fbink" && "$STATE/fbink" -q -e >/dev/null 2>&1; then
	FBINK="$STATE/fbink"
fi
log "drawing with ${FBINK:-eips}"

# Work from RAM: /mnt/us disappears whenever the Kindle is plugged into a computer.
rm -rf "$STATE/clock" && cp -r "$DIR/clock" "$STATE/clock"

BASE=$(head -n 1 "$DIR/url.txt" 2>/dev/null | tr -d '\r ')
OFFSET=-18000   # US Central Standard; replaced by meta.txt on each fetch
[ -f "$STATE/offset" ] && OFFSET=$(cat "$STATE/offset")
last_fetch=0
last_full=0
fails=0

fetch() {
	if [ -z "$BASE" ]; then
		# No server configured: use the bundled test image so the display can be checked.
		[ -f "$STATE/dashboard.png" ] || cp "$DIR/test/dashboard.png" "$STATE/dashboard.png"
		return 1
	fi
	if curl -k -s -f -m 45 -o "$STATE/new.png" "$BASE/dashboard.png" &&
	   curl -k -s -f -m 20 -o "$STATE/meta.txt" "$BASE/meta.txt"; then
		fails=0
		off=$(grep '^utc_offset=' "$STATE/meta.txt" | cut -d= -f2 | tr -d '\r')
		case "$off" in
			''|*[!0-9-]*) log "bad utc_offset '$off'" ;;
			*) OFFSET=$off; echo "$OFFSET" > "$STATE/offset" ;;
		esac
		if [ -f "$STATE/dashboard.png" ] && cmp -s "$STATE/new.png" "$STATE/dashboard.png"; then
			return 1
		fi
		mv "$STATE/new.png" "$STATE/dashboard.png"
		return 0
	fi
	fails=$((fails + 1))
	log "fetch failed ($fails in a row)"
	if [ $((fails % 3)) -eq 0 ]; then
		lipc-set-prop com.lab126.cmd wirelessEnable 1 >> "$LOG" 2>&1
	fi
	return 1
}

# draw IMAGE X Y [full]
draw() {
	if [ -n "$FBINK" ]; then
		if [ "$4" = full ]; then
			"$FBINK" -q -f -W GC16 -g "file=$1,x=$2,y=$3" >> "$LOG" 2>&1
		else
			"$FBINK" -q -W GC16 -g "file=$1,x=$2,y=$3" >> "$LOG" 2>&1
		fi
	elif [ "$4" = full ]; then
		eips -f -g "$1" -x "$2" -y "$3" >> "$LOG" 2>&1
	else
		eips -g "$1" -x "$2" -y "$3" >> "$LOG" 2>&1
	fi
}

draw_clock() {
	local_s=$(($(date -u +%s) + OFFSET))
	sod=$((local_s % 86400))
	h=$((sod / 3600 % 12))
	[ "$h" -eq 0 ] && h=12
	m=$((sod % 3600 / 60))
	draw "$STATE/clock/$(printf '%02d%02d' "$h" "$m").png" 0 $CLOCK_Y
}

while :; do
	now=$(date -u +%s)
	redraw=
	if [ $((now - last_fetch)) -ge $FETCH_EVERY ]; then
		last_fetch=$now
		fetch && redraw=partial
		[ $last_full -eq 0 ] && redraw=full
	fi
	if [ $((now - last_full)) -ge $FULL_EVERY ]; then
		redraw=full
	fi
	if [ -n "$redraw" ] && [ -f "$STATE/dashboard.png" ]; then
		draw "$STATE/dashboard.png" 0 0 $redraw
		[ "$redraw" = full ] && last_full=$now
	fi
	draw_clock
	now=$(date -u +%s)
	sleep $((60 - now % 60 + 1))
done
