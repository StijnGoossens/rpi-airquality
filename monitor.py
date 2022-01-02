import mh_z19
import time
import sqlite3
import datetime
import smbus2
import bme280
from config import DB_PATH

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
        data = bme280.sample(params["bus"], params["address"], params["calibration_params"])
        temp = data.temperature
        hum = data.humidity
        pressure = data.pressure
    except Exception as e:
        print("EXCEPTION", e)
        temp = None
        hum = None
        pressure = None
    return temp, hum, pressure


def create_table(sql_query):
    try:
        cur.execute(sql_query)
    except sqlite3.OperationalError:
        # The table already exists.
        pass

if __name__ == "__main__":
    # Connect to the database and create thee tables if they don't exist yet.
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    create_table('''CREATE TABLE records (date timestamp, co2 integer, voc real, eco2 real, temp real, hum real, pressure real, session_id integer)''')
    create_table('''CREATE TABLE sessions (session_id integer, start_date timestamp, location text)''')

    # Determine the current session id.
    cur.execute('SELECT * FROM sessions LIMIT 1')
    try:
        result = cur.fetchone()
        session_id = result[0] + 1
    except TypeError:
        session_id = 0
    cur.execute("INSERT INTO sessions VALUES (?, ? ,?)", (session_id, datetime.datetime.now(), ""))
    con.commit()

    # Initialise sensors.
    bme280_params = init_bme280()
    
    # Take measurements every minute.
    while True:
        now = datetime.datetime.now()

        # Read sensors.
        co2 = read_mhz19()
        temp, hum, pressure = read_bme280(bme280_params)
        print(temp, hum, pressure)

        # Add measurements to database.
        cur.execute("INSERT INTO records VALUES (?, ? ,? ,?, ?, ?, ?, ?)", (now, co2, None, None, temp, hum, pressure, session_id))
        con.commit()
        time.sleep(60)

    con.close()
