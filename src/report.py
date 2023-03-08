"""Create timeseries plots for a specified time interval."""

import sqlite3
import pandas as pd
from config import DB_PATH
from datetime import datetime
import matplotlib.pyplot as plt


from_date = "22/01/2023"
to_date = "20/02/2023" # None

from_date = datetime.strptime(from_date, "%d/%m/%Y")
to_date = datetime.strptime(to_date, "%d/%m/%Y") if to_date else datetime.now()

print(from_date, to_date)
# delta_days = (to_date - from_date + 1) * 60 * 24

con = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
df = pd.read_sql_query(f'SELECT *, date as "[timestamp]" FROM records WHERE date > "{from_date}" and date <= "{to_date}" ORDER BY date DESC', con)
# # Transform the date to a localised datetime.
df['date'] = pd.to_datetime(df['date'])
df["date"] = df["date"].dt.tz_localize("Europe/Brussels")

def plot_metric(df: pd.DataFrame, metric: str) -> None:
    plt.figure(figsize=(20,5))
    plt.plot(df["date"], df[metric])
    plt.ylabel(metric)
    plt.savefig(f"/home/pi/Documents/rpi-airquality/output/{metric}_over_time.jpg")

plot_metric(df, "co2")