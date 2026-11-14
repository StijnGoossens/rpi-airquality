#!/bin/sh
# Restart the Zigbee receiver if it isn't running.
# Same self-match trap as dashboard_watchdog.sh: keep the pgrep pattern in this
# file (not inline in the crontab) and match the script path.

# Only one run at a time -- see dashboard_watchdog.sh; two receivers would store
# every reading twice.
exec 9> "$HOME/.zigbee_watchdog.lock"
flock -n 9 || exit 0

if ! pgrep -f "rpi-airquality/src/zigbee_receiver.py" > /dev/null; then
    echo "$(date): zigbee receiver not running, restarting"
    # 9>&- so the child does not inherit and hold the lock.
    nohup "$HOME/venvs/airquality/bin/python" \
        "$HOME/Documents/rpi-airquality/src/zigbee_receiver.py" \
        >> "$HOME/cronjoblog-zigbee" 2>&1 9>&- &
fi
