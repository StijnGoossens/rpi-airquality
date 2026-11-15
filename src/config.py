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

# Zigbee2MQTT device name -> room, e.g. {"living_sensor": "living room"}. Set in the
# gitignored src/location.py. Devices not listed here are ignored by zigbee_receiver.py.
ZIGBEE_ROOMS = {}
try:
    from location import ZIGBEE_ROOMS  # noqa: F811
except ImportError:
    pass

# What to call the room the Pi itself stands in when charted next to the Zigbee rooms.
PI_ROOM = "pi"
try:
    from location import PI_ROOM  # noqa: F811
except ImportError:
    pass

# Added to the BME280 humidity (in percentage points) when displaying it, so a side-by-side
# calibration can be applied or changed later: the database keeps the raw readings.
BME280_HUM_OFFSET = 0.0
try:
    from location import BME280_HUM_OFFSET  # noqa: F811
except ImportError:
    pass
