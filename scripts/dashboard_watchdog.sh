#!/bin/sh
# Restart the Streamlit dashboard if it isn't running.
# Match on "bin/streamlit" (the venv binary path) rather than "streamlit run":
# a pattern embedded directly in a crontab line self-matches cron's own
# invocation of this check, since that shell's command line contains the
# same text. Keeping the check in its own script file avoids that.

# Only one run at a time: a restart by hand racing the 5-minute cron run makes
# both see "not running" and start a server, and the loser never binds 4202 --
# it just sits there holding ~130 MB on a 921 MB Pi.
exec 9> "$HOME/.dashboard_watchdog.lock"
flock -n 9 || exit 0

if ! pgrep -f "bin/streamlit" > /dev/null; then
    echo "$(date): streamlit not running, restarting"
    # 9>&- closes the lock fd in the child: it outlives this script, and would
    # otherwise hold the lock forever and silently disable every later run.
    nohup "$HOME/venvs/airquality/bin/streamlit" run \
        "$HOME/Documents/rpi-airquality/src/dashboard.py" \
        --server.address 0.0.0.0 --server.port 4202 \
        >> "$HOME/cronjoblog-dashboard" 2>&1 9>&- &
fi
