"""Store Zigbee2MQTT sensor readings in the air quality database.

Reads from a mosquitto_sub child process instead of an MQTT library, so this stays
stdlib-only. mosquitto_sub reconnects by itself when the broker restarts; if it can't
connect at all it exits, and so do we, and scripts/zigbee_watchdog.sh retries.

Check the parsing with:  python3 src/zigbee_receiver.py --demo
"""

import datetime
import json
import sqlite3
import subprocess
import sys

from config import DB_PATH, ZIGBEE_ROOMS

# -R skips retained messages: readings are timestamped on arrival, so an old retained
# value would otherwise be stored as if it had just been measured. The single-level
# wildcard matches devices only, not zigbee2mqtt/bridge/... or .../availability.
COMMAND = ["mosquitto_sub", "-R", "-v", "-t", "zigbee2mqtt/+"]

CREATE_TABLE = """CREATE TABLE IF NOT EXISTS zigbee_records (
    date timestamp,
    room text,
    temp real,
    hum real,
    battery real,
    linkquality integer,
    contact integer
)"""


def parse(line, rooms):
    """Turn a `topic {json}` line into (room, temp, hum, battery, linkquality, contact).

    Returns None for anything that isn't a reading from a known device. Splitting on
    " {" rather than the first space keeps device names containing spaces intact.
    """
    topic, json_found, payload = line.partition(" {")
    device = topic.removeprefix("zigbee2mqtt/")
    if json_found and device not in rooms:
        print(f"Ignoring unknown device {device!r}; add it to ZIGBEE_ROOMS", flush=True)
        return None
    try:
        data = json.loads("{" + payload)
    except ValueError:
        print(f"Ignoring unreadable message: {line.strip()}", flush=True)
        return None
    return (
        rooms[device],
        data.get("temperature"),
        data.get("humidity"),
        data.get("battery"),
        data.get("linkquality"),
        data.get("contact"),
    )


def store(con, reading):
    # A plain string rather than a datetime: same stored format as monitor.py, without
    # the default adapter that Python 3.12 deprecated.
    con.execute(
        "INSERT INTO zigbee_records VALUES (?, ?, ?, ?, ?, ?, ?)",
        (datetime.datetime.now().isoformat(" "), *reading),
    )
    con.commit()


def connect():
    # Same WAL and timeout as monitor.py: both write to this database.
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(CREATE_TABLE)
    return con


def run():
    con = connect()
    # No `with`: Popen.__exit__ waits for the child, so a database error here would hang
    # until the next message instead of exiting and letting the watchdog take over.
    sub = subprocess.Popen(
        COMMAND, stdout=subprocess.PIPE, encoding="utf-8", errors="replace"
    )
    for line in sub.stdout:
        reading = parse(line, ZIGBEE_ROOMS)
        if reading:
            store(con, reading)
    sys.exit(sub.wait())


def demo():
    rooms = {"living_sensor": "living room", "hall sensor": "hall"}

    line = (
        'zigbee2mqtt/living_sensor {"temperature":18.4,"humidity":52.1,"battery":97,'
        '"linkquality":120,"last_seen":"2026-11-09T08:00:00+01:00"}\n'
    )
    reading = parse(line, rooms)
    assert reading == ("living room", 18.4, 52.1, 97, 120, None), reading

    # A window contact has no temperature, and the name may hold a space.
    reading = parse('zigbee2mqtt/hall sensor {"contact":false}\n', rooms)
    assert reading == ("hall", None, None, None, None, False), reading

    # Unknown devices and broken payloads are skipped rather than stored or fatal.
    assert parse('zigbee2mqtt/other {"temperature":1}\n', rooms) is None
    assert parse('zigbee2mqtt/living_sensor {"temperature":\n', rooms) is None
    assert parse("zigbee2mqtt/living_sensor online\n", rooms) is None

    con = sqlite3.connect(":memory:")
    con.execute(CREATE_TABLE)
    store(con, ("living room", 18.4, 52.1, 97, 120, False))
    date, *rest = con.execute("SELECT * FROM zigbee_records").fetchone()
    assert rest == ["living room", 18.4, 52.1, 97.0, 120, 0], rest
    datetime.datetime.fromisoformat(date)  # summary.py and the dashboard parse this
    print("ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        run()
