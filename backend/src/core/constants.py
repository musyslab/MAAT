"""Define shared role identifiers and Chicago-time helpers.

Use the role values when interpreting persisted class assignments. All application
wall-clock timestamps are stored as naive America/Chicago values so database
comparisons and displayed times use one convention across the backend.
"""

from datetime import datetime
from zoneinfo import ZoneInfo


STUDENT_ROLE = 0


TEACHER_ROLE = 1


ADMIN_ROLE = 2


CHICAGO_TIMEZONE = ZoneInfo("America/Chicago")


def chicago_now() -> datetime:
    """Return the current America/Chicago wall time without tzinfo for database use."""
    # Store local wall time without timezone metadata to match the database convention.
    return datetime.now(CHICAGO_TIMEZONE).replace(tzinfo=None)


def to_chicago_datetime(value: datetime) -> datetime:
    """Normalize a datetime to naive America/Chicago wall time."""
    # Handle the case where value.tzinfo is None.
    if value.tzinfo is None:
        return value
    # Convert aware values to Chicago before removing their timezone information.
    return value.astimezone(CHICAGO_TIMEZONE).replace(tzinfo=None)


def chicago_iso(value: datetime | None) -> str | None:
    """Serialize a stored Chicago wall time with the correct UTC offset."""
    # Return an empty or negative result when this guard matches.
    if value is None:
        return None
    local_value = to_chicago_datetime(value)
    # Reattach the Chicago timezone so the serialized value includes its UTC offset.
    return local_value.replace(tzinfo=CHICAGO_TIMEZONE).isoformat()
