#!/bin/sh
# Restart the air quality monitor if it isn't running.
# Same self-match trap as dashboard_watchdog.sh: keep the pgrep pattern in this
# file (not inline in the crontab) and match the script path, not "monitor.py".

# Only one run at a time -- see dashboard_watchdog.sh; two monitors writing the
# same database would also double every measurement.
exec 9> "$HOME/.monitor_watchdog.lock"
flock -n 9 || exit 0

if ! pgrep -f "rpi-airquality/src/monitor.py" > /dev/null; then
    echo "$(date): monitor not running, restarting"
    # 9>&- so the child does not inherit and hold the lock.
    nohup "$HOME/venvs/airquality/bin/python" \
        "$HOME/Documents/rpi-airquality/src/monitor.py" \
        >> "$HOME/cronjoblog-monitor" 2>&1 9>&- &
fi
