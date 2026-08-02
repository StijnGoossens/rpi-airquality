"""Daily 07:00 air-quality summary pushed to ntfy.

The weather line and the stat footer are unconditional -- everything else appears only
when it has something worth saying, so a quiet morning is three lines and the absence of
a block is itself the signal. The footer exists so "no alert" is provable rather than
indistinguishable from a dead script.

Run from cron:  0 7 * * * cd ~/Documents/rpi-airquality/src && python3 summary.py
"""

import datetime
import json
import sqlite3
import time
import urllib.request

from config import DB_PATH, LATITUDE, LONGITUDE
from utils import send_notification

# CO2 is the Belgian indoor-air target value and sits in the EN 16798-1 Cat I band; it
# reads as a ventilation proxy, not a toxicity limit. PM follows the WHO 2021 guidelines,
# which are defined as 24h means -- the peak trigger is a "a source is burning right now"
# detector, set at the EPA AQI-100 boundary, and deliberately not a health threshold.
CO2_WARN = 900
PM25_PEAK = 35
PM25_24H = 15
PM10_24H = 45
# Don't advise airing out into outdoor air that is itself over the WHO 24h guideline.
OUTDOOR_PM25_MAX = 15
# Indoor must beat outdoor by this much before airing out is worth the bother.
VENT_MIN_GAP = 2.0

NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 7
GAP_MINUTES = 30
# Mirrors monitor.POLL_FREQUENCY_SECONDS; not imported because monitor.py pulls in the
# sensor libraries at module level and this script has to run without them.
POLL_MINUTES = 5

FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LATITUDE}&longitude={LONGITUDE}"
    "&current=temperature_2m"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "precipitation_probability_max,wind_speed_10m_max,wind_direction_10m_dominant"
    "&hourly=temperature_2m&timezone=Europe%2FBrussels&forecast_days=1"
)
AIR_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
    f"?latitude={LATITUDE}&longitude={LONGITUDE}&current=pm2_5&timezone=Europe%2FBrussels"
)

WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "fog", 51: "drizzly", 53: "drizzly", 55: "drizzly",
    61: "rainy", 63: "rainy", 65: "heavy rain", 71: "snow", 73: "snow", 75: "snow",
    77: "snow", 80: "showers", 81: "showers", 82: "heavy showers",
    95: "thunderstorms", 96: "thunderstorms", 99: "thunderstorms",
}
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass(degrees):
    return COMPASS[round(degrees / 45) % 8]


def _query(sql, params=()):
    # Read-only so a summary run can never contend with monitor.py's writes.
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as con:
        return con.execute(sql, params).fetchall()


def _get_json(url, attempts=3, retry_delay_seconds=5):
    # Retry like monitor._fetch_current does: Open-Meteo returns the odd 503, and losing
    # the forecast costs the summary the one line that makes it worth opening daily.
    last_exc = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return json.load(response)
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(retry_delay_seconds)
    print("Failed to fetch forecast:", last_exc)
    return None


def _stamp(value):
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _peak(rows, column):
    """Largest value in `column` and when it happened, ignoring missing readings."""
    seen = [(row[column], row[0]) for row in rows if row[column] is not None]
    if not seen:
        return None, None
    value, when = max(seen)
    return value, datetime.datetime.fromisoformat(when).strftime("%H:%M")


def _mean(rows, column):
    seen = [row[column] for row in rows if row[column] is not None]
    return sum(seen) / len(seen) if seen else None


def collect(now=None):
    now = now or datetime.datetime.now()
    night_start = (now - datetime.timedelta(days=1)).replace(
        hour=NIGHT_START_HOUR, minute=0, second=0, microsecond=0
    )
    # Cap the window at the send time so running this by hand at midday still reports on
    # last night instead of on the day so far.
    night_end = min(
        now, now.replace(hour=NIGHT_END_HOUR, minute=0, second=0, microsecond=0)
    )
    # Stored dates are naive local ISO strings, so lexicographic >= works (as in dashboard.py).
    night = _query(
        "SELECT date, co2 FROM records WHERE date >= ? AND date <= ? ORDER BY date",
        (_stamp(night_start), _stamp(night_end)),
    )
    day = _query(
        "SELECT date, pm25, pm10 FROM records WHERE date >= ? ORDER BY date",
        (_stamp(now - datetime.timedelta(hours=24)),),
    )
    latest = _query("SELECT temp FROM records ORDER BY date DESC LIMIT 1")

    co2_peak, co2_peak_at = _peak(night, 1)
    over = sum(1 for _, co2 in night if co2 is not None and co2 > CO2_WARN)
    pm25_peak, pm25_peak_at = _peak(day, 1)

    gaps = []
    for (earlier, _), (later, _) in zip(night, night[1:]):
        a = datetime.datetime.fromisoformat(earlier)
        b = datetime.datetime.fromisoformat(later)
        if b - a > datetime.timedelta(minutes=GAP_MINUTES):
            gaps.append((a.strftime("%H:%M"), b.strftime("%H:%M")))

    forecast = _get_json(FORECAST_URL)
    air = _get_json(AIR_URL)
    indoor_temp = latest[0][0] if latest and latest[0][0] is not None else None
    outdoor_pm25 = (air or {}).get("current", {}).get("pm2_5")

    weather = vent = None
    if forecast:
        daily = {key: values[0] for key, values in forecast["daily"].items() if values}
        weather = {
            "desc": WMO.get(daily.get("weather_code"), "mixed"),
            "tmin": daily.get("temperature_2m_min"),
            "tmax": daily.get("temperature_2m_max"),
            "precip": daily.get("precipitation_sum") or 0,
            "precip_prob": daily.get("precipitation_probability_max") or 0,
            "wind": daily.get("wind_speed_10m_max"),
            "wind_dir": daily.get("wind_direction_10m_dominant") or 0,
        }
        outdoor_now = forecast.get("current", {}).get("temperature_2m")
        clean_enough = outdoor_pm25 is None or outdoor_pm25 <= OUTDOOR_PM25_MAX
        worth_it = (
            indoor_temp is not None
            and outdoor_now is not None
            and outdoor_now < indoor_temp - VENT_MIN_GAP
            and weather["tmax"] is not None
            and weather["tmax"] > indoor_temp
        )
        if worth_it and clean_enough:
            vent = {
                "out": outdoor_now,
                "in": indoor_temp,
                "shut_by": _shut_by(forecast["hourly"], indoor_temp, now),
            }

    return {
        "weather": weather,
        "vent": vent,
        "outdoor_pm25": outdoor_pm25,
        "indoor_temp": indoor_temp,
        "co2_peak": co2_peak,
        "co2_peak_at": co2_peak_at,
        "co2_hours": over * POLL_MINUTES / 60,
        "pm25_peak": pm25_peak,
        "pm25_peak_at": pm25_peak_at,
        "pm25_24h": _mean(day, 1),
        "pm10_24h": _mean(day, 2),
        "gaps": gaps,
        "streak": co2_streak(now),
        "readings": len(night),
    }


def _shut_by(hourly, indoor_temp, now):
    """First hour today when outdoor catches up to indoor -- i.e. close the windows."""
    for stamp, temp in zip(hourly["time"], hourly["temperature_2m"]):
        when = datetime.datetime.fromisoformat(stamp)
        if when > now and temp is not None and temp >= indoor_temp:
            return when.strftime("%H:%M")
    return None


def co2_streak(now, nights=7):
    """Consecutive recent nights whose peak CO2 was over the threshold."""
    rows = _query(
        # Shifting by NIGHT_START_HOUR-hours groups an overnight stretch under the date it
        # started on, so 22:00 and the 05:00 that follows land in the same bucket.
        f"SELECT date(datetime(date, '-{NIGHT_START_HOUR} hours')) AS night, MAX(co2) "
        "FROM records WHERE date >= ? AND co2 IS NOT NULL "
        f"AND (CAST(strftime('%H', date) AS INTEGER) >= {NIGHT_START_HOUR} "
        f"OR CAST(strftime('%H', date) AS INTEGER) < {NIGHT_END_HOUR}) "
        "GROUP BY night ORDER BY night DESC",
        (_stamp(now - datetime.timedelta(days=nights + 1)),),
    )
    streak = 0
    for _, peak in rows:
        if peak is None or peak <= CO2_WARN:
            break
        streak += 1
    return streak


def render(s):
    """Build (title, body, tags). Pure, so the demo below can exercise every branch.

    The title stays plain ASCII because it travels as an HTTP header; the status emoji
    rides along as an ntfy tag shortcode instead.
    """
    lines = []
    problems = []

    if s["weather"]:
        w = s["weather"]
        if w["precip"] >= 0.5:
            rain = f"{w['precip']:.0f} mm rain"
        elif w["precip_prob"] >= 30:
            rain = f"{w['precip_prob']:.0f}% chance of rain"
        else:
            rain = "dry"
        lines.append(
            f"🌤 {w['tmin']:.0f}–{w['tmax']:.0f}°C {w['desc']}, {rain}, "
            f"wind {compass(w['wind_dir'])} {w['wind']:.0f} km/h"
        )

    if s["vent"]:
        v = s["vent"]
        shut = f", shut by {v['shut_by']}" if v["shut_by"] else ""
        lines.append(f"🪟 Air out now: {v['out']:.0f}°C out / {v['in']:.0f}°C in{shut}")
    elif s["outdoor_pm25"] is not None and s["outdoor_pm25"] > OUTDOOR_PM25_MAX:
        lines.append(f"🪟 Keep shut — outdoor PM2.5 is {s['outdoor_pm25']:.0f}")

    if not s["readings"]:
        problems.append("no data")
        lines.append("⚠️ No readings overnight — is monitor.py running?")

    if s["co2_peak"] is not None and s["co2_peak"] > CO2_WARN:
        problems.append("stuffy night")
        lines.append(
            f"😶‍🌫️ CO2 hit {s['co2_peak']:.0f} at {s['co2_peak_at']}, "
            f"{s['co2_hours']:.1f}h above {CO2_WARN}"
        )

    pm = []
    if s["pm25_peak"] is not None and s["pm25_peak"] > PM25_PEAK:
        pm.append(f"peaked {s['pm25_peak']:.0f} at {s['pm25_peak_at']}")
    if s["pm25_24h"] is not None and s["pm25_24h"] > PM25_24H:
        pm.append(f"24h avg {s['pm25_24h']:.0f} (WHO {PM25_24H})")
    if pm:
        problems.append("PM high")
        lines.append("🌫 PM2.5 " + ", ".join(pm))
    if s["pm10_24h"] is not None and s["pm10_24h"] > PM10_24H:
        if "PM high" not in problems:
            problems.append("PM high")
        lines.append(f"🌫 PM10 24h avg {s['pm10_24h']:.0f} (WHO {PM10_24H})")

    for start, end in s["gaps"]:
        if "gap" not in problems:
            problems.append("gap")
        lines.append(f"⚠️ No readings {start}–{end}")

    footer = []
    if s["co2_peak"] is not None:
        footer.append(f"night CO2 max {s['co2_peak']:.0f}")
    if s["pm25_peak"] is not None:
        footer.append(f"PM2.5 max {s['pm25_peak']:.0f}")
    if s["indoor_temp"] is not None:
        footer.append(f"{s['indoor_temp']:.1f}°C in")
    if s["streak"] > 1:
        footer.append(f"{s['streak']} nights in a row over {CO2_WARN}")
    if footer:
        lines.append("· " + " · ".join(footer))

    if problems:
        return "Air: " + ", ".join(problems), "\n".join(lines), "orange_circle"
    return "Air: all clear", "\n".join(lines), "green_circle"


def demo():
    quiet = {
        "weather": {"desc": "partly cloudy", "tmin": 15, "tmax": 24, "precip": 0,
                    "precip_prob": 10, "wind": 14, "wind_dir": 270},
        "vent": {"out": 14, "in": 22, "shut_by": "11:00"},
        "outdoor_pm25": 6, "indoor_temp": 21.8, "co2_peak": 812, "co2_peak_at": "05:10",
        "co2_hours": 0.0, "pm25_peak": 4.2, "pm25_peak_at": "19:00", "pm25_24h": 3.1,
        "pm10_24h": 3.4, "gaps": [], "streak": 0, "readings": 108,
    }
    title, body, tags = render(quiet)
    assert (title, tags) == ("Air: all clear", "green_circle"), (title, tags)
    assert body.count("\n") == 2, body  # weather + ventilation + footer, nothing else
    assert "CO2 hit" not in body and "🌫" not in body, body
    assert "night CO2 max 812" in body and "21.8°C in" in body, body

    bad = dict(quiet, co2_peak=1180, co2_peak_at="05:40", co2_hours=4.2, pm25_peak=87,
               pm25_peak_at="22:10", pm25_24h=19.4, pm10_24h=52.0, streak=3,
               gaps=[("02:00", "04:30")])
    title, body, tags = render(bad)
    assert (title, tags) == ("Air: stuffy night, PM high, gap", "orange_circle"), title
    assert "CO2 hit 1180 at 05:40, 4.2h above 900" in body, body
    assert "peaked 87 at 22:10" in body and "24h avg 19 (WHO 15)" in body, body
    assert "PM10 24h avg 52" in body and "No readings 02:00–04:30" in body, body
    assert "3 nights in a row over 900" in body, body

    # A borderline night stays quiet: PM at the threshold must not trigger.
    edge = dict(quiet, co2_peak=CO2_WARN, pm25_peak=PM25_PEAK, pm25_24h=PM25_24H,
                pm10_24h=PM10_24H)
    assert render(edge)[0] == "Air: all clear", render(edge)

    # No forecast (Open-Meteo 503s often enough) still yields a usable summary.
    offline = dict(quiet, weather=None, vent=None, outdoor_pm25=None)
    assert render(offline)[1].startswith("· night CO2 max"), render(offline)

    # A dead monitor is reported instead of silently looking clean.
    dead = dict(quiet, readings=0, co2_peak=None, pm25_peak=None)
    assert "no data" in render(dead)[0], render(dead)

    # Titles travel as HTTP headers: a non-latin-1 one silently loses the notification.
    for stats in (quiet, bad, edge, offline, dead):
        render(stats)[0].encode("latin-1")
    print("ok")


if __name__ == "__main__":
    import sys

    if "--demo" in sys.argv:
        demo()
    else:
        title, body, tags = render(collect())
        if "--dry-run" in sys.argv:
            print(title, body, sep="\n")
        else:
            send_notification(title, body, priority="default", tags=tags)
