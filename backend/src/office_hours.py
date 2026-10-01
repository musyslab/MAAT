"""Manage office-hours sessions and the student help queue.

Start sessions, join or leave queues, select students for help, and complete help
entries. Calculate queue positions and expire sessions or entries when their time
limits pass. Interpret stored naive timestamps using the existing Chicago-time
convention and apply submission-scope permissions to queue operations.

Endpoints use /api/office_hours/<handler_name>."""

from datetime import datetime
from datetime import timedelta
from flask import make_response
from http import HTTPStatus
from src.core.database import db
from src.core.models import Modules
from src.core.models import Projects
from src.core.models import Users
from src.core.models import OfficeHoursQueueEntry
from src.core.models import OfficeHoursSession
from src.submissions import current_user_id
from src.submissions import user_is_student_in_class_id
from src.submissions import OFFICE_HOURS_HELP_MINUTES
from src.submissions import OFFICE_HOURS_SESSION_MINUTES
from src.submissions import OFFICE_HOURS_WAIT_MINUTES
from src.submissions import parse_int
from src.submissions import current_chicago_datetime
from src.submissions import parse_cooldown_lifted_at
from src.submissions import serialize_cooldown_lifted_at
from flask import jsonify
from flask import request
from flask_jwt_extended import jwt_required
from src.core.blueprints import office_hours_api
from src.core.models import ClassAssignments
from src.core.models import Classes
from src.submissions import user_can_access_class_id


# Office-hours sessions, queue expiry, positions, and response serialization.


def active_office_hours_session(class_id: int):
    """Handle active office hours session for this component.

    Inputs: class_id."""
    class_id = parse_int(class_id, 0)
    # Return an empty or negative result when this guard matches.
    if class_id <= 0:
        return None

    now = current_chicago_datetime()
    return (
        OfficeHoursSession.query.filter(
            OfficeHoursSession.ClassId == class_id,
            OfficeHoursSession.StartedAt <= now,
            OfficeHoursSession.EndsAt > now,
        )
        .order_by(
            OfficeHoursSession.StartedAt.desc(),
            OfficeHoursSession.Id.desc(),
        )
        .first()
    )


def office_hours_session_for_time(class_id: int, value: datetime | None):
    """Handle office hours session for time for this component.

    Inputs: class_id, value."""
    class_id = parse_int(class_id, 0)
    at_time = parse_cooldown_lifted_at(value)
    # Return an empty or negative result when this guard matches.
    if class_id <= 0 or at_time is None:
        return None

    return (
        OfficeHoursSession.query.filter(
            OfficeHoursSession.ClassId == class_id,
            OfficeHoursSession.StartedAt <= at_time,
            OfficeHoursSession.EndsAt > at_time,
        )
        .order_by(
            OfficeHoursSession.StartedAt.desc(),
            OfficeHoursSession.Id.desc(),
        )
        .first()
    )


def serialize_office_hours_session(class_id: int) -> dict:
    """Serialize office hours session.

    Inputs: class_id."""
    now = current_chicago_datetime()
    session = active_office_hours_session(class_id)

    # Handle the case where session is None.
    if session is None:
        return {
            "office_hours_active": False,
            "session_started_at": None,
            "session_ends_at": None,
            "session_remaining_seconds": 0,
            "session_duration_minutes": OFFICE_HOURS_SESSION_MINUTES,
        }

    started_at = parse_cooldown_lifted_at(getattr(session, "StartedAt", None))
    ends_at = parse_cooldown_lifted_at(getattr(session, "EndsAt", None))
    return {
        "office_hours_active": True,
        "session_started_at": serialize_cooldown_lifted_at(started_at),
        "session_ends_at": serialize_cooldown_lifted_at(ends_at),
        "session_remaining_seconds": (
            max(0, int((ends_at - now).total_seconds() + 0.999)) if ends_at is not None else 0
        ),
        "session_duration_minutes": OFFICE_HOURS_SESSION_MINUTES,
    }


def office_hours_status_for_row(row: OfficeHoursQueueEntry | None) -> str:
    """Handle office hours status for row for this component.

    Inputs: row."""
    # Handle the case where row is None.
    if row is None:
        return "not_queued"

    now = current_chicago_datetime()
    joined_at = parse_cooldown_lifted_at(getattr(row, "JoinedAt", None))
    selected_at = parse_cooldown_lifted_at(getattr(row, "SelectedAt", None))
    completed_at = parse_cooldown_lifted_at(getattr(row, "CompletedAt", None))
    exempt_until = parse_cooldown_lifted_at(getattr(row, "CooldownExemptUntil", None))
    session = office_hours_session_for_time(int(row.ClassId), joined_at)
    session_ends_at = (
        parse_cooldown_lifted_at(getattr(session, "EndsAt", None)) if session is not None else None
    )

    waiting_expires_at = (
        joined_at + timedelta(minutes=OFFICE_HOURS_WAIT_MINUTES) if joined_at is not None else None
    )
    if (
        waiting_expires_at is not None
        and session_ends_at is not None
        and session_ends_at < waiting_expires_at
    ):
        waiting_expires_at = session_ends_at

    help_expires_at = exempt_until or (
        selected_at + timedelta(minutes=OFFICE_HOURS_HELP_MINUTES)
        if selected_at is not None
        else None
    )
    if (
        help_expires_at is not None
        and session_ends_at is not None
        and session_ends_at < help_expires_at
    ):
        help_expires_at = session_ends_at

    if completed_at is not None:
        # Handle the case where selected_at is not None.
        if selected_at is not None:
            return "helped"

        # Handle the case where waiting_expires_at is not None and completed_at >= waiting_expires_at.
        if waiting_expires_at is not None and completed_at >= waiting_expires_at:
            return "expired_waiting"

        return "left_queue"

    # Handle the case where selected_at is not None.
    if selected_at is not None:
        return (
            "being_helped" if help_expires_at is not None and help_expires_at > now else "expired"
        )

    # Handle the case where waiting_expires_at is not None and waiting_expires_at <= now.
    if waiting_expires_at is not None and waiting_expires_at <= now:
        return "expired"

    return "waiting"


def expire_office_hours_entries(class_id: int | None = None) -> int:
    """Close queue/help rows when their own timer or the class session expires."""
    now = current_chicago_datetime()
    # Execute the database lookup with the filters specified below.
    query = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.CompletedAt.is_(None),
    )

    if class_id is not None:
        query = query.filter(OfficeHoursQueueEntry.ClassId == int(class_id))

    # Execute the database lookup with the filters specified below.
    rows = query.all()
    # Handle the case where not rows.
    if not rows:
        return 0
    joined_times = [parse_cooldown_lifted_at(row.JoinedAt) for row in rows]
    valid_times = [value for value in joined_times if value is not None]
    sessions_by_class = {}
    if valid_times:
        # Execute the database lookup with the filters specified below.
        sessions = OfficeHoursSession.query.filter(
            OfficeHoursSession.ClassId.in_({int(row.ClassId) for row in rows if int(row.ClassId) > 0}),
            OfficeHoursSession.StartedAt <= max(valid_times),
            OfficeHoursSession.EndsAt > min(valid_times),
        ).order_by(OfficeHoursSession.StartedAt.desc(), OfficeHoursSession.Id.desc()).all()
        # Process each session from sessions.
        for session in sessions:
            sessions_by_class.setdefault(int(session.ClassId), []).append(session)
    expired_rows = []

    # Process each (row, joined_at) from zip(rows, joined_times).
    for row, joined_at in zip(rows, joined_times):
        selected_at = parse_cooldown_lifted_at(getattr(row, "SelectedAt", None))
        session = next(
            (session for session in sessions_by_class.get(int(row.ClassId), ())
             if joined_at is not None and session.StartedAt <= joined_at < session.EndsAt),
            None,
        )
        session_ends_at = (
            parse_cooldown_lifted_at(getattr(session, "EndsAt", None))
            if session is not None
            else None
        )

        if selected_at is not None:
            expires_at = parse_cooldown_lifted_at(getattr(row, "CooldownExemptUntil", None)) or (
                selected_at + timedelta(minutes=OFFICE_HOURS_HELP_MINUTES)
            )
        else:
            if joined_at is None:
                continue
            expires_at = joined_at + timedelta(minutes=OFFICE_HOURS_WAIT_MINUTES)

        if session_ends_at is not None and session_ends_at < expires_at:
            expires_at = session_ends_at

        if session is None:
            # Close entries whose session no longer exists.
            expires_at = min(expires_at, now)

        if expires_at <= now:
            row.CompletedAt = expires_at
            if selected_at is not None:
                row.CooldownExemptUntil = expires_at
            expired_rows.append(row)

    if expired_rows:
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    return len(expired_rows)


def active_office_hours_entry_for_project(
    user_id: int,
    class_id: int,
    project_id: int,
):
    """Return an active help entry only while the class office-hours window is active."""
    user_id = parse_int(user_id, 0)
    class_id = parse_int(class_id, 0)
    project_id = parse_int(project_id, 0)

    # Return an empty or negative result when this guard matches.
    if user_id <= 0 or class_id <= 0 or project_id <= 0:
        return None

    session = active_office_hours_session(class_id)
    if session is None:
        expire_office_hours_entries(class_id)
        return None

    # Execute the database lookup with the filters specified below.
    project = Projects.query.filter(
        Projects.Id == project_id,
        Projects.ClassId == class_id,
    ).first()
    module_id = parse_int(getattr(project, "ModuleId", 0), 0)
    # Return an empty or negative result when this guard matches.
    if module_id <= 0:
        return None

    expire_office_hours_entries(class_id)
    now = current_chicago_datetime()
    session_ends_at = parse_cooldown_lifted_at(getattr(session, "EndsAt", None))
    return (
        OfficeHoursQueueEntry.query.filter(
            OfficeHoursQueueEntry.UserId == user_id,
            OfficeHoursQueueEntry.ClassId == class_id,
            OfficeHoursQueueEntry.ModuleId == module_id,
            OfficeHoursQueueEntry.CompletedAt.is_(None),
            OfficeHoursQueueEntry.SelectedAt.isnot(None),
            OfficeHoursQueueEntry.CooldownExemptUntil > now,
            OfficeHoursQueueEntry.JoinedAt >= session.StartedAt,
            OfficeHoursQueueEntry.JoinedAt < session.EndsAt,
        )
        .order_by(
            OfficeHoursQueueEntry.SelectedAt.desc(),
            OfficeHoursQueueEntry.Id.desc(),
        )
        .first()
        if session_ends_at is not None
        else None
    )


def office_hours_queue_position(row: OfficeHoursQueueEntry) -> int | None:
    """Handle office hours queue position for this component.

    Inputs: row."""
    # Return an empty or negative result when this guard matches.
    if office_hours_status_for_row(row) != "waiting":
        return None

    # Execute the database lookup with the filters specified below.
    waiting_rows = (
        OfficeHoursQueueEntry.query.filter(
            OfficeHoursQueueEntry.ClassId == int(row.ClassId),
            OfficeHoursQueueEntry.CompletedAt.is_(None),
            OfficeHoursQueueEntry.SelectedAt.is_(None),
        )
        .order_by(
            OfficeHoursQueueEntry.JoinedAt.asc(),
            OfficeHoursQueueEntry.Id.asc(),
        )
        .all()
    )

    # Process each (index, waiting_row) from enumerate(waiting_rows, start=1).
    for index, waiting_row in enumerate(waiting_rows, start=1):
        # Handle the case where int(waiting_row.Id) == int(row.Id).
        if int(waiting_row.Id) == int(row.Id):
            return index

    return None


def serialize_office_hours_entry(
    row: OfficeHoursQueueEntry,
    *,
    include_student: bool = False,
) -> dict:
    """Serialize office hours entry.

    Inputs: row, include_student."""
    now = current_chicago_datetime()
    status = office_hours_status_for_row(row)
    # Execute the database lookup with the filters specified below.
    module = Modules.query.filter(Modules.Id == int(row.ModuleId)).first()
    joined_at = parse_cooldown_lifted_at(getattr(row, "JoinedAt", None))
    selected_at = parse_cooldown_lifted_at(getattr(row, "SelectedAt", None))
    exempt_until = parse_cooldown_lifted_at(getattr(row, "CooldownExemptUntil", None))
    session = office_hours_session_for_time(int(row.ClassId), joined_at)
    session_ends_at = (
        parse_cooldown_lifted_at(getattr(session, "EndsAt", None)) if session is not None else None
    )
    waiting_expires_at = (
        joined_at + timedelta(minutes=OFFICE_HOURS_WAIT_MINUTES) if joined_at is not None else None
    )
    if (
        waiting_expires_at is not None
        and session_ends_at is not None
        and session_ends_at < waiting_expires_at
    ):
        waiting_expires_at = session_ends_at

    help_expires_at = exempt_until or (
        selected_at + timedelta(minutes=OFFICE_HOURS_HELP_MINUTES)
        if selected_at is not None
        else None
    )
    if (
        help_expires_at is not None
        and session_ends_at is not None
        and session_ends_at < help_expires_at
    ):
        help_expires_at = session_ends_at

    payload = {
        "id": int(row.Id),
        "status": status,
        "in_queue": status in ("waiting", "being_helped"),
        "user_id": int(row.UserId),
        "class_id": int(row.ClassId),
        "module_id": int(row.ModuleId),
        "module_name": str(getattr(module, "Name", "") or ""),
        "joined_at": serialize_cooldown_lifted_at(joined_at),
        "waiting_expires_at": serialize_cooldown_lifted_at(waiting_expires_at),
        "selected_at": serialize_cooldown_lifted_at(selected_at),
        "help_expires_at": serialize_cooldown_lifted_at(help_expires_at),
        "completed_at": serialize_cooldown_lifted_at(
            parse_cooldown_lifted_at(getattr(row, "CompletedAt", None))
        ),
        "cooldown_exempt": status == "being_helped",
        "cooldown_exempt_until": serialize_cooldown_lifted_at(exempt_until),
        "help_remaining_seconds": (
            max(0, int((help_expires_at - now).total_seconds() + 0.999))
            if status == "being_helped" and help_expires_at is not None
            else 0
        ),
        "queue_position": office_hours_queue_position(row),
    }

    if include_student:
        # Execute the database lookup with the filters specified below.
        student = Users.query.filter(Users.Id == int(row.UserId)).first()
        payload.update(
            {
                "first_name": str(getattr(student, "Firstname", "") or ""),
                "last_name": str(getattr(student, "Lastname", "") or ""),
                "email": str(getattr(student, "Email", "") or ""),
                "student_number": str(getattr(student, "StudentNumber", "") or ""),
            }
        )

    return payload


def empty_office_hours_status() -> dict:
    """Handle empty office hours status for this component."""
    return {
        "status": "not_queued",
        "in_queue": False,
        "waiting_expires_at": None,
        "help_expires_at": None,
        "cooldown_exempt": False,
        "cooldown_exempt_until": None,
        "help_remaining_seconds": 0,
        "queue_position": None,
    }


def validate_student_office_hours_scope(class_id: int, module_id: int):
    """Validate student office hours scope.

    Inputs: class_id, module_id."""
    # Return the response below when this validation or access check matches.
    if class_id <= 0 or module_id <= 0:
        return None, make_response(
            {"message": "class_id and module_id are required."},
            HTTPStatus.BAD_REQUEST,
        )

    # Return the response below when this validation or access check matches.
    if not user_is_student_in_class_id(current_user_id(), class_id):
        return None, make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    # Execute the database lookup with the filters specified below.
    module = Modules.query.filter(
        Modules.Id == int(module_id),
        Modules.ClassId == int(class_id),
    ).first()
    # Return the response below when this validation or access check matches.
    if module is None:
        return None, make_response(
            {"message": "Module not found for this class."},
            HTTPStatus.NOT_FOUND,
        )

    return module, None


def office_hours_admin_queue_payload(class_id: int) -> dict:
    """Handle office hours admin queue payload for this component.

    Inputs: class_id."""
    expire_office_hours_entries(class_id)

    # Execute the database lookup with the filters specified below.
    active_rows = (
        OfficeHoursQueueEntry.query.filter(
            OfficeHoursQueueEntry.ClassId == int(class_id),
            OfficeHoursQueueEntry.CompletedAt.is_(None),
        )
        .order_by(
            OfficeHoursQueueEntry.JoinedAt.asc(),
            OfficeHoursQueueEntry.Id.asc(),
        )
        .all()
    )

    active_entries = [
        serialize_office_hours_entry(row, include_student=True)
        for row in active_rows
        if office_hours_status_for_row(row) in ("waiting", "being_helped")
    ]
    waiting = [entry for entry in active_entries if entry["status"] == "waiting"]
    helping = [entry for entry in active_entries if entry["status"] == "being_helped"]

    # Execute the database lookup with the filters specified below.
    completed_rows = (
        OfficeHoursQueueEntry.query.filter(
            OfficeHoursQueueEntry.ClassId == int(class_id),
            OfficeHoursQueueEntry.CompletedAt.isnot(None),
        )
        .order_by(
            OfficeHoursQueueEntry.CompletedAt.desc(),
            OfficeHoursQueueEntry.Id.desc(),
        )
        .all()
    )
    completed_entries = [
        serialize_office_hours_entry(row, include_student=True) for row in completed_rows
    ]
    helped = [
        entry for entry in completed_entries if entry["status"] in ("helped", "expired_waiting")
    ]

    return {
        "class_id": int(class_id),
        **serialize_office_hours_session(class_id),
        "waiting": waiting,
        "helping": helping,
        "helped": helped,
        "waiting_count": len(waiting),
        "helping_count": len(helping),
        "helped_count": len(helped),
        "server_time": serialize_cooldown_lifted_at(current_chicago_datetime()),
        "wait_duration_minutes": OFFICE_HOURS_WAIT_MINUTES,
        "help_duration_minutes": OFFICE_HOURS_HELP_MINUTES,
    }

# Submissions HTTP endpoints for office hours.


@office_hours_api.route('/office_hours_start_session', methods=["POST"])
@jwt_required()
def office_hours_start_session():
    """Handle office hours start session for this component.

    HTTP: POST /api/office_hours/office_hours_start_session.
    Database changes are committed at the explicit transaction boundaries below."""
    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)

    # Return the response below when this validation or access check matches.
    if class_id <= 0:
        return make_response(
            {"message": "class_id is required."},
            HTTPStatus.BAD_REQUEST,
        )
    # Return the response below when this validation or access check matches.
    if not user_can_access_class_id(class_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    now = current_chicago_datetime()

    # Serialize starts per class so two admins cannot create overlapping windows.
    Classes.query.filter(Classes.Id == class_id).with_for_update().first()
    # Execute the database lookup with the filters specified below.
    existing = (
        OfficeHoursSession.query.filter(
            OfficeHoursSession.ClassId == class_id,
            OfficeHoursSession.StartedAt <= now,
            OfficeHoursSession.EndsAt > now,
        )
        .order_by(
            OfficeHoursSession.StartedAt.desc(),
            OfficeHoursSession.Id.desc(),
        )
        .with_for_update()
        .first()
    )

    if existing is not None:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        payload = office_hours_admin_queue_payload(class_id)
        payload["message"] = "Office hours are already active for this class."
        return jsonify(payload)

    # Close active queue rows before opening a new window.
    stale_rows = (
        OfficeHoursQueueEntry.query.filter(
            OfficeHoursQueueEntry.ClassId == class_id,
            OfficeHoursQueueEntry.CompletedAt.is_(None),
        )
        .with_for_update()
        .all()
    )
    # Process each row from stale_rows.
    for row in stale_rows:
        row.CompletedAt = now
        if getattr(row, "CooldownExemptUntil", None) is not None and row.CooldownExemptUntil > now:
            row.CooldownExemptUntil = now

    session = OfficeHoursSession(
        ClassId=class_id,
        StartedAt=now,
        EndsAt=now + timedelta(minutes=OFFICE_HOURS_SESSION_MINUTES),
        StartedByUserId=current_user_id(),
    )
    # Stage the new records in the current database transaction.
    db.session.add(session)
    # Commit the pending database changes so they persist beyond this request.
    db.session.commit()

    payload = office_hours_admin_queue_payload(class_id)
    payload["message"] = (
        f"Office hours started. Students can join for the next "
        f"{OFFICE_HOURS_SESSION_MINUTES // 60} hours."
    )
    return make_response(jsonify(payload), HTTPStatus.CREATED)


@office_hours_api.route('/office_hours_student_status', methods=["GET"])
@jwt_required()
def office_hours_student_status():
    """Handle office hours student status for this component.

    HTTP: GET /api/office_hours/office_hours_student_status."""
    class_id = parse_int(request.args.get("class_id", 0), 0)
    module_id = parse_int(request.args.get("module_id", 0), 0)
    _, error = validate_student_office_hours_scope(class_id, module_id)
    # Handle the case where error is not None.
    if error is not None:
        return error

    expire_office_hours_entries(class_id)
    # Execute the database lookup with the filters specified below.
    row = (
        OfficeHoursQueueEntry.query.filter(
            OfficeHoursQueueEntry.UserId == current_user_id(),
            OfficeHoursQueueEntry.ClassId == class_id,
            OfficeHoursQueueEntry.CompletedAt.is_(None),
        )
        .order_by(
            OfficeHoursQueueEntry.JoinedAt.desc(),
            OfficeHoursQueueEntry.Id.desc(),
        )
        .first()
    )

    payload = serialize_office_hours_entry(row) if row is not None else empty_office_hours_status()
    payload.update(serialize_office_hours_session(class_id))
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@office_hours_api.route('/office_hours_queue', methods=["GET", "POST", "DELETE"])
@jwt_required()
def office_hours_queue():
    """Handle office hours queue for this component.

    HTTP: GET, POST, DELETE /api/office_hours/office_hours_queue.
    Database changes are committed at the explicit transaction boundaries below."""
    if request.method == "GET":
        class_id = parse_int(request.args.get("class_id", 0), 0)
        # Return the response below when this validation or access check matches.
        if class_id <= 0:
            return make_response(
                {"message": "class_id is required."},
                HTTPStatus.BAD_REQUEST,
            )
        # Return the response below when this validation or access check matches.
        if not user_can_access_class_id(class_id):
            return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

        response = jsonify(office_hours_admin_queue_payload(class_id))
        response.headers["Cache-Control"] = "no-store"
        return response

    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)
    module_id = parse_int(data.get("module_id", 0), 0)
    _, error = validate_student_office_hours_scope(class_id, module_id)
    # Handle the case where error is not None.
    if error is not None:
        return error

    expire_office_hours_entries(class_id)
    user_id = current_user_id()

    if request.method == "POST":
        session = active_office_hours_session(class_id)
        if session is None:
            payload = empty_office_hours_status()
            payload.update(serialize_office_hours_session(class_id))
            payload["message"] = "Office hours are not active for this class."
            return make_response(jsonify(payload), HTTPStatus.CONFLICT)

    # Lock the student's class enrollment so simultaneous join requests cannot
    # create two active office-hours visits for the same student and class.
    ClassAssignments.query.filter(
        ClassAssignments.UserId == user_id,
        ClassAssignments.ClassId == class_id,
    ).with_for_update().first()

    # Execute the database lookup with the filters specified below.
    row = (
        OfficeHoursQueueEntry.query.filter(
            OfficeHoursQueueEntry.UserId == user_id,
            OfficeHoursQueueEntry.ClassId == class_id,
            OfficeHoursQueueEntry.CompletedAt.is_(None),
        )
        .order_by(
            OfficeHoursQueueEntry.JoinedAt.desc(),
            OfficeHoursQueueEntry.Id.desc(),
        )
        .with_for_update()
        .first()
    )
    now = current_chicago_datetime()

    if request.method == "DELETE":
        if row is not None:
            row.CompletedAt = now
            if (
                getattr(row, "CooldownExemptUntil", None) is not None
                and row.CooldownExemptUntil > now
            ):
                row.CooldownExemptUntil = now
            # Commit the pending database changes so they persist beyond this request.
            db.session.commit()
        else:
            # Undo pending database changes after the operation fails.
            db.session.rollback()

        payload = empty_office_hours_status()
        payload.update(serialize_office_hours_session(class_id))
        return jsonify(payload)

    if row is not None and office_hours_status_for_row(row) in ("waiting", "being_helped"):
        if int(row.ModuleId) != module_id:
            # Undo pending database changes after the operation fails.
            db.session.rollback()
            return make_response(
                {
                    "message": (
                        "You are already in the office-hours queue for another module. "
                        "Leave that queue before joining this one."
                    ),
                    **serialize_office_hours_entry(row),
                },
                HTTPStatus.CONFLICT,
            )

        # Undo pending database changes after the operation fails.
        db.session.rollback()
        payload = serialize_office_hours_entry(row)
        payload.update(serialize_office_hours_session(class_id))
        return jsonify(payload)

    row = OfficeHoursQueueEntry(
        UserId=user_id,
        ClassId=class_id,
        ModuleId=module_id,
        JoinedAt=now,
    )
    # Stage the new records in the current database transaction.
    db.session.add(row)
    # Commit the pending database changes so they persist beyond this request.
    db.session.commit()

    payload = serialize_office_hours_entry(row)
    payload.update(serialize_office_hours_session(class_id))
    return make_response(
        jsonify(payload),
        HTTPStatus.CREATED,
    )


@office_hours_api.route('/office_hours_help_student', methods=["POST"])
@jwt_required()
def office_hours_help_student():
    """Handle office hours help student for this component.

    HTTP: POST /api/office_hours/office_hours_help_student.
    Database changes are committed at the explicit transaction boundaries below."""
    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)
    student_id = parse_int(data.get("student_id", 0), 0)

    # Return the response below when this validation or access check matches.
    if class_id <= 0 or student_id <= 0:
        return make_response(
            {"message": "class_id and student_id are required."},
            HTTPStatus.BAD_REQUEST,
        )
    # Return the response below when this validation or access check matches.
    if not user_can_access_class_id(class_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)
    # Return the response below when this validation or access check matches.
    if not user_is_student_in_class_id(student_id, class_id):
        return make_response(
            {"message": "Student is not enrolled in this class."},
            HTTPStatus.BAD_REQUEST,
        )

    expire_office_hours_entries(class_id)
    session = active_office_hours_session(class_id)
    # Return the response below when this validation or access check matches.
    if session is None:
        return make_response(
            {"message": "Office hours are not active for this class."},
            HTTPStatus.CONFLICT,
        )

    # Execute the database lookup with the filters specified below.
    row = (
        OfficeHoursQueueEntry.query.filter(
            OfficeHoursQueueEntry.ClassId == class_id,
            OfficeHoursQueueEntry.UserId == student_id,
            OfficeHoursQueueEntry.CompletedAt.is_(None),
            OfficeHoursQueueEntry.SelectedAt.is_(None),
        )
        .order_by(
            OfficeHoursQueueEntry.JoinedAt.desc(),
            OfficeHoursQueueEntry.Id.desc(),
        )
        .with_for_update()
        .first()
    )

    if row is None:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        return make_response(
            {"message": "Student is no longer waiting for office hours."},
            HTTPStatus.NOT_FOUND,
        )

    now = current_chicago_datetime()
    row.SelectedAt = now
    row.SelectedByUserId = current_user_id()
    session_ends_at = parse_cooldown_lifted_at(getattr(session, "EndsAt", None))
    requested_exemption_end = now + timedelta(minutes=OFFICE_HOURS_HELP_MINUTES)
    row.CooldownExemptUntil = (
        min(requested_exemption_end, session_ends_at)
        if session_ends_at is not None
        else requested_exemption_end
    )
    # Commit the pending database changes so they persist beyond this request.
    db.session.commit()

    payload = office_hours_admin_queue_payload(class_id)
    payload["message"] = (
        f"Started helping the student. Cooldown-free submissions are enabled "
        f"for up to {OFFICE_HOURS_HELP_MINUTES} minutes."
    )
    return jsonify(payload)


@office_hours_api.route('/office_hours_complete_student', methods=["POST"])
@jwt_required()
def office_hours_complete_student():
    """Handle office hours complete student for this component.

    HTTP: POST /api/office_hours/office_hours_complete_student.
    Database changes are committed at the explicit transaction boundaries below."""
    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)
    student_id = parse_int(data.get("student_id", 0), 0)

    # Return the response below when this validation or access check matches.
    if class_id <= 0 or student_id <= 0:
        return make_response(
            {"message": "class_id and student_id are required."},
            HTTPStatus.BAD_REQUEST,
        )
    # Return the response below when this validation or access check matches.
    if not user_can_access_class_id(class_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    expire_office_hours_entries(class_id)
    # Execute the database lookup with the filters specified below.
    row = (
        OfficeHoursQueueEntry.query.filter(
            OfficeHoursQueueEntry.ClassId == class_id,
            OfficeHoursQueueEntry.UserId == student_id,
            OfficeHoursQueueEntry.CompletedAt.is_(None),
            OfficeHoursQueueEntry.SelectedAt.isnot(None),
        )
        .order_by(
            OfficeHoursQueueEntry.SelectedAt.desc(),
            OfficeHoursQueueEntry.Id.desc(),
        )
        .with_for_update()
        .first()
    )

    if row is None:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        return make_response(
            {"message": "The student does not have an active help session."},
            HTTPStatus.NOT_FOUND,
        )

    now = current_chicago_datetime()
    row.CompletedAt = now
    if getattr(row, "CooldownExemptUntil", None) is not None and row.CooldownExemptUntil > now:
        row.CooldownExemptUntil = now
    # Commit the pending database changes so they persist beyond this request.
    db.session.commit()

    payload = office_hours_admin_queue_payload(class_id)
    payload["message"] = "The help session ended."
    return jsonify(payload)
