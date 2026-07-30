#!/bin/sh
# Restart the air quality monitor if it isn't running.
# Same self-match trap as dashboard_watchdog.sh: keep the pgrep pattern in this
# file (not inline in the crontab) and match the script path, not "monitor.py".
if ! pgrep -f "rpi-airquality/src/monitor.py" > /dev/null; then
    echo "$(date): monitor not running, restarting"
    nohup "$HOME/venvs/airquality/bin/python" \
        "$HOME/Documents/rpi-airquality/src/monitor.py" \
        >> "$HOME/cronjoblog-monitor" 2>&1 &
fi
