import urllib.request
from datetime import timezone
from email.header import Header

from config import NTFY_TOPIC


def utc_to_be(utc_dt):
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(tz="Europe/Brussels")


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
