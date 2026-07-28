from pathlib import Path

# Store the SQLite database alongside the repository so the path works for any user.
DB_PATH = Path(__file__).resolve().parent.parent / "airquality.db"

# Location used to fetch outdoor weather from Open-Meteo. Defaults to Brussels; put your
# real coordinates in the gitignored src/location.py to keep them out of the public repo.
LATITUDE = 50.85
LONGITUDE = 4.35
try:
    from location import LATITUDE, LONGITUDE  # noqa: F811
except ImportError:
    pass

# ntfy.sh topic to push notifications to (e.g. indoor/outdoor temp getting close).
# Set in the gitignored src/location.py to keep it out of the public repo. Install the
# ntfy app (https://ntfy.sh) and subscribe to the same topic to receive alerts.
NTFY_TOPIC = None
try:
    from location import NTFY_TOPIC  # noqa: F811
except ImportError:
    pass
