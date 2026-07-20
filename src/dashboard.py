import base64
import datetime
import sqlite3
from subprocess import call

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from config import DB_PATH

NIGHT_START, NIGHT_END = 22, 7  # night is 22:00 -> 07:00
TEMP_THRESHOLDS = [
    (20, "#5aa9e6", "20°C tropical-night threshold"),
    (26, "#f2a516", "26°C warm indoor threshold"),
    (28, "#e8630a", "28°C hot indoor threshold"),
]
WEEK_FEATURES = {
    "temp": "Temperature (°C)",
    "co2": "CO2 (ppm)",
    "hum": "Humidity (%)",
    "pressure": "Pressure (hPa)",
    "pm25": "PM2.5 (µg/m³)",
}

# Outdoor (Open-Meteo) counterparts of indoor metrics, drawn as dashed gray comparison lines.
OUTDOOR_COLUMNS = {"temp": "out_temp", "hum": "out_hum", "pressure": "out_pressure"}
OUTDOOR_COLOR = "#888888"

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


def load_records(limit: int | None = None) -> pd.DataFrame:
    query = "SELECT * FROM records ORDER BY date DESC"
    if limit is not None:
        query += f" LIMIT {limit}"
    with sqlite3.connect(DB_PATH) as con:
        df = pd.read_sql_query(query, con)
    return _normalize_dataframe(df).sort_values("date")


def load_day(day: datetime.date) -> pd.DataFrame:
    # Dates are stored as naive local ISO strings, so a prefix match selects a day.
    with sqlite3.connect(DB_PATH) as con:
        df = pd.read_sql_query(
            "SELECT * FROM records WHERE date LIKE ? ORDER BY date",
            con,
            params=(f"{day.isoformat()}%",),
        )
    return _normalize_dataframe(df)


def load_last_days(days: int = 7) -> pd.DataFrame:
    # Stored dates are local ISO strings, so lexicographic >= works.
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with sqlite3.connect(DB_PATH) as con:
        df = pd.read_sql_query(
            "SELECT * FROM records WHERE date >= ? ORDER BY date",
            con,
            params=(cutoff,),
        )
    return _normalize_dataframe(df)


def night_spans(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    spans = []
    day = start.normalize()
    while day <= end.normalize():
        n0 = day + pd.Timedelta(hours=NIGHT_START)
        n1 = day + pd.Timedelta(hours=24 + NIGHT_END)
        if n0 < end and n1 > start:
            spans.append((max(n0, start), min(n1, end)))
        day += pd.Timedelta(days=1)
    return spans


def plot_week_overview(df: pd.DataFrame, col: str, label: str) -> alt.Chart | None:
    data = df[["date", col]].dropna()
    if data.empty:
        return None
    start, end = data["date"].min(), data["date"].max()
    indexed = data.set_index("date")[col]
    # Downsample the raw line to keep the browser-side chart light.
    raw = indexed.resample("5min").mean().dropna().reset_index()
    hourly = indexed.resample("1h").mean().dropna().reset_index()

    x = alt.X("date:T", axis=alt.Axis(title=None, format="%b %d"))
    y = alt.Y(f"{col}:Q", title=label, scale=alt.Scale(zero=False))

    bands_df = pd.DataFrame(night_spans(start, end), columns=["n0", "n1"])
    bands = (
        alt.Chart(bands_df)
        .mark_rect(color="#cccccc", opacity=0.4)
        .encode(x="n0:T", x2="n1:T")
    )
    raw_line = (
        alt.Chart(raw).mark_line(strokeWidth=0.5, color="#e8763a", opacity=0.6).encode(x=x, y=y)
    )
    hourly_line = alt.Chart(hourly).mark_line(strokeWidth=2.5, color="#d0421b").encode(
        x=x, y=y, tooltip=["date:T", alt.Tooltip(f"{col}:Q", format=".1f", title=label)]
    )
    chart = bands + raw_line + hourly_line

    out_col = OUTDOOR_COLUMNS.get(col)
    if out_col and out_col in df.columns and df[out_col].notna().any():
        out_hourly = (
            df.set_index("date")[out_col].resample("1h").mean().dropna().reset_index()
        )
        chart += alt.Chart(out_hourly).mark_line(
            strokeWidth=1.5, color=OUTDOOR_COLOR, strokeDash=[5, 3]
        ).encode(
            x=x,
            y=alt.Y(f"{out_col}:Q", title=label, scale=alt.Scale(zero=False)),
            tooltip=["date:T", alt.Tooltip(f"{out_col}:Q", format=".1f", title="Outdoor")],
        )

    if col == "temp":
        thr_df = pd.DataFrame(TEMP_THRESHOLDS, columns=["y", "color", "label"])
        thr_df["x"] = end
        color = alt.Color("color:N", scale=None)
        chart += alt.Chart(thr_df).mark_rule(strokeDash=[6, 4]).encode(
            y="y:Q", color=color
        )
        chart += alt.Chart(thr_df).mark_text(align="right", dx=-4, dy=-7).encode(
            x="x:T", y="y:Q", text="label:N", color=color
        )
        minima = [
            {"date": chunk.idxmin(), "y": chunk.min(), "label": f"{chunk.min():.1f}°C"}
            for n0, n1 in night_spans(start, end)
            if not (chunk := indexed[n0:n1]).empty
        ]
        min_df = pd.DataFrame(minima)
        chart += alt.Chart(min_df).mark_point(filled=True, size=90, color="#2077b4").encode(
            x="date:T", y="y:Q", tooltip=[alt.Tooltip("y:Q", format=".1f", title="Night min")]
        )
        chart += alt.Chart(min_df).mark_text(dy=14, color="#2077b4").encode(
            x="date:T", y="y:Q", text="label:N"
        )

    return chart.properties(height=350)


latest_df = load_records(limit=1)

if latest_df.empty:
    st.warning("No measurements have been recorded yet.")
    st.stop()

last_record = latest_df.iloc[-1]


def plot_metric_over_time(df, col):
    chart = (
        alt.Chart(df)
        .mark_line()
        .encode(x=alt.X("date:T", axis=alt.Axis(title="time", format=("%H %M"))), y=col)
    )
    out_col = OUTDOOR_COLUMNS.get(col)
    if out_col and out_col in df.columns and df[out_col].notna().any():
        outdoor = (
            alt.Chart(df.dropna(subset=[out_col]))
            .mark_line(color=OUTDOOR_COLOR, strokeDash=[5, 3])
            .encode(
                x="date:T",
                y=alt.Y(f"{out_col}:Q", title=col),
                tooltip=[
                    "date:T",
                    alt.Tooltip(f"{out_col}:Q", format=".1f", title=f"outdoor {col}"),
                ],
            )
        )
        chart = (chart + outdoor).properties(
            title=alt.TitleParams("solid = indoor, dashed gray = outdoor", fontSize=11, anchor="end")
        )
    return chart


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
age = pd.Timestamp.now(tz="Europe/Brussels") - last_record["date"]
if age > pd.Timedelta(minutes=10):
    st.warning(
        f"⚠️ Last measurement is {int(age.total_seconds() // 60)} minutes old "
        "— the monitor may be down."
    )
# room = st.text_input("Room", on_change=set_room)
co2_alert = "🚨" if last_record["co2"] > 900 else ""
st.markdown(f"## {last_record['co2']:.0f} ppm {co2_alert}")
st.text("😶‍🌫️ CO2 level")
st.markdown(f"## {last_record['temp']:.1f} °C")
st.text("🌡 Temperature")
if "out_temp" in last_record and pd.notna(last_record["out_temp"]):
    st.text(f"🌳 Outdoor: {last_record['out_temp']:.1f} °C")
day_temps = load_last_days(1).dropna(subset=["temp"])
if not day_temps.empty:
    tmin = day_temps.loc[day_temps["temp"].idxmin()]
    tmax = day_temps.loc[day_temps["temp"].idxmax()]
    st.text(f"↓ {tmin['temp']:.1f} °C at {tmin['date'].strftime('%H:%M')} (last 24h)")
    st.text(f"↑ {tmax['temp']:.1f} °C at {tmax['date'].strftime('%H:%M')} (last 24h)")
st.markdown(f"## {last_record['hum']:.0f} %")
st.text("💧 Humidity")
if "out_hum" in last_record and pd.notna(last_record["out_hum"]):
    st.text(f"🌳 Outdoor: {last_record['out_hum']:.0f} %")
if "pm25" in last_record and pd.notna(last_record["pm25"]):
    pm_alert = "🚨" if last_record["pm25"] > 15 else ""
    st.markdown(f"## {last_record['pm25']:.1f} µg/m³ {pm_alert}")
    st.text("🌫 PM2.5")

# Evolution over time.
st.markdown("# Air quality evolution")
date = st.date_input("Day of interest", datetime.datetime.now())
filtered_df = load_day(date)

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

# Last 7 days overview.
st.markdown("# Last 7 days")
week_df = load_last_days(7)
if week_df.empty:
    st.info("No measurements recorded in the last 7 days.")
else:
    available = [c for c in WEEK_FEATURES if c in week_df.columns]
    feature = st.selectbox(
        "Feature", available, index=available.index("temp"), format_func=WEEK_FEATURES.get
    )
    week_chart = plot_week_overview(week_df, feature, WEEK_FEATURES[feature])
    if week_chart is None:
        st.info("No measurements for this feature in the last 7 days.")
    else:
        caption = f"Shaded bands are nights, {NIGHT_START}:00-0{NIGHT_END}:00"
        if feature in OUTDOOR_COLUMNS:
            caption += " — dashed gray line is outdoor"
        st.text(caption)
        st.altair_chart(week_chart, use_container_width=True)

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
if st.checkbox("I really want to shut down the Pi"):
    if st.button("⚠️ Shutdown Raspberry Pi"):
        call("sudo shutdown -h now", shell=True)
