import mh_z19
import time
import sqlite3
import datetime
import smbus2
import bme280
from config import DB_PATH


if __name__ == "__main__":
    # Connect to the database and create a table if it doesn't exist yet.
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    try:
        cur.execute('''CREATE TABLE records (date timestamp, co2 integer, voc real, eco2 real, temp real, hum real)''')
    except sqlite3.OperationalError:
        # The table already exists.
        pass

    port = 1
    address = 0x77
    bus = smbus2.SMBus(port)
    calibration_params = bme280.load_calibration_params(bus, address)

    # Take measurements every minute.
    while True:
        now = datetime.datetime.now()
        try:
            co2 = mh_z19.read()["co2"]
        except:
            co2 = None

        try:
            data = bme280.sample(bus, address, calibration_params)
            temp = data.temperature
            hum = data.humidity
        except:
            temp = None
            hum = None

        cur.execute("INSERT INTO records VALUES (?, ? ,? ,?, ?, ?)", (now, co2, None, None, temp, hum))
        con.commit()
        time.sleep(60)

    con.close()
