import datetime
import sqlite3
from subprocess import call

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from config import DB_PATH

# Compat shim for legacy dependencies expecting deprecated numpy aliases.
if not hasattr(np, "object"):
    np.object = object  # type: ignore[attr-defined,assignment]
if not hasattr(np, "bool"):
    np.bool = bool  # type: ignore[attr-defined,assignment]

# Connect to the database and load the 10080 most recent records, i.e., with one sample per minute,
# equal to the past week (=60*24*7).
con = sqlite3.connect(DB_PATH)
df = pd.read_sql_query("SELECT * FROM records ORDER BY date DESC LIMIT 10080", con)
# Transform the date to a localised datetime.
df["date"] = pd.to_datetime(df["date"])
df["date"] = df["date"].dt.tz_localize("Europe/Brussels")
# Ensure numeric columns are floats; SQLite nulls otherwise promote dtype=object.
numeric_columns = [c for c in df.columns if c != "date"]
df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
# Get the first record for the current air quality.
last_record = df.iloc[0]


def plot_metric_over_time(df, col):
    return (
        alt.Chart(df)
        .mark_line()
        .encode(x=alt.X("date:T", axis=alt.Axis(title="time", format=("%H %M"))), y=col)
    )


# def set_room():
#     print(room)

#### DEFINE THE STREAMLIT APP ####

# Hide the hamburger menu and remove the default margins on top and bottom.
# Source: https://discuss.streamlit.io/t/how-do-i-hide-remove-the-menu-in-production/362/10
st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .reportview-container .main .block-container {padding-top: 0; padding-bottom: 0}
    </style>
    """,
    unsafe_allow_html=True,
)

# The current metrics.
st.markdown("# Current air quality")
st.text(f"🗓 {last_record['date'].strftime('%d-%m-%Y %H:%M')}")
# room = st.text_input("Room", on_change=set_room)
co2_alert = "🚨" if last_record["co2"] > 900 else ""
st.markdown(f"## {last_record['co2']:.0f} ppm {co2_alert}")
st.text("😶‍🌫️ CO2 level")
st.markdown(f"## {last_record['temp']:.1f} °C")
st.text("🌡 Temperature")
st.markdown(f"## {last_record['hum']:.0f} %")
st.text("💧 Humidity")

# Evolution over time.
st.markdown("# Air quality evolution")
date = st.date_input("Day of interest", datetime.datetime.now())
filtered_df = df[df["date"].dt.date == date].copy()

if filtered_df.empty:
    st.info("No measurements recorded for the selected day yet.")
else:
    st.altair_chart(plot_metric_over_time(filtered_df, "co2"), use_container_width=True)
    st.altair_chart(
        plot_metric_over_time(filtered_df, "temp"), use_container_width=True
    )
    st.altair_chart(plot_metric_over_time(filtered_df, "hum"), use_container_width=True)
    st.altair_chart(
        plot_metric_over_time(filtered_df, "pressure"), use_container_width=True
    )

# Raspberry Pi shutdown button.
if st.button("⚠️ Shutdown Raspberry Pi"):
    call("sudo shutdown -h now", shell=True)
