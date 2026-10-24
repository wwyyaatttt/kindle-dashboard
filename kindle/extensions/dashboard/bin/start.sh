#!/bin/sh
# KUAL entry point. The dashboard stops the Kindle UI (and KUAL with it),
# so run it detached from KUAL's process.
DIR=/mnt/us/extensions/dashboard
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
if which setsid >/dev/null 2>&1; then
	setsid sh "$DIR/bin/dashboard.sh" >/dev/null 2>&1 < /dev/null &
else
	nohup sh "$DIR/bin/dashboard.sh" >/dev/null 2>&1 < /dev/null &
fi
