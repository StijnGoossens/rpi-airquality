import base64
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


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])
    if df["date"].dt.tz is None:
        df["date"] = df["date"].dt.tz_localize("Europe/Brussels")
    else:
        df["date"] = df["date"].dt.tz_convert("Europe/Brussels")

    numeric_columns = [c for c in df.columns if c != "date"]
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
    return df


def load_records(limit: int | None = 10080) -> pd.DataFrame:
    query = "SELECT * FROM records ORDER BY date DESC"
    if limit is not None:
        query += f" LIMIT {limit}"
    with sqlite3.connect(DB_PATH) as con:
        df = pd.read_sql_query(query, con)
    return _normalize_dataframe(df).sort_values("date")


df = load_records(limit=10080)

if df.empty:
    st.warning("No measurements have been recorded yet.")
    st.stop()

last_record = df.iloc[-1]


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

# Data export.
st.markdown("### Export measurements")
if st.button("⬇️ Prepare complete CSV download"):
    full_df = load_records(limit=None)
    if full_df.empty:
        st.info("No data available yet to download.")
    else:
        csv_bytes = full_df.to_csv(index=False).encode("utf-8")
        csv_b64 = base64.b64encode(csv_bytes).decode()
        st.markdown(
            f'<a href="data:text/csv;base64,{csv_b64}" download="airquality_full_history.csv">'
            "Download airquality_full_history.csv</a>",
            unsafe_allow_html=True,
        )
else:
    st.write("Press the button to prepare the download link.")

# Raspberry Pi shutdown button.
st.markdown("### Shutdown Raspberry Pi")
if st.button("⚠️ Shutdown Raspberry Pi"):
    call("sudo shutdown -h now", shell=True)
