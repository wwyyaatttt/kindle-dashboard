#!/bin/sh
# KUAL entry point. The dashboard stops the Kindle UI (and KUAL with it), so run it
# detached, from a copy in /tmp, with no working directory or open files on /mnt/us.
DIR=/mnt/us/extensions/dashboard
STATE=/tmp/dashboard
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
mkdir -p "$STATE" && cp "$DIR/bin/dashboard.sh" "$STATE/dashboard.sh" || exit 1
cd /
if [ -x /usr/bin/setsid ] || [ -x /bin/setsid ] || [ -x /usr/sbin/setsid ] || [ -x /sbin/setsid ]; then
	setsid sh "$STATE/dashboard.sh" >/dev/null 2>&1 < /dev/null &
else
	nohup sh "$STATE/dashboard.sh" >/dev/null 2>&1 < /dev/null &
fi
