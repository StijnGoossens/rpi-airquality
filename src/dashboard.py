import base64
import datetime
import sqlite3
from subprocess import call

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from config import DB_PATH

PM_COLUMNS = ["pm1", "pm25", "pm4", "pm10"]
PM_LABELS = {
    "pm1": "PM1.0",
    "pm25": "PM2.5",
    "pm4": "PM4.0",
    "pm10": "PM10",
}

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


def plot_pm_over_time(df, domain=None):
    # Check which PM columns are available in the dataframe.
    available_columns = [col for col in PM_COLUMNS if col in df.columns]
    if not available_columns:
        return None

    # Prepare the data by removing the first valid measurement for each PM size.
    pm_df = df[["date"] + available_columns].copy()
    pm_df = pm_df.dropna(subset=available_columns, how="all")
    if pm_df.empty:
        return None

    # Remove first valid measurement for each column to avoid initialization spikes.
    for column in available_columns:
        first_idx = pm_df[column].first_valid_index()
        if first_idx is not None:
            pm_df.loc[first_idx, column] = np.nan
    pm_df = pm_df.dropna(subset=available_columns, how="all")
    if pm_df.empty:
        return None

    # Prepare long-form data for Altair.
    label_map = {col: PM_LABELS[col] for col in available_columns}
    ordered_labels = [label_map[col] for col in available_columns]

    # Raw data.
    raw_long = (
        pm_df.rename(columns=label_map)
        .melt("date", var_name="particulate", value_name="μg/m³")
        .dropna(subset=["μg/m³"])
    )
    if raw_long.empty:
        return None

    # Smoothed data (5-point rolling mean).
    smoothed_wide = pm_df[["date"]].copy()
    for col in available_columns:
        smoothed_wide[col] = pm_df[col].rolling(window=5, min_periods=1).mean()

    # Long-form smoothed data.
    smoothed_long = (
        smoothed_wide.rename(columns=label_map)
        .melt("date", var_name="particulate", value_name="μg/m³")
        .dropna(subset=["μg/m³"])
    )
    smoothed_long = smoothed_long[smoothed_long["particulate"].isin(ordered_labels)]

    # Create the Altair chart.
    x_encoding = alt.X(
        "date:T",
        axis=alt.Axis(title="time", format=("%H %M")),
        scale=alt.Scale(domain=domain) if domain else alt.Undefined,
    )
    y_encoding = alt.Y("μg/m³:Q", axis=alt.Axis(title="mass concentration (μg/m³)"))
    color_encoding = alt.Color("particulate:N", sort=ordered_labels, title="PM size")

    # Base chart with raw data.
    base_chart = (
        alt.Chart(raw_long)
        .mark_line(strokeWidth=0.5)
        .encode(x=x_encoding, y=y_encoding, color=color_encoding)
    )

    # Add smoothed data on top, if available.
    if smoothed_long.empty:
        return base_chart
    smooth_chart = (
        alt.Chart(smoothed_long)
        .mark_line(strokeWidth=2)
        .encode(x=x_encoding, y=y_encoding, color=color_encoding)
    )
    return base_chart + smooth_chart


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
if "pm25" in last_record and pd.notna(last_record["pm25"]):
    st.markdown(f"## {last_record['pm25']:.1f} µg/m³")
    st.text("🌫 PM2.5")

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
    domain = None
    if not filtered_df.empty:
        domain = (filtered_df["date"].min(), filtered_df["date"].max())
    pm_chart = plot_pm_over_time(filtered_df, domain=domain)
    if pm_chart is not None:
        st.altair_chart(pm_chart, use_container_width=True)

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
