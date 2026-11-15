import base64
import csv
import datetime
import io
import sqlite3
from subprocess import call
from zoneinfo import ZoneInfo

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from config import BME280_HUM_OFFSET, DB_PATH, PI_ROOM, ZIGBEE_ROOMS
from utils import baseline_deviation, smooth_steps

NIGHT_START, NIGHT_END = 22, 7  # night is 22:00 -> 07:00
EXPORT_TZ = ZoneInfo("Europe/Brussels")  # matches _normalize_dataframe's localisation
WEEK_FEATURES = {
    "temp": "Temperature (°C)",
    "co2": "CO2 (ppm)",
    "hum": "Humidity (%)",
    "pressure": "Pressure (hPa)",
    "pm25": "PM2.5 (µg/m³)",
    "voc": "TVOC (ppb)",
}

# Outdoor (Open-Meteo) counterparts of indoor metrics, drawn as dashed gray comparison lines.
OUTDOOR_COLUMNS = {"temp": "out_temp", "hum": "out_hum", "pressure": "out_pressure"}
OUTDOOR_COLOR = "#888888"

# Every room the charts can show: the Pi's own, then the Zigbee rooms.
ROOMS = [PI_ROOM, *sorted(set(ZIGBEE_ROOMS.values()) - {PI_ROOM})]
# A fixed colour per room, so hiding one never repaints the rest. The Pi's room keeps
# the red its lines have always had; teal and violet stay apart from that red under
# red-green colour blindness.
PI_COLOR = "#d0421b"
# ponytail: two hues for two sensors; a third room reuses teal, add a validated hue then.
ZIGBEE_COLORS = ["#1a9e9e", "#8e5cd9"]
ROOM_COLOR = {PI_ROOM: PI_COLOR} | {
    room: ZIGBEE_COLORS[i % len(ZIGBEE_COLORS)] for i, room in enumerate(ROOMS[1:])
}
# A one-row legend above the plot, instead of a column that narrows it.
ROOM_LEGEND = alt.Legend(orient="top", title=None)


def room_lines(rooms: pd.DataFrame, col: str) -> pd.DataFrame:
    """Each Zigbee room's readings for col with their steps smoothed; see smooth_steps."""
    return pd.concat(
        [
            smooth_steps(readings.set_index("date")[col].dropna()).reset_index().assign(room=room)
            for room, readings in rooms.groupby("room")
        ],
        ignore_index=True,
    )


def room_scale(rooms) -> alt.Scale:
    """Colour scale naming just these rooms, each in its fixed colour."""
    present = set(rooms)
    named = [room for room in ROOMS if room in present]
    return alt.Scale(domain=named, range=[ROOM_COLOR[room] for room in named])

# How far above its own 24h baseline a TVOC reading has to sit before it counts
# as a spike, in robust z-scores. 3.5 is the usual cut-off for this measure.
# The CCS811's ppb output re-baselines itself continuously, so there is no
# absolute number to threshold against -- only "unusual for this room lately".
VOC_SPIKE_DEVIATION = 3.5

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

    numeric_columns = [c for c in df.columns if c not in ("date", "room")]
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
    return df


def _normalize_records(df: pd.DataFrame) -> pd.DataFrame:
    # Only for the Pi's own table: the Zigbee sensors don't share the BME280's offset.
    df = _normalize_dataframe(df)
    df["hum"] += BME280_HUM_OFFSET
    return df


def load_records(limit: int | None = None) -> pd.DataFrame:
    query = "SELECT * FROM records ORDER BY date DESC"
    if limit is not None:
        query += f" LIMIT {limit}"
    with sqlite3.connect(DB_PATH) as con:
        df = pd.read_sql_query(query, con)
    return _normalize_records(df).sort_values("date")


def load_day(day: datetime.date) -> pd.DataFrame:
    # Dates are stored as naive local ISO strings, so a prefix match selects a day.
    with sqlite3.connect(DB_PATH) as con:
        df = pd.read_sql_query(
            "SELECT * FROM records WHERE date LIKE ? ORDER BY date",
            con,
            params=(f"{day.isoformat()}%",),
        )
    return _normalize_records(df)


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
    return _normalize_records(df)


def load_room_readings(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    # Stored dates are local ISO strings, so lexicographic comparison works.
    window = (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with sqlite3.connect(DB_PATH) as con:
            df = pd.read_sql_query(
                "SELECT date, room, temp, hum FROM zigbee_records "
                "WHERE date >= ? AND date <= ? ORDER BY date",
                con,
                params=window,
            )
    except pd.errors.DatabaseError:
        # The table only exists once zigbee_receiver.py has run; without Zigbee
        # sensors the dashboard simply has no room charts.
        return pd.DataFrame()
    return _normalize_dataframe(df)


def _float_export_columns(cur, columns: list[str]) -> set[str]:
    # pandas widens any numeric column containing a NULL to float64, so those
    # export as "426.0" rather than "426". Counting the nulls sqlite-side keeps
    # the streamed CSV byte-identical to the pandas one it replaces.
    # Column names come from the table schema, not from user input.
    counts = cur.execute(
        "SELECT COUNT(*), " + ", ".join(f"COUNT({c})" for c in columns) + " FROM records"
    ).fetchone()
    total, per_column = counts[0], counts[1:]
    return {c for c, filled in zip(columns, per_column) if filled < total}


def full_history_download_link(chunk_rows: int = 2000) -> str | None:
    """Build the CSV download link, streaming so the history is never one blob.

    Streamlit 0.62 has no download_button, so the file has to be embedded in the
    page as a base64 data: URI. Reading the history into a DataFrame first cost
    ~106 MB peak on a 921 MB Pi; going straight from the sqlite cursor to base64
    chunks costs ~39 MB for the same bytes. Returns None when there is no data.
    """
    parts = ['<a href="data:text/csv;base64,']
    buf = io.StringIO()
    # to_csv() writes \n; csv.writer defaults to \r\n.
    writer = csv.writer(buf, lineterminator="\n")
    carry = b""
    rows_seen = 0

    con = sqlite3.connect(DB_PATH)
    try:
        columns = [d[1] for d in con.execute("PRAGMA table_info(records)")]
        float_columns = _float_export_columns(con.cursor(), columns)
        is_float = [c in float_columns for c in columns]
        date_at = columns.index("date")

        writer.writerow(columns)
        cur = con.execute("SELECT * FROM records ORDER BY date")
        while rows := cur.fetchmany(chunk_rows):
            rows_seen += len(rows)
            for row in rows:
                out = ["" if v is None else repr(float(v)) if f else str(v)
                       for v, f in zip(row, is_float)]
                # Dates are stored naive local; _normalize_dataframe localises
                # them to Europe/Brussels, so the export has to match.
                out[date_at] = (
                    datetime.datetime.fromisoformat(row[date_at])
                    .replace(tzinfo=EXPORT_TZ)
                    .isoformat(sep=" ")
                )
                writer.writerow(out)

            chunk = carry + buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate(0)
            # b64encode(a) + b64encode(b) equals b64encode(a + b) only when
            # len(a) is a multiple of 3, so carry the remainder to the next chunk.
            cut = len(chunk) - len(chunk) % 3
            parts.append(base64.b64encode(chunk[:cut]).decode("ascii"))
            carry = chunk[cut:]
    finally:
        con.close()

    if not rows_seen:
        return None
    if carry:
        parts.append(base64.b64encode(carry).decode("ascii"))
    parts.append(
        '" download="airquality_full_history.csv">'
        "Download airquality_full_history.csv</a>"
    )
    return "".join(parts)


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


def plot_week_overview(
    df: pd.DataFrame,
    col: str,
    label: str,
    rooms: pd.DataFrame | None = None,
    shown: list[str] = ROOMS,
) -> alt.Chart | None:
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
    hourly_line = alt.Chart(hourly).mark_line(strokeWidth=2.5, color=PI_COLOR).encode(
        x=x, y=y, tooltip=["date:T", alt.Tooltip(f"{col}:Q", format=".1f", title=label)]
    )
    show_pi = PI_ROOM in shown
    chart = bands + raw_line + hourly_line if show_pi else bands

    if rooms is not None and not rooms.empty:
        smoothed = room_lines(rooms, col)
        smoothed = smoothed[smoothed["room"].isin(shown)]
        # The Pi's line is drawn above, but naming it here lets the legend explain the red.
        named = {*smoothed["room"], *([PI_ROOM] if show_pi else [])}
        chart += alt.Chart(smoothed).mark_line(strokeWidth=2).encode(
            x=x,
            y=y,
            color=alt.Color("room:N", scale=room_scale(named), legend=ROOM_LEGEND),
            tooltip=["date:T", "room:N", alt.Tooltip(f"{col}:Q", format=".1f", title=label)],
        )

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

    if col == "temp" and show_pi:
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


def time_axis_format(df) -> str:
    # A window crossing midnight repeats every clock label, so name the day too.
    return "%H %M" if df["date"].dt.normalize().nunique() <= 1 else "%a %H %M"


def plot_metric_over_time(df, col, baseline=None):
    chart = (
        alt.Chart(df)
        .mark_line()
        .encode(
            x=alt.X("date:T", axis=alt.Axis(title="time", format=time_axis_format(df))),
            y=col,
        )
    )
    if baseline is not None:
        # For a self-baselining sensor the line height means little on its own;
        # what it does either side of this reference is the whole reading.
        chart += (
            alt.Chart(pd.DataFrame({"baseline": [baseline]}))
            .mark_rule(color=OUTDOOR_COLOR, strokeDash=[4, 4])
            .encode(y=alt.Y("baseline:Q", title=col))
        )
        chart = chart.properties(
            title=alt.TitleParams(
                "dashed gray = normal for this window", fontSize=11, anchor="end"
            )
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


def plot_rooms(df, rooms, col, shown):
    """One line per shown room, the Pi's own included, with outdoor dashed gray as elsewhere."""
    # 15-minute means of the Pi's minute readings, on the same grid as the room lines.
    pi = df.set_index("date")[col].resample("15min").mean().dropna().reset_index()
    lines = pd.concat([pi.assign(room=PI_ROOM), room_lines(rooms, col)], ignore_index=True)
    lines = lines[lines["room"].isin(shown)]
    label = WEEK_FEATURES[col]
    chart = (
        alt.Chart(lines)
        .mark_line()
        .encode(
            x=alt.X("date:T", axis=alt.Axis(title=None, format=time_axis_format(lines))),
            y=alt.Y(f"{col}:Q", title=label, scale=alt.Scale(zero=False)),
            color=alt.Color("room:N", scale=room_scale(lines["room"]), legend=ROOM_LEGEND),
            tooltip=["date:T", "room:N", alt.Tooltip(f"{col}:Q", format=".1f", title=label)],
        )
    )
    out_col = OUTDOOR_COLUMNS[col]
    if out_col in df.columns and df[out_col].notna().any():
        outdoor = (
            df.set_index("date")[out_col].resample("15min").mean().dropna().reset_index()
        )
        chart += (
            alt.Chart(outdoor)
            .mark_line(color=OUTDOOR_COLOR, strokeDash=[5, 3])
            .encode(
                x="date:T",
                y=alt.Y(f"{out_col}:Q", title=label, scale=alt.Scale(zero=False)),
                tooltip=["date:T", alt.Tooltip(f"{out_col}:Q", format=".1f", title="Outdoor")],
            )
        )
    return chart.properties(
        title=alt.TitleParams(
            "other rooms averaged over 1h, dashed gray = outdoor", fontSize=11, anchor="end"
        )
    )


def show_room_charts(df, cols, shown):
    """Per-room charts for cols; False when there's no Zigbee data to chart."""
    rooms = load_room_readings(df["date"].min(), df["date"].max())
    if rooms.empty:
        return False
    for col in cols:
        st.altair_chart(plot_rooms(df, rooms, col, shown), use_container_width=True)
    return True


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
        axis=alt.Axis(title="time", format=time_axis_format(pm_df)),
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
    /* Streamlit 0.62 predates st.columns, so the metrics are laid out with a CSS
       grid instead. auto-fit fits as many 12rem tracks as the window allows, so
       the cards reflow down to one column on a phone. Grid rather than flexbox
       because a leftover card on the last row keeps its column width here,
       where a flex item would grow to span the whole row on its own. Colours
       are inherited so the grid follows whichever Streamlit theme is active. */
    .metrics {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
        gap: 0.75rem;
        margin-bottom: 1rem;
    }
    .metric {
        padding: 0.6rem 0.9rem;
        border: 1px solid rgba(128, 128, 128, 0.35);
        border-radius: 0.5rem;
    }
    .metric-value {font-size: 1.6rem; font-weight: 600; line-height: 1.3;}
    .metric-label {font-size: 0.9rem; opacity: 0.75;}
    .metric-sub {font-size: 0.8rem; opacity: 0.6;}
    </style>
    """,
    unsafe_allow_html=True,
)


def metric_card(value: str, label: str, *extras: str) -> str:
    subs = "".join(f"<div class='metric-sub'>{extra}</div>" for extra in extras)
    return (
        f"<div class='metric'><div class='metric-value'>{value}</div>"
        f"<div class='metric-label'>{label}</div>{subs}</div>"
    )

# The current metrics.
st.markdown("# Current air quality")
now = pd.Timestamp.now(tz="Europe/Brussels")
# Latest temperature and humidity per Zigbee room. A room silent for over 24h drops
# off the cards; the daily summary has flagged it as silent long before that.
room_readings = load_room_readings(now - pd.Timedelta(days=1), now)
latest_rooms = room_readings.groupby("room").last() if not room_readings.empty else room_readings


def room_extras(col: str, fmt: str, unit: str) -> list[str]:
    extras = []
    for room, reading in latest_rooms.iterrows():
        if pd.notna(reading[col]):
            hours = int((now - reading["date"]) / pd.Timedelta(hours=1))
            stale = f" ({hours}h ago)" if hours >= 2 else ""
            extras.append(f"🏠 {room}: {reading[col]:{fmt}} {unit}{stale}")
    return extras


# With other rooms on the cards, say which room the big numbers are from.
where = f"  📍 {PI_ROOM}" if not latest_rooms.empty else ""
st.text(f"🗓 {last_record['date'].strftime('%d-%m-%Y %H:%M')}{where}")
age = now - last_record["date"]
if age > pd.Timedelta(minutes=10):
    st.warning(
        f"⚠️ Last measurement is {int(age.total_seconds() // 60)} minutes old "
        "— the monitor may be down."
    )
# room = st.text_input("Room", on_change=set_room)
co2_alert = "🚨" if last_record["co2"] > 900 else ""
cards = [metric_card(f"{last_record['co2']:.0f} ppm {co2_alert}", "😶‍🌫️ CO2 level")]

temp_extras = room_extras("temp", ".1f", "°C")
if "out_temp" in last_record and pd.notna(last_record["out_temp"]):
    temp_extras.append(f"🌳 Outdoor: {last_record['out_temp']:.1f} °C")
last_24h = load_last_days(1)
day_temps = last_24h.dropna(subset=["temp"])
if not day_temps.empty:
    tmin = day_temps.loc[day_temps["temp"].idxmin()]
    tmax = day_temps.loc[day_temps["temp"].idxmax()]
    temp_extras.append(f"↓ {tmin['temp']:.1f} °C at {tmin['date'].strftime('%H:%M')} (last 24h)")
    temp_extras.append(f"↑ {tmax['temp']:.1f} °C at {tmax['date'].strftime('%H:%M')} (last 24h)")
cards.append(metric_card(f"{last_record['temp']:.1f} °C", "🌡 Temperature", *temp_extras))

hum_extras = room_extras("hum", ".0f", "%")
if "out_hum" in last_record and pd.notna(last_record["out_hum"]):
    hum_extras.append(f"🌳 Outdoor: {last_record['out_hum']:.0f} %")
cards.append(metric_card(f"{last_record['hum']:.0f} %", "💧 Humidity", *hum_extras))

if "pm25" in last_record and pd.notna(last_record["pm25"]):
    pm_alert = "🚨" if last_record["pm25"] > 15 else ""
    cards.append(metric_card(f"{last_record['pm25']:.1f} µg/m³ {pm_alert}", "🌫 PM2.5"))
if "voc" in last_record and pd.notna(last_record["voc"]):
    # The ppb figure is not comparable to any guideline or to another device, so
    # it is judged against this room's own recent history instead. See
    # baseline_deviation() for why.
    voc = last_record["voc"]
    voc_alert, voc_extras = "", []
    deviation = baseline_deviation(last_24h["voc"].tolist(), voc)
    if deviation is not None:
        voc_baseline, voc_z = deviation
        voc_extras.append(f"📉 24h normal: {voc_baseline:.0f} ppb")
        if voc_z >= VOC_SPIKE_DEVIATION:
            voc_alert = "🚨"
            # A zero baseline is what a still-conditioning sensor looks like,
            # and there is no meaningful multiple of zero to quote.
            voc_extras.append(
                f"↑ {voc / voc_baseline:.1f}× the 24h normal"
                if voc_baseline > 0
                else "↑ well above the 24h normal"
            )
    else:
        voc_extras.append("📉 building up 24h baseline")
    cards.append(metric_card(f"{voc:.0f} ppb {voc_alert}", "🧪 TVOC", *voc_extras))

st.markdown(f"<div class='metrics'>{''.join(cards)}</div>", unsafe_allow_html=True)

# Evolution over time.
st.markdown("# Air quality evolution")
date = st.date_input("Day of interest", datetime.datetime.now())
# One room filter for the temperature and humidity charts here and in the 7 days below.
shown_rooms = st.multiselect("Rooms", ROOMS, default=ROOMS) if len(ROOMS) > 1 else ROOMS
# Today is a rolling 24h window instead of a stub of a day; past days stay whole.
is_today = date == datetime.date.today()
filtered_df = last_24h if is_today else load_day(date)
if is_today:
    st.text("Showing the last 24 hours.")

if filtered_df.empty:
    st.info("No measurements recorded for the selected day yet.")
else:
    st.altair_chart(plot_metric_over_time(filtered_df, "co2"), use_container_width=True)
    # The per-room charts already include this room's line and outdoor, so they
    # replace the single-room ones whenever there's Zigbee data for the window.
    if not show_room_charts(filtered_df, ["temp", "hum"], shown_rooms):
        for col in ("temp", "hum"):
            st.altair_chart(plot_metric_over_time(filtered_df, col), use_container_width=True)
    st.altair_chart(
        plot_metric_over_time(filtered_df, "pressure"), use_container_width=True
    )
    # Only charted once the CCS811 has logged something; older days have no TVOC.
    if "voc" in filtered_df.columns and filtered_df["voc"].notna().any():
        # Baselined over the window on screen, not the rolling 24h the card uses,
        # so the line still means "normal here" when looking back at a past day.
        st.altair_chart(
            plot_metric_over_time(
                filtered_df, "voc", baseline=filtered_df["voc"].median()
            ),
            use_container_width=True,
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
    # The room filter only applies where there are rooms to compare.
    week_rooms, week_shown = None, ROOMS
    if feature in ("temp", "hum"):
        week_rooms = load_room_readings(week_df["date"].min(), week_df["date"].max())
        week_shown = shown_rooms
    week_chart = plot_week_overview(
        week_df, feature, WEEK_FEATURES[feature], week_rooms, week_shown
    )
    if week_chart is None:
        st.info("No measurements for this feature in the last 7 days.")
    else:
        caption = f"Shaded bands are nights, {NIGHT_START}:00-0{NIGHT_END}:00"
        if feature in OUTDOOR_COLUMNS:
            caption += " — dashed gray line is outdoor"
        if week_rooms is not None and not week_rooms.empty:
            caption += "\nOther rooms are averaged over 1h"
        st.text(caption)
        st.altair_chart(week_chart, use_container_width=True)

# Data export.
st.markdown("### Export measurements")
if st.button("⬇️ Prepare complete CSV download"):
    link = full_history_download_link()
    if link is None:
        st.info("No data available yet to download.")
    else:
        st.markdown(link, unsafe_allow_html=True)
else:
    st.write("Press the button to prepare the download link.")

# Raspberry Pi shutdown button.
st.markdown("### Shutdown Raspberry Pi")
if st.checkbox("I really want to shut down the Pi"):
    if st.button("⚠️ Shutdown Raspberry Pi"):
        call("sudo shutdown -h now", shell=True)
