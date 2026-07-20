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

from config import DB_PATH, LATITUDE, LONGITUDE

POLL_FREQUENCY_SECONDS = 300


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


def _fetch_current(url, keys):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            current = json.load(response)["current"]
        return tuple(current.get(key) for key in keys)
    except Exception as exc:
        print("Failed to fetch outdoor data:", exc)
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
    con = sqlite3.connect(DB_PATH)
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
    sps30_params = init_sps30()

    # Take measurements every minute.
    while True:
        now = datetime.datetime.now()

        # Read sensors.
        co2 = read_mhz19()
        temp, hum, pressure = read_bme280(bme280_params)
        pm1, pm25, pm4, pm10 = read_sps30(sps30_params)
        out_temp, out_hum, out_pressure, out_wind_speed, out_wind_dir = read_outdoor()
        out_pm25, out_pm10 = read_outdoor_air()
        print(
            temp, hum, pressure, pm1, pm25, pm4, pm10,
            out_temp, out_hum, out_pressure, out_pm25, out_pm10,
            out_wind_speed, out_wind_dir,
        )

        # Add measurements to database.
        cur.execute(
            "INSERT INTO records (date, co2, voc, eco2, temp, hum, pressure, pm1, pm25, pm4, pm10, out_temp, out_hum, out_pressure, out_pm25, out_pm10, out_wind_speed, out_wind_dir, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, co2, None, None, temp, hum, pressure, pm1, pm25, pm4, pm10, out_temp, out_hum, out_pressure, out_pm25, out_pm10, out_wind_speed, out_wind_dir, session_id),
        )
        con.commit()
        time.sleep(POLL_FREQUENCY_SECONDS)

    con.close()
