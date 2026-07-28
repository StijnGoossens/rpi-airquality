#!/bin/sh
# Restart the Streamlit dashboard if it isn't running.
# Match on "bin/streamlit" (the venv binary path) rather than "streamlit run":
# a pattern embedded directly in a crontab line self-matches cron's own
# invocation of this check, since that shell's command line contains the
# same text. Keeping the check in its own script file avoids that.
if ! pgrep -f "bin/streamlit" > /dev/null; then
    echo "$(date): streamlit not running, restarting"
    nohup "$HOME/venvs/airquality/bin/streamlit" run \
        "$HOME/Documents/rpi-airquality/src/dashboard.py" \
        --server.address 0.0.0.0 --server.port 4202 \
        >> "$HOME/cronjoblog-dashboard" 2>&1 &
fi
