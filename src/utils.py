import urllib.request
from datetime import timezone
from email.header import Header
from statistics import median

from config import NTFY_TOPIC

# Below this the spread of the recent history is treated as sensor noise rather
# than as real variation. Without it a very flat baseline shrinks the deviation
# scale to nearly nothing and every small wiggle reads as a spike. In ppb, so it
# is the knob to turn if the TVOC card cries wolf (raise it) or stays silent
# through an obvious cooking spike (lower it).
MAD_FLOOR = 5.0
# Enough samples to have seen a quiet stretch; at 5 minutes apart, an hour.
# Known gap: after the sensor loses power it climbs from ~0 to its real baseline
# over several hours, and that ramp reads as a spike for as long as the window
# still remembers the low start. Rare enough (it survives a monitor restart, only
# a power cut resets it) not to be worth special-casing; raise this to cover the
# ramp if it ever becomes annoying.
MIN_HISTORY = 12


def utc_to_be(utc_dt):
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(tz="Europe/Brussels")


def baseline_deviation(history, current, mad_floor=MAD_FLOOR):
    """Return (baseline, deviation) of `current` against `history`, or None.

    Metal-oxide TVOC sensors continuously re-baseline themselves, so their ppb
    output is an index in disguise: it says nothing absolute, only how this
    reading compares to recent ones. So compare it to recent ones.

    `baseline` is the median of the history and `deviation` is a robust z-score
    (how many median-absolute-deviations `current` sits above it). Median and
    MAD rather than mean and standard deviation because the spikes we want to
    catch would otherwise inflate the very baseline they are measured against.
    Returns None when the history is too short to say what normal looks like.
    """
    values = [v for v in history if v is not None and v == v]  # v == v drops NaN
    if len(values) < MIN_HISTORY:
        return None
    baseline = median(values)
    mad = median([abs(v - baseline) for v in values])
    # 0.6745 is the scaling that makes MAD comparable to a standard deviation.
    return baseline, 0.6745 * (current - baseline) / max(mad, mad_floor)


def send_notification(title, message, priority="high", tags="warning"):
    if not NTFY_TOPIC:
        return
    # HTTP headers are latin-1, so an emoji in the title otherwise kills the whole
    # notification. RFC 2047-encode it instead (ntfy decodes encoded-words); the body is
    # sent as UTF-8 data and needs no such treatment. Prefer plain ASCII titles anyway and
    # carry status emoji in `tags`, which ntfy renders from shortcodes.
    try:
        title.encode("latin-1")
    except UnicodeEncodeError:
        title = Header(title, "utf-8").encode()
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        print("Failed to send notification:", exc)
