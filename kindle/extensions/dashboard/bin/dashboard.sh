#!/bin/sh
# Wall dashboard loop for a jailbroken Kindle Paperwhite (5th gen).
#  - every minute: copy clock/HHMM.gz into the clock band (no flash)
#  - every 10 minutes: fetch dashboard.gz + meta.txt from the URL in url.txt
#  - every hour: full-flash redraw to clear e-ink ghosting
# Images are raw framebuffer rows (8-bit gray, 768-byte stride), dd'd onto /dev/fb0
# and refreshed with FBInk's GC16 waveform. KOReader's FBInk has no PNG support,
# so if FBInk or gunzip is missing we fall back to eips and the .png files.
# To exit: hold the power button ~20s; the Kindle reboots into its normal UI.
DIR=/mnt/us/extensions/dashboard
STATE=/tmp/dashboard
LOG=$DIR/dashboard.log
CLOCK_Y=72
CLOCK_H=182
STRIDE=768
FETCH_EVERY=600
FULL_EVERY=3600
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

mkdir -p "$STATE"
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ] && mv "$LOG" "$LOG.old"
log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG" 2>/dev/null; }

# Single instance.
if [ -f "$STATE/pid" ] && kill -0 "$(cat "$STATE/pid")" 2>/dev/null; then
	log "already running as $(cat "$STATE/pid")"
	exit 0
fi
echo $$ > "$STATE/pid"
log "starting (pid $$)"

# Work from RAM: /mnt/us disappears whenever the Kindle is plugged into a computer.
rm -rf "$STATE/clock" && cp -r "$DIR/clock" "$STATE/clock"
[ -d "$DIR/test" ] && cp "$DIR"/test/* "$STATE/" 2>/dev/null

FBINK=
if cp "$DIR/bin/fbink" "$STATE/fbink" 2>/dev/null && chmod +x "$STATE/fbink" && "$STATE/fbink" -q -e >/dev/null 2>&1; then
	FBINK="$STATE/fbink"
fi
RAW=
if [ -n "$FBINK" ] && which gunzip >/dev/null 2>&1 && [ -w /dev/fb0 ]; then
	RAW=1
fi
log "drawing with ${RAW:+raw framebuffer + }${FBINK:-eips}"

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

BASE=$(head -n 1 "$DIR/url.txt" 2>/dev/null | tr -d '\r ')
OFFSET=-18000   # US Central Standard; replaced by meta.txt on each fetch
last_fetch=0
last_full=0
fails=0

fetch() {
	[ -n "$BASE" ] || return 1   # no server configured: keep showing the bundled test image
	ext=png
	[ -n "$RAW" ] && ext=gz
	if curl -k -s -f -m 45 -o "$STATE/new.$ext" "$BASE/dashboard.$ext" &&
	   curl -k -s -f -m 20 -o "$STATE/meta.txt" "$BASE/meta.txt"; then
		fails=0
		off=$(grep '^utc_offset=' "$STATE/meta.txt" | cut -d= -f2 | tr -d '\r')
		case "$off" in
			''|*[!0-9-]*) log "bad utc_offset '$off'" ;;
			*) OFFSET=$off ;;
		esac
		if [ -f "$STATE/dashboard.$ext" ] && cmp -s "$STATE/new.$ext" "$STATE/dashboard.$ext"; then
			return 1
		fi
		mv "$STATE/new.$ext" "$STATE/dashboard.$ext"
		return 0
	fi
	fails=$((fails + 1))
	log "fetch failed ($fails in a row)"
	if [ $((fails % 3)) -eq 0 ]; then
		lipc-set-prop com.lab126.cmd wirelessEnable 1 >> "$LOG" 2>&1
	fi
	return 1
}

# blit NAME TOP: put NAME.gz (raw rows) into the framebuffer at row TOP, without refreshing.
blit() {
	gunzip -c "$1.gz" > "$STATE/blit.raw" 2>> "$LOG" &&
	dd if="$STATE/blit.raw" of=/dev/fb0 bs=$STRIDE seek="$2" 2>/dev/null
}

# show NAME TOP HEIGHT [full]: draw NAME.gz (or NAME.png via eips) and refresh that band.
show() {
	if [ -n "$RAW" ]; then
		blit "$1" "$2" || return
		if [ "$4" = full ]; then
			"$FBINK" -q -f -W GC16 -s >> "$LOG" 2>&1
		else
			"$FBINK" -q -W GC16 -s "top=$2,left=0,width=758,height=$3" >> "$LOG" 2>&1
		fi
	elif [ "$4" = full ]; then
		eips -f -g "$1.png" -x 0 -y "$2" >> "$LOG" 2>&1
	else
		eips -g "$1.png" -x 0 -y "$2" >> "$LOG" 2>&1
	fi
}

clock_sprite() {
	local_s=$(($(date -u +%s) + OFFSET))
	sod=$((local_s % 86400))
	h=$((sod / 3600 % 12))
	[ "$h" -eq 0 ] && h=12
	m=$((sod % 3600 / 60))
	printf '%s/clock/%02d%02d' "$STATE" "$h" "$m"
}

while :; do
	now=$(date -u +%s)
	redraw=
	if [ $((now - last_fetch)) -ge $FETCH_EVERY ]; then
		last_fetch=$now
		fetch && redraw=partial
	fi
	if [ $((now - last_full)) -ge $FULL_EVERY ]; then
		redraw=full
	fi
	if [ -n "$redraw" ] && [ -f "$STATE/dashboard.gz" -o -f "$STATE/dashboard.png" ]; then
		if [ -n "$RAW" ]; then
			# Compose background + clock in the framebuffer, then refresh once.
			blit "$STATE/dashboard" 0 && blit "$(clock_sprite)" $CLOCK_Y
			if [ "$redraw" = full ]; then
				"$FBINK" -q -f -W GC16 -s >> "$LOG" 2>&1
			else
				"$FBINK" -q -W GC16 -s >> "$LOG" 2>&1
			fi
		else
			show "$STATE/dashboard" 0 1024 $redraw
			show "$(clock_sprite)" $CLOCK_Y $CLOCK_H
		fi
		[ "$redraw" = full ] && last_full=$now
	else
		show "$(clock_sprite)" $CLOCK_Y $CLOCK_H
	fi
	now=$(date -u +%s)
	sleep $((60 - now % 60 + 1))
done
