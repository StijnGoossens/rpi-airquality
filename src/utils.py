from datetime import timezone

def utc_to_be(utc_dt):
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(tz="Europe/Brussels")