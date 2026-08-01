import atexit
import datetime
import json
import sqlite3
import time
import urllib.request
from contextlib import suppress

import bme280
import mh_z19
import smbus2
try:
    from sensirion_i2c_sps30 import Sps30Device, commands
    from sensirion_driver_adapters.i2c_adapter.linux_i2c_channel_provider import (
        LinuxI2cChannelProvider,
    )
except ImportError:
    LinuxI2cChannelProvider = None  # type: ignore[assignment]
    Sps30Device = None  # type: ignore[assignment]
    commands = None  # type: ignore[assignment]

from config import DB_PATH, LATITUDE, LONGITUDE, NTFY_TOPIC

POLL_FREQUENCY_SECONDS = 300
# Close-the-windows alert: fires once outdoor has risen to within this many degrees of
# indoor (and is still below it). Re-arms once they diverge again by the reset amount.
TEMP_CLOSE_THRESHOLD = 1.0
TEMP_CLOSE_RESET_THRESHOLD = 2.0
# Open-the-windows alert: fires once outdoor has dropped at least this many degrees below
# indoor. Re-arms once the gap shrinks back under the reset amount.
TEMP_OPEN_THRESHOLD = 2.0
TEMP_OPEN_RESET_THRESHOLD = 1.0


def read_mhz19():
    try:
        co2 = mh_z19.read()["co2"]
    except:
        co2 = None
    return co2


def init_bme280():
    port = 1
    address = 0x77
    bus = smbus2.SMBus(port)
    calibration_params = bme280.load_calibration_params(bus, address)
    return {
        "bus": bus,
        "address": address,
        "calibration_params": calibration_params,
    }


def read_bme280(params):
    try:
        data = bme280.sample(
            params["bus"], params["address"], params["calibration_params"]
        )
        temp = data.temperature
        hum = data.humidity
        pressure = data.pressure
    except Exception as e:
        print("EXCEPTION", e)
        temp = None
        hum = None
        pressure = None
    return temp, hum, pressure


# The CCS811 sits on the VMA342 board at 0x5b instead of its 0x5a default.
CCS811_ADDRESS = 0x5B


def init_ccs811(bus):
    try:
        if bus.read_byte_data(CCS811_ADDRESS, 0x20) != 0x81:  # HW_ID
            print("CCS811 not found; skipping TVOC readings.")
            return None
        # Leave the bootloader for the application firmware. APP_START is a bare
        # register write with no data, which write_byte_data cannot express.
        if not bus.read_byte_data(CCS811_ADDRESS, 0x00) & 0x80:  # STATUS.FW_MODE
            bus.i2c_rdwr(smbus2.i2c_msg.write(CCS811_ADDRESS, [0xF4]))
            time.sleep(0.1)
        bus.write_byte_data(CCS811_ADDRESS, 0x01, 0x10)  # MEAS_MODE: one reading per second
    except Exception as exc:
        print("Failed to initialise CCS811:", exc)
        return None
    return bus


def read_ccs811(bus, temp, hum):
    """Return (TVOC ppb, eCO2 ppm), or (None, None) when there is nothing to read.

    Both stay at 0/400 until the sensor has burnt in (~20 minutes from cold, and
    Sensirion asks for 48 hours of running before the baseline settles).
    """
    if not bus:
        return None, None
    try:
        if temp is not None and hum is not None:
            # ENV_DATA compensates the next reading: humidity in 1/512 %RH and
            # temperature in 1/512 °C offset by 25 °C, both big-endian 16-bit.
            bus.write_i2c_block_data(
                CCS811_ADDRESS,
                0x05,
                list(round(hum * 512).to_bytes(2, "big"))
                + list(round((temp + 25) * 512).to_bytes(2, "big")),
            )
        status = bus.read_byte_data(CCS811_ADDRESS, 0x00)
        if status & 0x01:  # STATUS.ERROR
            print("CCS811 error 0x%02x" % bus.read_byte_data(CCS811_ADDRESS, 0xE0))
            return None, None
        if not status & 0x08:  # STATUS.DATA_READY
            return None, None
        eco2_hi, eco2_lo, voc_hi, voc_lo = bus.read_i2c_block_data(
            CCS811_ADDRESS, 0x02, 4
        )  # ALG_RESULT_DATA
        return voc_hi << 8 | voc_lo, eco2_hi << 8 | eco2_lo
    except Exception as exc:
        print("Failed to read CCS811:", exc)
        return None, None


def init_sps30():
    if Sps30Device is None or LinuxI2cChannelProvider is None or commands is None:
        print(
            "sensirion_i2c_sps30 package not available; skipping particulate matter readings."
        )
        return None
    try:
        channel_provider = LinuxI2cChannelProvider("/dev/i2c-1")
        channel_provider.prepare_channel()
        channel = channel_provider.get_channel(
            slave_address=0x69, crc_parameters=(8, 0x31, 0xFF, 0x00)
        )
        device = Sps30Device(channel)
        device.start_measurement(commands.OutputFormat.OUTPUT_FORMAT_FLOAT)
        time.sleep(1)
    except Exception as exc:
        print("Failed to initialise SPS30:", exc)
        return None

    def cleanup():
        with suppress(Exception):
            device.stop_measurement()
        with suppress(Exception):
            channel_provider.release_channel_resources()

    atexit.register(cleanup)
    return {"device": device, "channel_provider": channel_provider}


def read_sps30(params):
    if not params:
        return None, None, None, None
    try:
        if not params["device"].read_data_ready_flag():
            return None, None, None, None
        measurement = params["device"].read_measurement_values_float()
        pm1, pm25, pm4, pm10 = measurement[:4]
    except Exception as exc:
        print("Failed to read SPS30:", exc)
        pm1 = None
        pm25 = None
        pm4 = None
        pm10 = None
    return pm1, pm25, pm4, pm10


OUTDOOR_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LATITUDE}&longitude={LONGITUDE}"
    "&current=temperature_2m,relative_humidity_2m,surface_pressure,"
    "wind_speed_10m,wind_direction_10m"
)
# Outdoor particulates from the CAMS model (hourly, ~10 km resolution).
AIR_QUALITY_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
    f"?latitude={LATITUDE}&longitude={LONGITUDE}"
    "&current=pm2_5,pm10"
)


def _fetch_current(url, keys, attempts=3, retry_delay_seconds=5):
    last_exc = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                current = json.load(response)["current"]
            return tuple(current.get(key) for key in keys)
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(retry_delay_seconds)
    print("Failed to fetch outdoor data:", last_exc)
    return (None,) * len(keys)


def read_outdoor():
    # Wind speed in km/h, direction in degrees (0 = north).
    return _fetch_current(
        OUTDOOR_URL,
        (
            "temperature_2m",
            "relative_humidity_2m",
            "surface_pressure",
            "wind_speed_10m",
            "wind_direction_10m",
        ),
    )


def read_outdoor_air():
    return _fetch_current(AIR_QUALITY_URL, ("pm2_5", "pm10"))


def send_notification(title, message):
    if not NTFY_TOPIC:
        return
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "high", "Tags": "warning"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        print("Failed to send notification:", exc)


def create_table(sql_query):
    try:
        cur.execute(sql_query)
    except sqlite3.OperationalError:
        # The table already exists.
        pass


def ensure_column(table, column, column_type):
    cur.execute(f"PRAGMA table_info({table})")
    existing_columns = [info[1] for info in cur.fetchall()]
    if column not in existing_columns:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


if __name__ == "__main__":
    # Connect to the database and create thee tables if they don't exist yet.
    # WAL so a dashboard read (the full-history chart scans the whole table) no
    # longer blocks our writes; the timeout rides out the brief locks WAL keeps
    # (checkpoints, schema changes) instead of dying on "database is locked".
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    cur = con.cursor()
    create_table(
        """CREATE TABLE records (
        date timestamp,
        co2 integer,
        voc real,
        eco2 real,
        temp real,
        hum real,
        pressure real,
        pm1 real,
        pm25 real,
        pm4 real,
        pm10 real,
        session_id integer
        )"""
    )
    create_table(
        """CREATE TABLE sessions (session_id integer, start_date timestamp, location text)"""
    )
    for column in (
        "pm1",
        "pm25",
        "pm4",
        "pm10",
        "out_temp",
        "out_hum",
        "out_pressure",
        "out_pm25",
        "out_pm10",
        "out_wind_speed",
        "out_wind_dir",
    ):
        ensure_column("records", column, "real")

    # Determine the current session id.
    cur.execute("SELECT * FROM sessions LIMIT 1")
    try:
        result = cur.fetchone()
        session_id = result[0] + 1
    except TypeError:
        session_id = 0
    cur.execute(
        "INSERT INTO sessions VALUES (?, ? ,?)",
        (session_id, datetime.datetime.now(), ""),
    )
    con.commit()

    # Initialise sensors.
    bme280_params = init_bme280()
    ccs811_bus = init_ccs811(bme280_params["bus"])
    sps30_params = init_sps30()

    # Take measurements every minute.
    close_alert_sent = False
    open_alert_sent = False
    while True:
        now = datetime.datetime.now()

        # Read sensors.
        co2 = read_mhz19()
        temp, hum, pressure = read_bme280(bme280_params)
        voc, eco2 = read_ccs811(ccs811_bus, temp, hum)
        pm1, pm25, pm4, pm10 = read_sps30(sps30_params)
        out_temp, out_hum, out_pressure, out_wind_speed, out_wind_dir = read_outdoor()
        out_pm25, out_pm10 = read_outdoor_air()
        print(
            temp, hum, pressure, voc, eco2, pm1, pm25, pm4, pm10,
            out_temp, out_hum, out_pressure, out_pm25, out_pm10,
            out_wind_speed, out_wind_dir,
        )

        # Add measurements to database.
        cur.execute(
            "INSERT INTO records (date, co2, voc, eco2, temp, hum, pressure, pm1, pm25, pm4, pm10, out_temp, out_hum, out_pressure, out_pm25, out_pm10, out_wind_speed, out_wind_dir, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, co2, voc, eco2, temp, hum, pressure, pm1, pm25, pm4, pm10, out_temp, out_hum, out_pressure, out_pm25, out_pm10, out_wind_speed, out_wind_dir, session_id),
        )
        con.commit()

        # Notify when indoor/outdoor temps cross the close/open-window zones.
        if temp is not None and out_temp is not None:
            diff = temp - out_temp  # positive = indoor warmer than outdoor

            # Close the windows: outdoor has risen back up to near indoor.
            if 0 <= diff <= TEMP_CLOSE_THRESHOLD and not close_alert_sent:
                send_notification(
                    "Close the windows",
                    f"Outdoor temp ({out_temp:.1f}°C) is within "
                    f"{TEMP_CLOSE_THRESHOLD:.0f}°C of indoor ({temp:.1f}°C).",
                )
                close_alert_sent = True
            elif abs(diff) > TEMP_CLOSE_RESET_THRESHOLD:
                close_alert_sent = False

            # Open the windows: outdoor has dropped comfortably below indoor.
            if diff >= TEMP_OPEN_THRESHOLD and not open_alert_sent:
                send_notification(
                    "Open the windows",
                    f"Outdoor temp ({out_temp:.1f}°C) is "
                    f"{diff:.1f}°C below indoor ({temp:.1f}°C).",
                )
                open_alert_sent = True
            elif diff < TEMP_OPEN_RESET_THRESHOLD:
                open_alert_sent = False

        time.sleep(POLL_FREQUENCY_SECONDS)

    con.close()
