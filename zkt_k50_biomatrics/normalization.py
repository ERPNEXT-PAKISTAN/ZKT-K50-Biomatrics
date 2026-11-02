"""Framework independent event normalization shared with the edge agent."""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

EVENT_FIELDS = ("user_id", "timestamp", "punch", "uid", "native_id")


def clean_event(event):
    if not isinstance(event, dict):
        raise ValueError("Each attendance event must be an object")
    result = {k: str(event[k]) for k in EVENT_FIELDS if event.get(k) is not None}
    if not result.get("user_id") or len(result["user_id"]) > 140:
        raise ValueError("A user ID of at most 140 characters is required")
    if not result.get("timestamp"):
        raise ValueError("Device timestamp is required")
    if any(len(v) > 200 for v in result.values()):
        raise ValueError("Attendance values must be at most 200 characters")
    # Discard arbitrary metadata and all biometric enrollment data.
    datetime.fromisoformat(result["timestamp"])
    return result


def event_key(device, event):
    event = clean_event(event)
    # pyzk Attendance.uid is a USER UID, never a native attendance record ID.
    # Without a true native ID, identical device/user/second/punch tuples collapse.
    timestamp = datetime.fromisoformat(event["timestamp"]).isoformat(timespec="seconds")
    identity = [str(device), event["user_id"], timestamp, event.get("punch", "")]
    if event.get("native_id"):
        identity.append(event["native_id"])
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()


def normalize_time(value, device_timezone, site_timezone):
    original = datetime.fromisoformat(str(value))
    device_tz = ZoneInfo(device_timezone)
    if original.tzinfo is None:
        # DST overlaps/gaps require explicit review rather than guessing.
        if (
            original.replace(tzinfo=device_tz, fold=0).utcoffset()
            != original.replace(tzinfo=device_tz, fold=1).utcoffset()
        ):
            raise ValueError("Ambiguous or nonexistent local time; supply an explicit UTC offset")
        original = original.replace(tzinfo=device_tz)
    return original.astimezone(ZoneInfo(site_timezone)).replace(tzinfo=None, microsecond=0)


def punch_type(punch, mappings):
    values = {str(row["punch_code"]): row["log_type"] for row in mappings}
    return values.get(str(punch), "Unknown")


def validate_coordinates(latitude, longitude):
    if latitude is None or longitude is None:
        raise ValueError("Configure the physical terminal latitude and longitude")
    lat, lon = float(latitude), float(longitude)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        raise ValueError("Configure valid terminal coordinates; 0,0 is not a terminal location")
    return lat, lon
