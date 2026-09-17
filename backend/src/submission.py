from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import os
import re
import threading
import requests
import urllib3
from src.repositories.user_repository import UserRepository
from flask import Blueprint
from flask import make_response, request, current_app, send_file
from flask import request
from http import HTTPStatus
from injector import inject
from flask_jwt_extended import jwt_required
from flask_jwt_extended import current_user
from src.repositories.submission_repository import SubmissionRepository
from src.repositories.project_repository import ProjectRepository
from src.constants import ADMIN_ROLE, STUDENT_ROLE, TEACHER_ROLE
import json
import zipfile
from io import BytesIO
from tap.parser import Parser
from flask import jsonify
from dependency_injector.wiring import inject, Provide
from sqlalchemy import func
from container import Container
from urllib.parse import unquote
import csv
from io import StringIO
from src.ai_suggestions import GRADING_DEFAULT_DEFS_MAP
from src.repositories.models import (
    Checkpoints,
    ClassAssignments,
    Classes,
    Modules,
    OfficeHoursQueueEntry,
    OfficeHoursSession,
    Projects,
    StudentCheckpointSkips,
    StudentCooldownSkips,
    StudentStarAwards,
    StudentTestcaseInputPurchases,
    StudentUploadState,
    Submissions,
    Users,
    Testcases,
)
from src.repositories.database import db

CHICAGO_TIMEZONE = ZoneInfo("America/Chicago")


def submission_datetime_utc(value: datetime) -> datetime:
    """Convert stored Chicago wall time to naive UTC for cooldown comparisons."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=CHICAGO_TIMEZONE)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


ui_clicks_log = "/tabot-files/project-files/code_view_clicks.log"

submission_api = Blueprint('submission_api', __name__)

CHECKPOINT_SUBMISSION_COOLDOWN_AFTER_ATTEMPT = {1: 0, 2: 60, 3: 120, 4: 300}
CHECKPOINT_SUBMISSION_COOLDOWN_MAX_SECONDS = 300
MAIN_SUBMISSION_COOLDOWN_AFTER_ATTEMPT = {1: 0, 2: 120, 3: 300, 4: 600}
MAIN_SUBMISSION_COOLDOWN_MAX_SECONDS = 1200
CHECKPOINT_SUBMISSION_COOLDOWN_SKIP_COST_STARS = 1
MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS = 2
CHECKPOINT_TESTCASE_INPUT_COST_STARS = 1
MAIN_PROJECT_TESTCASE_INPUT_COST_STARS = 2
CHECKPOINT_COMPLETION_STARS = 1
MAIN_PROJECT_COMPLETION_STARS = 3
EARLY_START_MULTIPLIER = 2
OFFICE_HOURS_WAIT_MINUTES = 30
OFFICE_HOURS_HELP_MINUTES = 30
OFFICE_HOURS_SESSION_MINUTES = 120


def parse_int(v, default: int = -1) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default

def parse_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def submission_cooldown_skip_cost(checkpoint: bool) -> int:
    return (
        CHECKPOINT_SUBMISSION_COOLDOWN_SKIP_COST_STARS
        if checkpoint
        else MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS
    )


def testcase_input_purchase_cost(checkpoint: bool) -> int:
    return (
        CHECKPOINT_TESTCASE_INPUT_COST_STARS
        if checkpoint
        else MAIN_PROJECT_TESTCASE_INPUT_COST_STARS
    )


def role_from_row(role_row, default: int = STUDENT_ROLE) -> int:
    if hasattr(role_row, "Role"):
        raw_role = role_row.Role
    elif isinstance(role_row, (tuple, list)):
        raw_role = role_row[0] if role_row else default
    else:
        raw_role = role_row

    return parse_int(raw_role, default)


def current_user_id() -> int:
    return parse_int(getattr(current_user, "Id", 0), 0)


def get_user_assignment_roles(user_id: int) -> list[int]:
    user_id = parse_int(user_id, 0)
    if user_id <= 0:
        return []

    try:
        role_rows = ClassAssignments.query.with_entities(ClassAssignments.Role).filter(
            ClassAssignments.UserId == user_id
        ).all()
    except Exception:
        return []

    return [role_from_row(role_row) for role_row in role_rows]


def current_user_effective_role() -> int:
    return max([STUDENT_ROLE] + get_user_assignment_roles(current_user_id()))


def is_admin_user() -> bool:
    return current_user_effective_role() >= ADMIN_ROLE


def is_teacher_user() -> bool:
    return current_user_effective_role() >= TEACHER_ROLE


def class_exists(class_id: int) -> bool:
    class_id = parse_int(class_id, 0)
    if class_id <= 0:
        return False

    return Classes.query.filter(Classes.Id == class_id).first() is not None


def get_class_assignment(user_id: int, class_id: int):
    user_id = parse_int(user_id, 0)
    class_id = parse_int(class_id, 0)

    if user_id <= 0 or class_id <= 0:
        return None

    try:
        return ClassAssignments.query.filter(
            ClassAssignments.UserId == user_id,
            ClassAssignments.ClassId == class_id,
        ).first()
    except Exception:
        return None


def get_class_assignment_role(user_id: int, class_id: int) -> int | None:
    assignment = get_class_assignment(user_id, class_id)

    if assignment is None:
        return None

    if hasattr(assignment, "Role"):
        try:
            return int(getattr(assignment, "Role", 0) or 0)
        except Exception:
            return 0

    return 0


def current_user_class_role(class_id: int) -> int | None:
    return get_class_assignment_role(current_user_id(), class_id)


def current_user_is_enrolled_in_class_id(class_id: int) -> bool:
    return get_class_assignment(current_user_id(), class_id) is not None


def user_is_student_in_class_id(user_id: int, class_id: int) -> bool:
    assignment_role = get_class_assignment_role(user_id, class_id)
    return assignment_role == STUDENT_ROLE


def is_staff_user() -> bool:
    if is_admin_user() or is_teacher_user():
        return True

    try:
        return (
            ClassAssignments.query.filter(
                ClassAssignments.UserId == current_user_id(),
                ClassAssignments.Role >= TEACHER_ROLE,
            ).first()
            is not None
        )
    except Exception:
        return False


def teacher_id_is_on_class(teacher_id: int, class_item: Classes) -> bool:
    if class_item is None or class_item.Tid is None:
        return False

    teacher_ids = [
        token
        for token in "".join(
            character if character.isdigit() else " "
            for character in str(class_item.Tid)
        ).split()
    ]

    return str(teacher_id) in teacher_ids


def user_can_access_class_id(class_id: int) -> bool:
    class_id = parse_int(class_id, 0)
    if class_id <= 0:
        return False

    assignment_role = current_user_class_role(class_id)
    if assignment_role is not None and assignment_role >= TEACHER_ROLE:
        return True

    if is_admin_user():
        return class_exists(class_id)

    if is_teacher_user():
        class_item = Classes.query.filter(Classes.Id == class_id).first()
        return teacher_id_is_on_class(current_user_id(), class_item)

    return False


def current_user_can_view_class_id(class_id: int) -> bool:
    class_id = parse_int(class_id, 0)
    if class_id <= 0:
        return False

    return user_can_access_class_id(class_id) or current_user_is_enrolled_in_class_id(class_id)


def user_can_access_project_id(project_id: int) -> bool:
    project_id = parse_int(project_id, 0)
    if project_id <= 0:
        return False

    project = Projects.query.filter(Projects.Id == project_id).first()
    if project is None:
        return False

    return user_can_access_class_id(int(getattr(project, "ClassId", 0) or 0))


def current_user_can_view_project_id(project_id: int) -> bool:
    project_id = parse_int(project_id, 0)
    if project_id <= 0:
        return False

    project = Projects.query.filter(Projects.Id == project_id).first()
    if project is None:
        return False

    class_id = int(getattr(project, "ClassId", 0) or 0)
    return current_user_can_view_class_id(class_id)


def current_utc_datetime() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_cooldown_lifted_at(value) -> datetime | None:
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            raw = str(value).strip()
            if not raw:
                return None
            if raw.endswith("Z"):
                raw = f"{raw[:-1]}+00:00"
            parsed = datetime.fromisoformat(raw)
        except Exception:
            return None

    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return parsed


def serialize_cooldown_lifted_at(value: datetime | None) -> str | None:
    if value is None:
        return None

    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)

    return f"{value.isoformat()}Z"


def seconds_until(value: datetime | None) -> int:
    if value is None:
        return 0

    return max(0, int((value - current_utc_datetime()).total_seconds() + 0.999))


def ensure_office_hours_tables() -> None:
    """Create office-hours tables when upgrading an existing development database."""
    for model in (OfficeHoursSession, OfficeHoursQueueEntry):
        try:
            model.__table__.create(db.engine, checkfirst=True)
        except Exception:
            pass


def active_office_hours_session(class_id: int):
    class_id = parse_int(class_id, 0)
    if class_id <= 0:
        return None

    ensure_office_hours_tables()
    now = current_utc_datetime()
    return OfficeHoursSession.query.filter(
        OfficeHoursSession.ClassId == class_id,
        OfficeHoursSession.StartedAt <= now,
        OfficeHoursSession.EndsAt > now,
    ).order_by(
        OfficeHoursSession.StartedAt.desc(),
        OfficeHoursSession.Id.desc(),
    ).first()


def office_hours_session_for_time(class_id: int, value: datetime | None):
    class_id = parse_int(class_id, 0)
    at_time = parse_cooldown_lifted_at(value)
    if class_id <= 0 or at_time is None:
        return None

    ensure_office_hours_tables()
    return OfficeHoursSession.query.filter(
        OfficeHoursSession.ClassId == class_id,
        OfficeHoursSession.StartedAt <= at_time,
        OfficeHoursSession.EndsAt > at_time,
    ).order_by(
        OfficeHoursSession.StartedAt.desc(),
        OfficeHoursSession.Id.desc(),
    ).first()


def serialize_office_hours_session(class_id: int) -> dict:
    now = current_utc_datetime()
    session = active_office_hours_session(class_id)

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
            max(0, int((ends_at - now).total_seconds() + 0.999))
            if ends_at is not None
            else 0
        ),
        "session_duration_minutes": OFFICE_HOURS_SESSION_MINUTES,
    }


def office_hours_status_for_row(row: OfficeHoursQueueEntry | None) -> str:
    if row is None:
        return "not_queued"

    now = current_utc_datetime()
    joined_at = parse_cooldown_lifted_at(getattr(row, "JoinedAt", None))
    selected_at = parse_cooldown_lifted_at(getattr(row, "SelectedAt", None))
    completed_at = parse_cooldown_lifted_at(getattr(row, "CompletedAt", None))
    exempt_until = parse_cooldown_lifted_at(
        getattr(row, "CooldownExemptUntil", None)
    )
    session = office_hours_session_for_time(int(row.ClassId), joined_at)
    session_ends_at = parse_cooldown_lifted_at(
        getattr(session, "EndsAt", None)
    ) if session is not None else None

    waiting_expires_at = (
        joined_at + timedelta(minutes=OFFICE_HOURS_WAIT_MINUTES)
        if joined_at is not None
        else None
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
        if selected_at is not None:
            return "helped"

        if waiting_expires_at is not None and completed_at >= waiting_expires_at:
            return "expired_waiting"

        return "left_queue"

    if selected_at is not None:
        return (
            "being_helped"
            if help_expires_at is not None and help_expires_at > now
            else "expired"
        )

    if waiting_expires_at is not None and waiting_expires_at <= now:
        return "expired"

    return "waiting"


def expire_office_hours_entries(class_id: int | None = None) -> int:
    """Close queue/help rows when their own timer or the class session expires."""
    ensure_office_hours_tables()
    now = current_utc_datetime()
    query = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.CompletedAt.is_(None),
    )

    if class_id is not None:
        query = query.filter(OfficeHoursQueueEntry.ClassId == int(class_id))

    rows = query.all()
    expired_rows = []

    for row in rows:
        joined_at = parse_cooldown_lifted_at(getattr(row, "JoinedAt", None))
        selected_at = parse_cooldown_lifted_at(getattr(row, "SelectedAt", None))
        session = office_hours_session_for_time(int(row.ClassId), joined_at)
        session_ends_at = parse_cooldown_lifted_at(
            getattr(session, "EndsAt", None)
        ) if session is not None else None

        if selected_at is not None:
            expires_at = parse_cooldown_lifted_at(
                getattr(row, "CooldownExemptUntil", None)
            ) or (selected_at + timedelta(minutes=OFFICE_HOURS_HELP_MINUTES))
        else:
            if joined_at is None:
                continue
            expires_at = joined_at + timedelta(minutes=OFFICE_HOURS_WAIT_MINUTES)

        if session_ends_at is not None and session_ends_at < expires_at:
            expires_at = session_ends_at

        if session is None:
            # Legacy/open rows that are not tied to a valid office-hours window
            # should not stay active once session-gated office hours are enabled.
            expires_at = min(expires_at, now)

        if expires_at <= now:
            row.CompletedAt = expires_at
            if selected_at is not None:
                row.CooldownExemptUntil = expires_at
            expired_rows.append(row)

    if expired_rows:
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

    if user_id <= 0 or class_id <= 0 or project_id <= 0:
        return None

    session = active_office_hours_session(class_id)
    if session is None:
        expire_office_hours_entries(class_id)
        return None

    project = Projects.query.filter(
        Projects.Id == project_id,
        Projects.ClassId == class_id,
    ).first()
    module_id = parse_int(getattr(project, "ModuleId", 0), 0)
    if module_id <= 0:
        return None

    expire_office_hours_entries(class_id)
    now = current_utc_datetime()
    session_ends_at = parse_cooldown_lifted_at(getattr(session, "EndsAt", None))
    return OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.UserId == user_id,
        OfficeHoursQueueEntry.ClassId == class_id,
        OfficeHoursQueueEntry.ModuleId == module_id,
        OfficeHoursQueueEntry.CompletedAt.is_(None),
        OfficeHoursQueueEntry.SelectedAt.isnot(None),
        OfficeHoursQueueEntry.CooldownExemptUntil > now,
        OfficeHoursQueueEntry.JoinedAt >= session.StartedAt,
        OfficeHoursQueueEntry.JoinedAt < session.EndsAt,
    ).order_by(
        OfficeHoursQueueEntry.SelectedAt.desc(),
        OfficeHoursQueueEntry.Id.desc(),
    ).first() if session_ends_at is not None else None


def office_hours_queue_position(row: OfficeHoursQueueEntry) -> int | None:
    if office_hours_status_for_row(row) != "waiting":
        return None

    waiting_rows = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.ClassId == int(row.ClassId),
        OfficeHoursQueueEntry.CompletedAt.is_(None),
        OfficeHoursQueueEntry.SelectedAt.is_(None),
    ).order_by(
        OfficeHoursQueueEntry.JoinedAt.asc(),
        OfficeHoursQueueEntry.Id.asc(),
    ).all()

    for index, waiting_row in enumerate(waiting_rows, start=1):
        if int(waiting_row.Id) == int(row.Id):
            return index

    return None


def serialize_office_hours_entry(
    row: OfficeHoursQueueEntry,
    *,
    include_student: bool = False,
) -> dict:
    now = current_utc_datetime()
    status = office_hours_status_for_row(row)
    module = Modules.query.filter(Modules.Id == int(row.ModuleId)).first()
    joined_at = parse_cooldown_lifted_at(getattr(row, "JoinedAt", None))
    selected_at = parse_cooldown_lifted_at(getattr(row, "SelectedAt", None))
    exempt_until = parse_cooldown_lifted_at(
        getattr(row, "CooldownExemptUntil", None)
    )
    session = office_hours_session_for_time(int(row.ClassId), joined_at)
    session_ends_at = parse_cooldown_lifted_at(
        getattr(session, "EndsAt", None)
    ) if session is not None else None
    waiting_expires_at = (
        joined_at + timedelta(minutes=OFFICE_HOURS_WAIT_MINUTES)
        if joined_at is not None
        else None
    )
    if (
        waiting_expires_at is not None
        and session_ends_at is not None
        and session_ends_at < waiting_expires_at
    ):
        waiting_expires_at = session_ends_at

    help_expires_at = (
        exempt_until
        or (
            selected_at + timedelta(minutes=OFFICE_HOURS_HELP_MINUTES)
            if selected_at is not None
            else None
        )
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
        student = Users.query.filter(Users.Id == int(row.UserId)).first()
        payload.update({
            "first_name": str(getattr(student, "Firstname", "") or ""),
            "last_name": str(getattr(student, "Lastname", "") or ""),
            "email": str(getattr(student, "Email", "") or ""),
            "student_number": str(getattr(student, "StudentNumber", "") or ""),
        })

    return payload


def empty_office_hours_status() -> dict:
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
    if class_id <= 0 or module_id <= 0:
        return None, make_response(
            {"message": "class_id and module_id are required."},
            HTTPStatus.BAD_REQUEST,
        )

    if not user_is_student_in_class_id(current_user_id(), class_id):
        return None, make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    module = Modules.query.filter(
        Modules.Id == int(module_id),
        Modules.ClassId == int(class_id),
    ).first()
    if module is None:
        return None, make_response(
            {"message": "Module not found for this class."},
            HTTPStatus.NOT_FOUND,
        )

    return module, None


def ensure_incentive_tables():
    for model in (
        StudentStarAwards,
        StudentCheckpointSkips,
        StudentCooldownSkips,
        StudentTestcaseInputPurchases,
    ):
        try:
            model.__table__.create(db.engine, checkfirst=True)
        except Exception:
            pass


def get_star_balance(user_id: int, class_id: int) -> int:
    """Return awards minus every star purchase made in the class."""
    ensure_incentive_tables()
    user_id = int(user_id)
    class_id = int(class_id)

    awarded = db.session.query(
        func.coalesce(func.sum(StudentStarAwards.AwardedStars), 0)
    ).filter(
        StudentStarAwards.UserId == user_id,
        StudentStarAwards.ClassId == class_id,
    ).scalar()

    checkpoint_spent = db.session.query(
        func.coalesce(func.sum(StudentCheckpointSkips.SpentStars), 0)
    ).filter(
        StudentCheckpointSkips.UserId == user_id,
        StudentCheckpointSkips.ClassId == class_id,
    ).scalar()

    cooldown_spent = db.session.query(
        func.coalesce(func.sum(StudentCooldownSkips.SpentStars), 0)
    ).filter(
        StudentCooldownSkips.UserId == user_id,
        StudentCooldownSkips.ClassId == class_id,
    ).scalar()

    testcase_input_spent = db.session.query(
        func.coalesce(func.sum(StudentTestcaseInputPurchases.SpentStars), 0)
    ).filter(
        StudentTestcaseInputPurchases.UserId == user_id,
        StudentTestcaseInputPurchases.ClassId == class_id,
    ).scalar()

    return max(
        0,
        parse_int(awarded, 0)
        - parse_int(checkpoint_spent, 0)
        - parse_int(cooldown_spent, 0)
        - parse_int(testcase_input_spent, 0),
    )


def project_window(project) -> tuple[datetime | None, datetime | None]:
    module = getattr(project, "Module", None)

    if module is None:
        module_id = parse_int(getattr(project, "ModuleId", 0), 0)
        if module_id > 0:
            try:
                module = Modules.query.filter(Modules.Id == module_id).first()
            except Exception:
                module = None

    if module is None:
        return None, None

    start = parse_cooldown_lifted_at(getattr(module, "Start", None))
    end = parse_cooldown_lifted_at(getattr(module, "End", None))

    return start, end


def early_start_deadline_for_project(project) -> datetime | None:
    start, end = project_window(project)

    if start is None or end is None or end <= start:
        return None

    return start + ((end - start) / 2)


def assignment_started_early(
    user_id: int,
    project,
    checkpoint: bool,
    checkpoint_id: int,
    early_deadline: datetime | None,
) -> bool:
    if project is None or early_deadline is None:
        return False

    query = Submissions.query.filter(
        Submissions.User == int(user_id),
        Submissions.Project == int(project.Id),
        Submissions.IsCheckpoint == bool(checkpoint),
    )

    if checkpoint:
        query = query.filter(Submissions.CheckpointId == int(checkpoint_id or 0))
    else:
        query = query.filter(Submissions.CheckpointId.is_(None))

    rows = query.order_by(Submissions.Time.asc()).all()
    first_started_at = None

    for row in rows:
        parsed = parse_submission_datetime_for_cooldown(getattr(row, "Time", None))
        if parsed is None:
            continue
        if first_started_at is None or parsed < first_started_at:
            first_started_at = parsed

    return first_started_at is not None and first_started_at <= early_deadline


def existing_star_award_for_scope(
    user_id: int,
    class_id: int,
    project_id: int,
    checkpoint: bool,
    checkpoint_id: int,
):
    if project_id <= 0:
        return None

    ensure_incentive_tables()
    award_type = "checkpoint_completion" if checkpoint else "main_completion"
    checkpoint_key = int(checkpoint_id or 0) if checkpoint else 0

    return StudentStarAwards.query.filter(
        StudentStarAwards.UserId == int(user_id),
        StudentStarAwards.ClassId == int(class_id),
        StudentStarAwards.ProjectId == int(project_id),
        StudentStarAwards.CheckpointId == checkpoint_key,
        StudentStarAwards.AwardType == award_type,
    ).first()


def scoped_reward_payload(
    user_id: int,
    class_id: int,
    project_id: int = 0,
    checkpoint: bool = False,
    checkpoint_id: int = 0,
) -> dict:
    base_stars = CHECKPOINT_COMPLETION_STARS if checkpoint else MAIN_PROJECT_COMPLETION_STARS
    project = Projects.query.filter(Projects.Id == int(project_id)).first() if project_id > 0 else None
    early_deadline = early_start_deadline_for_project(project) if project is not None else None
    early_remaining_seconds = seconds_until(early_deadline)
    early_window_open = early_deadline is not None and early_remaining_seconds > 0
    started_early = early_window_open or assignment_started_early(
        user_id,
        project,
        checkpoint,
        checkpoint_id,
        early_deadline,
    )
    existing_award = existing_star_award_for_scope(
        user_id,
        class_id,
        project_id,
        checkpoint,
        checkpoint_id,
    )

    if existing_award is not None:
        awarded_stars = max(0, parse_int(getattr(existing_award, "Stars", 0), 0))
        awarded_base = max(0, parse_int(getattr(existing_award, "BaseStars", base_stars), base_stars))
        awarded_multiplier = max(1, parse_int(getattr(existing_award, "Multiplier", 1), 1))

        return {
            "checkpoint_completion_stars": CHECKPOINT_COMPLETION_STARS,
            "main_project_completion_stars": MAIN_PROJECT_COMPLETION_STARS,
            "early_start_multiplier": EARLY_START_MULTIPLIER,
            "early_start_deadline": serialize_cooldown_lifted_at(early_deadline),
            "early_start_remaining_seconds": early_remaining_seconds,
            "early_start_window_open": early_window_open,
            "reward_base_stars": awarded_base,
            "reward_multiplier": awarded_multiplier,
            "reward_total_stars": awarded_stars,
            "reward_already_awarded": True,
            "reward_started_early": bool(getattr(existing_award, "StartedEarly", False)),
        }

    multiplier = EARLY_START_MULTIPLIER if started_early else 1

    return {
        "checkpoint_completion_stars": CHECKPOINT_COMPLETION_STARS,
        "main_project_completion_stars": MAIN_PROJECT_COMPLETION_STARS,
        "early_start_multiplier": EARLY_START_MULTIPLIER,
        "early_start_deadline": serialize_cooldown_lifted_at(early_deadline),
        "early_start_remaining_seconds": early_remaining_seconds,
        "early_start_window_open": early_window_open,
        "reward_base_stars": base_stars,
        "reward_multiplier": multiplier,
        "reward_total_stars": base_stars * multiplier,
        "reward_already_awarded": False,
        "reward_started_early": started_early,
    }


def incentive_payload(
    user_id: int,
    class_id: int,
    project_id: int = 0,
    checkpoint: bool = False,
    checkpoint_id: int = 0,
) -> dict:
    balance = get_star_balance(user_id, class_id)
    cooldown_skip_cost = submission_cooldown_skip_cost(checkpoint)
    cooldown_state = submission_cooldown_state(
        user_id,
        class_id,
        project_id,
        checkpoint,
        checkpoint_id,
    )
    payload = {
        "stars": balance,
        "star_balance": balance,
        "checkpoint_cooldown_skip_cost": CHECKPOINT_SUBMISSION_COOLDOWN_SKIP_COST_STARS,
        "main_project_cooldown_skip_cost": MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS,
        "cooldown_skip_cost": cooldown_skip_cost,
        **cooldown_state,
    }
    payload.update(scoped_reward_payload(user_id, class_id, project_id, checkpoint, checkpoint_id))
    return payload


def parse_submission_datetime_for_cooldown(value) -> datetime | None:
    """Return Chicago wall time, matching the existing naive submission column."""
    if isinstance(value, datetime):
        return value.astimezone(CHICAGO_TIMEZONE).replace(tzinfo=None) if value.tzinfo else value

    raw = str(value or "").strip()
    if not raw:
        return None

    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(raw)
        return parsed.astimezone(CHICAGO_TIMEZONE).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass

    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%y %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except ValueError:
            pass

    return None


def submission_cooldown_seconds_for_attempt_count(
    completed_attempts: int,
    is_checkpoint: bool,
) -> int:
    completed_attempts = max(0, int(completed_attempts or 0))

    if completed_attempts <= 0:
        return 0

    schedule = (
        CHECKPOINT_SUBMISSION_COOLDOWN_AFTER_ATTEMPT
        if is_checkpoint
        else MAIN_SUBMISSION_COOLDOWN_AFTER_ATTEMPT
    )
    max_seconds = (
        CHECKPOINT_SUBMISSION_COOLDOWN_MAX_SECONDS
        if is_checkpoint
        else MAIN_SUBMISSION_COOLDOWN_MAX_SECONDS
    )

    return schedule.get(completed_attempts, max_seconds)


def submission_scope_query(
    user_id: int,
    project_id: int,
    checkpoint: bool,
    checkpoint_id: int,
):
    query = Submissions.query.filter(
        Submissions.User == int(user_id),
        Submissions.Project == int(project_id),
        Submissions.IsCheckpoint == bool(checkpoint),
    )

    if checkpoint:
        return query.filter(Submissions.CheckpointId == int(checkpoint_id or 0))

    return query.filter(Submissions.CheckpointId.is_(None))


def pending_cooldown_skip_exists(
    user_id: int,
    class_id: int,
    project_id: int,
    checkpoint_id: int,
    submitted_at: datetime | None,
) -> bool:
    query = StudentCooldownSkips.query.filter(
        StudentCooldownSkips.UserId == int(user_id),
        StudentCooldownSkips.ClassId == int(class_id),
        StudentCooldownSkips.ProjectId == int(project_id),
        StudentCooldownSkips.CheckpointId == int(checkpoint_id or 0),
        StudentCooldownSkips.UsedAt.is_(None),
    )

    if submitted_at is not None:
        submitted_at_utc = submission_datetime_utc(submitted_at)
        query = query.filter(StudentCooldownSkips.CreatedAt >= submitted_at_utc)

    return query.first() is not None


def submission_cooldown_state(
    user_id: int,
    class_id: int,
    project_id: int,
    checkpoint: bool,
    checkpoint_id: int,
) -> dict:
    if project_id <= 0:
        return {
            "submission_attempt_count": 0,
            "next_attempt_number": 1,
            "submission_cooldown_seconds": 0,
            "cooldown_remaining_seconds": 0,
            "cooldown_lifted_at": None,
            "office_hours_cooldown_exempt": False,
            "office_hours_cooldown_exempt_until": None,
        }

    scope_query = submission_scope_query(
        user_id,
        project_id,
        checkpoint,
        checkpoint_id,
    )
    completed_attempts = scope_query.count()
    next_attempt = completed_attempts + 1
    office_hours_entry = active_office_hours_entry_for_project(
        user_id,
        class_id,
        project_id,
    )

    if office_hours_entry is not None:
        return {
            "submission_attempt_count": completed_attempts,
            "next_attempt_number": next_attempt,
            "submission_cooldown_seconds": 0,
            "cooldown_remaining_seconds": 0,
            "cooldown_lifted_at": None,
            "office_hours_cooldown_exempt": True,
            "office_hours_cooldown_exempt_until": serialize_cooldown_lifted_at(
                parse_cooldown_lifted_at(
                    getattr(office_hours_entry, "CooldownExemptUntil", None)
                )
            ),
        }

    cooldown_seconds = submission_cooldown_seconds_for_attempt_count(
        completed_attempts,
        checkpoint,
    )
    latest = scope_query.order_by(Submissions.Time.desc(), Submissions.Id.desc()).first()
    submitted_at = parse_submission_datetime_for_cooldown(
        getattr(latest, "Time", None) if latest is not None else None
    )

    remaining_seconds = 0
    cooldown_lifted_at = None

    if submitted_at is not None and cooldown_seconds > 0:
        # Submission times are Chicago wall time; compare actual UTC instants.
        elapsed_seconds = (
            current_utc_datetime() - submission_datetime_utc(submitted_at)
        ).total_seconds()
        remaining_seconds = max(
            0,
            int(cooldown_seconds - elapsed_seconds + 0.999),
        )
        if remaining_seconds > 0:
            cooldown_lifted_at = current_utc_datetime() + timedelta(
                seconds=remaining_seconds
            )

        if remaining_seconds > 0 and pending_cooldown_skip_exists(
            user_id,
            class_id,
            project_id,
            checkpoint_id if checkpoint else 0,
            submitted_at,
        ):
            remaining_seconds = 0
            cooldown_lifted_at = None

    return {
        "submission_attempt_count": completed_attempts,
        "next_attempt_number": next_attempt,
        "submission_cooldown_seconds": cooldown_seconds,
        "cooldown_remaining_seconds": remaining_seconds,
        "cooldown_lifted_at": serialize_cooldown_lifted_at(cooldown_lifted_at),
        "office_hours_cooldown_exempt": False,
        "office_hours_cooldown_exempt_until": None,
    }


def upload_state_scope_from_mapping(mapping) -> tuple[int, int, bool, int]:
    class_id = parse_int(mapping.get("class_id", 0), 0)
    project_id = parse_int(mapping.get("project_id", 0), 0)
    checkpoint = parse_bool(mapping.get("checkpoint", False))
    checkpoint_id = parse_int(mapping.get("checkpoint_id", 0), 0) if checkpoint else 0

    return class_id, project_id, checkpoint, max(0, checkpoint_id)


def current_user_can_use_upload_state(class_id: int, project_id: int, checkpoint_id: int) -> bool:
    class_id = parse_int(class_id, 0)
    project_id = parse_int(project_id, 0)
    checkpoint_id = parse_int(checkpoint_id, 0)

    if class_id <= 0 or project_id <= 0:
        return False

    project = Projects.query.filter(Projects.Id == project_id).first()
    if project is None:
        return False

    if parse_int(getattr(project, "ClassId", 0), 0) != class_id:
        return False

    if not current_user_can_view_project_id(project_id):
        return False

    if checkpoint_id > 0:
        checkpoint = Checkpoints.query.filter(
            Checkpoints.Id == checkpoint_id,
            Checkpoints.ProjectId == project_id,
        ).first()
        return checkpoint is not None

    return True


def get_student_upload_state_row(class_id: int, project_id: int, checkpoint_id: int):
    return StudentUploadState.query.filter(
        StudentUploadState.UserId == int(current_user.Id),
        StudentUploadState.ClassId == int(class_id),
        StudentUploadState.ProjectId == int(project_id),
        StudentUploadState.CheckpointId == int(checkpoint_id or 0),
    ).first()


def latest_submission_for_upload_state(
    submission_repo: SubmissionRepository,
    project_id: int,
    checkpoint: bool,
    checkpoint_id: int,
):
    if checkpoint:
        return latest_checkpoint_submission(
            int(project_id),
            int(current_user.Id),
            int(checkpoint_id) if checkpoint_id > 0 else None,
        )

    return submission_repo.get_submission_by_user_and_projectid(
        int(current_user.Id),
        int(project_id),
    )


def submission_matches_upload_state(submission, project_id: int, checkpoint: bool, checkpoint_id: int) -> bool:
    if submission is None:
        return False

    if int(getattr(submission, "User", -1) or -1) != int(current_user.Id):
        return False

    if int(getattr(submission, "Project", -1) or -1) != int(project_id):
        return False

    if checkpoint:
        if not bool(getattr(submission, "IsCheckpoint", False)):
            return False

        if checkpoint_id > 0 and int(getattr(submission, "CheckpointId", 0) or 0) != int(checkpoint_id):
            return False

    elif bool(getattr(submission, "IsCheckpoint", False)):
        return False

    return True


def serialize_student_upload_state(
    submission_repo: SubmissionRepository,
    class_id: int,
    project_id: int,
    checkpoint: bool,
    checkpoint_id: int,
) -> dict:
    latest_submission = latest_submission_for_upload_state(
        submission_repo,
        int(project_id),
        bool(checkpoint),
        int(checkpoint_id or 0),
    )
    latest_submission_id = (
        int(getattr(latest_submission, "Id", 0) or 0)
        if latest_submission is not None
        else None
    )
    cooldown_state = submission_cooldown_state(
        int(current_user.Id),
        class_id,
        project_id,
        checkpoint,
        checkpoint_id,
    )

    return {
        "last_submission_id": latest_submission_id,
        "previous_submission_id": latest_submission_id,
        **cooldown_state,
    }


def user_can_access_submission(submission) -> bool:
    if submission is None:
        return False

    return user_can_access_project_id(int(getattr(submission, "Project", 0) or 0))


def current_user_can_view_submission(submission, submission_repo: SubmissionRepository | None = None) -> bool:
    if submission is None:
        return False

    if user_can_access_submission(submission):
        return True

    if int(getattr(submission, "User", -1) or -1) == int(current_user.Id):
        return True

    if submission_repo is not None:
        try:
            return submission_repo.submission_view_verification(
                int(current_user.Id),
                int(getattr(submission, "Id", -1) or -1),
            )
        except Exception:
            return False

    return False

def opt_int(raw) -> int | None:
    s = str(raw or "").strip()
    return int(s) if s.isdigit() else None

def checkpoint_params_from_args() -> tuple[bool, int | None]:
    want_checkpoint = parse_bool(request.args.get("checkpoint", ""))
    ppid = opt_int(request.args.get("checkpoint_id", ""))
    return want_checkpoint, ppid

def latest_checkpoint_submission(project_id: int, user_id: int, checkpoint_id: int | None):
    try:
        q = (
            Submissions.query
            .filter(
                Submissions.Project == int(project_id),
                Submissions.User == int(user_id),
                Submissions.IsCheckpoint == True,
            )
        )
        if checkpoint_id is not None and hasattr(Submissions, "CheckpointId"):
            q = q.filter(Submissions.CheckpointId == int(checkpoint_id))
        return q.order_by(Submissions.Time.desc(), Submissions.Id.desc()).first()
    except Exception:
        return None

def resolve_submission_for_current_user(
    submission_repo: SubmissionRepository,
    project_repo: ProjectRepository,
    submission_id: int,
    class_id: int,
    want_checkpoint: bool,
    ppid: int | None,
):
    """
    Resolves:
      - real submission id -> that submission
      - otherwise treats submission_id as project id and returns latest (main/checkpoint) for current_user
      - if submission_id is EMPTY, resolves current project by class_id and returns latest main submission
    Returns: (submission | None, project_id:int, checkpoint_id_for_hidden_flags:int|None)
    """
    project_id = -1
    sub = None
    checkpoint_id = None

    if submission_id != -1:
        sub = submission_repo.get_submission_by_submission_id(int(submission_id))
        if sub is None:
            project_id = int(submission_id)
            if want_checkpoint:
                sub = latest_checkpoint_submission(project_id, int(current_user.Id), ppid)
            else:
                sub = submission_repo.get_submission_by_user_and_projectid(int(current_user.Id), int(project_id))
        if sub is None:
            return None, int(project_id), None
        project_id = int(getattr(sub, "Project", -1) or -1)
    else:
        proj = project_repo.get_current_project_by_class(int(class_id))
        if proj is None:
            return None, -1, None
        project_id = int(getattr(proj, "Id", -1) or -1)
        sub = submission_repo.get_submission_by_user_and_projectid(int(current_user.Id), int(project_id))
        if sub is None:
            return None, int(project_id), None

    try:
        if bool(getattr(sub, "IsCheckpoint", False)) and getattr(sub, "CheckpointId", None) is not None:
            checkpoint_id = int(getattr(sub, "CheckpointId"))
    except Exception:
        checkpoint_id = None

    return sub, int(project_id), checkpoint_id

def apply_hidden_flags_to_results(output_json: str, project_id: int, checkpoint_id: int | None) -> str:
    try:
        obj = json.loads(output_json) if isinstance(output_json, str) else (output_json or {})
        results = obj.get("results", None) if isinstance(obj, dict) else None
        if not isinstance(results, list) or int(project_id) <= 0:
            return output_json

        q = Testcases.query.filter(Testcases.ProjectId == int(project_id))
        if checkpoint_id is not None:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))
        tcs = q.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all()
        testcase_meta_by_name = {}
        for index, tc in enumerate(tcs or [], start=1):
            key = str(getattr(tc, "Name", "") or "").strip().lower()
            testcase_meta_by_name.setdefault(key, []).append({
                "hidden": bool(getattr(tc, "Hidden", False)),
                "order": index,
            })

        name_occurrences = {}
        for original_index, r in enumerate(results):
            if not isinstance(r, dict):
                continue
            r.pop("description", None)
            name = None
            if isinstance(r.get("name"), str):
                name = r.get("name")
            elif isinstance(r.get("test"), dict) and isinstance(r["test"].get("name"), str):
                name = r["test"]["name"]

            key = (str(name or "").strip().lower())
            occurrence = int(name_occurrences.get(key, 0))
            name_occurrences[key] = occurrence + 1
            matches = testcase_meta_by_name.get(key, [])
            metadata = matches[occurrence] if occurrence < len(matches) else None

            r["hidden"] = bool(metadata["hidden"]) if metadata else False
            if metadata:
                r["order"] = int(metadata["order"])
            elif not isinstance(r.get("order"), int):
                r["order"] = len(tcs) + original_index + 1
            r["_original_index"] = original_index

            if isinstance(r.get("test"), dict):
                r["test"].pop("description", None)
                r["test"]["hidden"] = r["hidden"]

        results.sort(
            key=lambda result: (
                parse_int(result.get("order"), len(tcs) + len(results) + 1)
                if isinstance(result, dict)
                else len(tcs) + len(results) + 1,
                parse_int(result.get("_original_index"), 0) if isinstance(result, dict) else 0,
            )
        )
        for result in results:
            if isinstance(result, dict):
                result.pop("_original_index", None)

        return json.dumps(obj, sort_keys=True, indent=4)
    except Exception:
        return output_json


def resolve_testcase_input_purchase_scope(
    submission_repo: SubmissionRepository,
    submission_id: int,
    class_id: int,
):
    if submission_id <= 0 or class_id <= 0:
        return None, "submission_id and class_id are required.", HTTPStatus.BAD_REQUEST

    user_id = int(current_user.Id)
    if not user_is_student_in_class_id(user_id, class_id):
        return None, "Not Authorized", HTTPStatus.UNAUTHORIZED

    submission = submission_repo.get_submission_by_submission_id(submission_id)
    if submission is None:
        return None, "Submission not found.", HTTPStatus.NOT_FOUND

    if int(getattr(submission, "User", 0) or 0) != user_id:
        return None, "Not Authorized", HTTPStatus.UNAUTHORIZED

    project_id = int(getattr(submission, "Project", 0) or 0)
    project = Projects.query.filter(Projects.Id == project_id).first()
    if project is None:
        return None, "Project not found.", HTTPStatus.NOT_FOUND

    if int(getattr(project, "ClassId", 0) or 0) != class_id:
        return None, "Not Authorized", HTTPStatus.UNAUTHORIZED

    is_checkpoint = bool(getattr(submission, "IsCheckpoint", False))
    checkpoint_id = (
        int(getattr(submission, "CheckpointId", 0) or 0)
        if is_checkpoint
        else 0
    )
    if is_checkpoint and checkpoint_id <= 0:
        return None, "Checkpoint not found.", HTTPStatus.NOT_FOUND

    return {
        "user_id": user_id,
        "class_id": class_id,
        "project_id": project_id,
        "checkpoint_id": checkpoint_id,
        "is_checkpoint": is_checkpoint,
        "submission_id": int(getattr(submission, "Id", submission_id) or submission_id),
        "submission_output_path": str(
            getattr(submission, "OutputFilepath", "") or ""
        ),
    }, None, HTTPStatus.OK


def testcase_input_rows_for_scope(project_id: int, checkpoint_id: int):
    query = Testcases.query.filter(Testcases.ProjectId == int(project_id))
    if checkpoint_id > 0:
        query = query.filter(Testcases.CheckpointId == int(checkpoint_id))
    else:
        query = query.filter(Testcases.CheckpointId.is_(None))

    return query.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all()


INPUT_EVENT_PATTERN = re.compile(
    r"\[\[\[MAAT_INPUT_B64:[A-Za-z0-9_-]*\]\]\]"
)
HIDDEN_INPUT_EVENT = "[[[MAAT_INPUT_HIDDEN]]]"


def redact_input_events(value):
    if isinstance(value, str):
        return INPUT_EVENT_PATTERN.sub(HIDDEN_INPUT_EVENT, value)
    if isinstance(value, list):
        return [redact_input_events(item) for item in value]
    if isinstance(value, dict):
        return {
            key: redact_input_events(item)
            for key, item in value.items()
        }
    return value


def protect_unrevealed_testcase_inputs(
    output_json,
    user_id: int,
    class_id: int,
    project_id: int,
    checkpoint_id: int,
):
    """Remove exact testcase values from student diff payloads until purchased."""
    try:
        ensure_incentive_tables()
        testcase_rows = testcase_input_rows_for_scope(project_id, checkpoint_id)
        purchased_ids = {
            int(row.TestcaseId)
            for row in StudentTestcaseInputPurchases.query.filter(
                StudentTestcaseInputPurchases.UserId == int(user_id),
                StudentTestcaseInputPurchases.ClassId == int(class_id),
                StudentTestcaseInputPurchases.ProjectId == int(project_id),
                StudentTestcaseInputPurchases.CheckpointId == int(checkpoint_id),
            ).all()
        }
        revealed_orders = {
            order
            for order, testcase in enumerate(testcase_rows, start=1)
            if int(testcase.Id) in purchased_ids
        }

        payload = json.loads(output_json) if isinstance(output_json, str) else (output_json or {})
        results = payload.get("results", []) if isinstance(payload, dict) else []

        for index, result in enumerate(results, start=1):
            if not isinstance(result, dict):
                results[index - 1] = redact_input_events(result)
                continue

            result_order = parse_int(
                result.get("order", (result.get("test") or {}).get("order", index)),
                index,
            )
            if result_order not in revealed_orders:
                results[index - 1] = redact_input_events(result)

        return json.dumps(payload, sort_keys=True, indent=4)
    except Exception:
        # Fail closed: a malformed or unexpected payload must not expose input.
        return INPUT_EVENT_PATTERN.sub(HIDDEN_INPUT_EVENT, str(output_json or ""))


def testcase_result_status(result: dict) -> str:
    if parse_bool(result.get("skipped", False)):
        return "skipped"

    raw_passed = result.get("passed")
    if isinstance(raw_passed, bool):
        return "passed" if raw_passed else "failed"
    if isinstance(raw_passed, (int, float)) and raw_passed in (0, 1):
        return "passed" if raw_passed == 1 else "failed"
    if isinstance(raw_passed, str):
        normalized = raw_passed.strip().lower()
        if normalized in ("1", "true", "yes", "y", "passed", "pass"):
            return "passed"
        if normalized in ("0", "false", "no", "n", "failed", "fail"):
            return "failed"

    return "unavailable"


def testcase_result_statuses_for_scope(scope: dict, rows: list) -> dict:
    statuses = {
        int(testcase.Id): "unavailable"
        for testcase in rows
    }

    try:
        output = convert_tap_to_json(
            scope["submission_output_path"],
            current_user_effective_role(),
            0,
            False,
        )
        output = apply_hidden_flags_to_results(
            output,
            scope["project_id"],
            scope["checkpoint_id"] if scope["checkpoint_id"] > 0 else None,
        )
        parsed = json.loads(output) if isinstance(output, str) else (output or {})
        results = parsed.get("results", []) if isinstance(parsed, dict) else []
        statuses_by_order = {}

        for result in results:
            if not isinstance(result, dict):
                continue
            order = parse_int(result.get("order"), 0)
            if order > 0:
                statuses_by_order[order] = testcase_result_status(result)

        for index, testcase in enumerate(rows, start=1):
            statuses[int(testcase.Id)] = statuses_by_order.get(
                index,
                "unavailable",
            )
    except Exception:
        pass

    return statuses


def serialize_testcase_input_store(scope: dict) -> dict:
    ensure_incentive_tables()
    rows = testcase_input_rows_for_scope(
        scope["project_id"],
        scope["checkpoint_id"],
    )
    result_statuses = testcase_result_statuses_for_scope(scope, rows)
    purchase_rows = StudentTestcaseInputPurchases.query.filter(
        StudentTestcaseInputPurchases.UserId == scope["user_id"],
        StudentTestcaseInputPurchases.ClassId == scope["class_id"],
        StudentTestcaseInputPurchases.ProjectId == scope["project_id"],
        StudentTestcaseInputPurchases.CheckpointId == scope["checkpoint_id"],
    ).all()
    purchased_ids = {int(row.TestcaseId) for row in purchase_rows}

    return {
        "project_id": scope["project_id"],
        "checkpoint_id": scope["checkpoint_id"],
        "is_checkpoint": scope["is_checkpoint"],
        "input_cost": testcase_input_purchase_cost(scope["is_checkpoint"]),
        "star_balance": get_star_balance(scope["user_id"], scope["class_id"]),
        "testcases": [
            {
                "testcase_id": int(testcase.Id),
                "name": str(getattr(testcase, "Name", "") or f"Testcase {index}"),
                "order": index,
                "purchased": int(testcase.Id) in purchased_ids,
                "result_status": result_statuses.get(
                    int(testcase.Id),
                    "unavailable",
                ),
                "purchase_eligible": (
                    result_statuses.get(int(testcase.Id)) == "failed"
                ),
                "input": (
                    str(getattr(testcase, "input", "") or "")
                    if int(testcase.Id) in purchased_ids
                    else None
                ),
            }
            for index, testcase in enumerate(rows, start=1)
        ],
    }


def convert_tap_to_json(file_path, role, current_level, hasLVLSYSEnabled):
    # New grader may write JSON directly. Accept either:
    #  1) a JSON file path
    #  2) a non-.json file whose CONTENTS are JSON
    #  3) (rare) a raw JSON string mistakenly passed as "file_path"
    try:
        s = str(file_path or "").strip()
        if not s:
            return json.dumps({"results": []}, sort_keys=True, indent=4)

        # Raw JSON string fallback
        if (s.startswith("{") or s.startswith("[") and "\n" in s):
            try:
                obj = json.loads(s) or {}
                return json.dumps(obj, sort_keys=True, indent=4)
            except Exception:
                pass

        # If it's a real file, try parsing its contents as JSON first (regardless of extension).
        if os.path.exists(s) and os.path.isfile(s):
            try:
                with open(s, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read() or ""
                raw_strip = raw.strip()
                if raw_strip.startswith("{") or raw_strip.startswith("["):
                    obj = json.loads(raw_strip) or {}
                    return json.dumps(obj, sort_keys=True, indent=4)
            except Exception:
                pass
    except Exception:
        pass

    parser = Parser()
    test = []
    final = {}

    def sanitize_yaml_block(yaml_block: dict) -> dict:
        new_yaml = (yaml_block or {}).copy()
        new_yaml.pop("description", None)
        return new_yaml

    def parse_suite(yaml_block: dict) -> int:
        try:
            return int((yaml_block or {}).get("suite", 0))
        except (TypeError, ValueError):
            return 0

    for line in parser.parse_file(file_path):
        if line.category != "test":
            continue
        if line.yaml_block is None:
            continue

        yaml_clean = sanitize_yaml_block(line.yaml_block)

        # Levels disabled: return tests as-is
        if not hasLVLSYSEnabled:
            test.append({
                'skipped': line.skip,
                'passed': line.ok,
                'test': yaml_clean
            })
            continue

        suite_req = parse_suite(yaml_clean)

        if current_level >= suite_req:
            test.append({
                'skipped': line.skip,
                'passed': line.ok,
                'test': yaml_clean
            })
        else:
            locked_yaml = {
                "name": yaml_clean.get("name", ""),
                "suite": suite_req,
                "locked": True
            }
            test.append({
                'skipped': "",
                'passed': "",
                'test': locked_yaml
            })

    final["results"] = test
    return json.dumps(final, sort_keys=True, indent=4)

@submission_api.route('/testcaseerrors', methods=['GET'])
@jwt_required()
@inject
def get_testcase_errors(submission_repo: SubmissionRepository = Provide[Container.submission_repo], project_repo:  ProjectRepository = Provide[Container.project_repo]):
    class_id = parse_int(request.args.get("class_id", "-1"), -1)
    submission_id = parse_int(request.args.get("id", "-1"), -1)
    want_checkpoint, ppid_qs = checkpoint_params_from_args()

    submission, projectid, checkpoint_id = resolve_submission_for_current_user(
        submission_repo,
        project_repo,
        submission_id,
        class_id,
        want_checkpoint,
        ppid_qs,
    )
    if submission is None:
        return make_response(json.dumps({"results": []}), HTTPStatus.OK)

    if not current_user_can_view_submission(submission, submission_repo):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    output = convert_tap_to_json(submission.OutputFilepath, current_user_effective_role(), 0, False)
    output = apply_hidden_flags_to_results(output, int(projectid), checkpoint_id)

    project = Projects.query.filter(Projects.Id == int(projectid)).first()
    submission_class_id = int(getattr(project, "ClassId", 0) or 0)

    if current_user_class_role(submission_class_id) == STUDENT_ROLE:
        output = protect_unrevealed_testcase_inputs(
            output,
            current_user_id(),
            submission_class_id,
            int(projectid),
            int(checkpoint_id or 0),
        )

    return make_response(output, HTTPStatus.OK)


@submission_api.route('/testcase-inputs', methods=['GET', 'POST'])
@jwt_required()
@inject
def testcase_inputs(
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    source = request.args if request.method == 'GET' else (request.get_json(silent=True) or {})
    submission_id = parse_int(
        source.get("id", source.get("submission_id", 0)),
        0,
    )
    class_id = parse_int(source.get("class_id", 0), 0)

    scope, error_message, error_status = resolve_testcase_input_purchase_scope(
        submission_repo,
        submission_id,
        class_id,
    )
    if scope is None:
        return make_response({"message": error_message}, error_status)

    if request.method == 'GET':
        response = jsonify(serialize_testcase_input_store(scope))
        response.headers["Cache-Control"] = "no-store"
        return response

    testcase_id = parse_int(source.get("testcase_id", 0), 0)
    if testcase_id <= 0:
        return make_response({"message": "testcase_id is required."}, HTTPStatus.BAD_REQUEST)

    testcase = Testcases.query.filter(
        Testcases.Id == testcase_id,
        Testcases.ProjectId == scope["project_id"],
    ).first()
    if testcase is None:
        return make_response({"message": "Testcase not found."}, HTTPStatus.NOT_FOUND)

    testcase_checkpoint_id = int(getattr(testcase, "CheckpointId", 0) or 0)
    if testcase_checkpoint_id != scope["checkpoint_id"]:
        return make_response({"message": "Testcase not found."}, HTTPStatus.NOT_FOUND)

    ensure_incentive_tables()
    purchase_filter = (
        StudentTestcaseInputPurchases.UserId == scope["user_id"],
        StudentTestcaseInputPurchases.ClassId == scope["class_id"],
        StudentTestcaseInputPurchases.ProjectId == scope["project_id"],
        StudentTestcaseInputPurchases.CheckpointId == scope["checkpoint_id"],
        StudentTestcaseInputPurchases.TestcaseId == testcase_id,
    )
    existing_purchase = StudentTestcaseInputPurchases.query.filter(*purchase_filter).first()
    if existing_purchase is not None:
        payload = serialize_testcase_input_store(scope)
        payload["already_purchased"] = True
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response

    result_statuses = testcase_result_statuses_for_scope(
        scope,
        testcase_input_rows_for_scope(
            scope["project_id"],
            scope["checkpoint_id"],
        ),
    )
    result_status = result_statuses.get(testcase_id, "unavailable")
    if result_status != "failed":
        if result_status == "passed":
            message = "Passed testcase inputs cannot be purchased."
        else:
            message = "Only failing testcase inputs can be purchased."
        return make_response({
            "message": message,
            "result_status": result_status,
            "star_balance": get_star_balance(
                scope["user_id"],
                scope["class_id"],
            ),
        }, HTTPStatus.BAD_REQUEST)

    # Lock the student row so simultaneous purchases cannot overspend the balance.
    locked_user = Users.query.filter(
        Users.Id == scope["user_id"]
    ).with_for_update().first()
    if locked_user is None:
        db.session.rollback()
        return make_response({"message": "User not found."}, HTTPStatus.NOT_FOUND)

    existing_purchase = StudentTestcaseInputPurchases.query.filter(*purchase_filter).first()
    if existing_purchase is not None:
        payload = serialize_testcase_input_store(scope)
        payload["already_purchased"] = True
        db.session.commit()
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response

    cost = testcase_input_purchase_cost(scope["is_checkpoint"])
    balance = get_star_balance(scope["user_id"], scope["class_id"])
    star_label = "star" if cost == 1 else "stars"
    if balance < cost:
        db.session.rollback()
        return make_response({
            "message": f"You need {cost} {star_label} to reveal this testcase input.",
            "star_balance": balance,
            "required_stars": cost,
        }, HTTPStatus.BAD_REQUEST)

    db.session.add(StudentTestcaseInputPurchases(
        UserId=scope["user_id"],
        ClassId=scope["class_id"],
        ProjectId=scope["project_id"],
        CheckpointId=scope["checkpoint_id"],
        TestcaseId=testcase_id,
        SpentStars=cost,
        CreatedAt=current_utc_datetime(),
    ))
    db.session.commit()

    payload = serialize_testcase_input_store(scope)
    payload["purchased_testcase_id"] = testcase_id
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@submission_api.route('/codefinder', methods=['GET'])
@jwt_required()
@inject
def codefinder(submission_repo: SubmissionRepository = Provide[Container.submission_repo], project_repo: ProjectRepository = Provide[Container.project_repo]):
    submissionid = parse_int(request.args.get("id", "-1"), -1)
    class_id = parse_int(request.args.get("class_id", "-1"), -1)
    fmt = (request.args.get("format", "") or "").strip().lower()
    want_json = fmt in ("json", "view", "preview")

    want_checkpoint, ppid = checkpoint_params_from_args()

    code_output = ""
    if submissionid != -1:
        sub = submission_repo.get_submission_by_submission_id(submissionid)
        if sub is not None and current_user_can_view_submission(sub, submission_repo):
            code_output = submission_repo.get_code_path_by_submission_id(submissionid)
        else:
            resolved, _, _ = resolve_submission_for_current_user(
                submission_repo,
                project_repo,
                int(submissionid),
                int(class_id),
                bool(want_checkpoint),
                ppid,
            )
            code_output = getattr(resolved, "CodeFilepath", "") if resolved else ""
    else:
        if not current_user_can_view_class_id(class_id):
            return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

        current_project = project_repo.get_current_project_by_class(class_id)
        if current_project is None:
            return make_response("Not Found", HTTPStatus.NOT_FOUND)

        projectid = current_project.Id
        latest_submission = submission_repo.get_submission_by_user_and_projectid(current_user.Id, projectid)
        code_output = getattr(latest_submission, "CodeFilepath", "") if latest_submission else ""
    # JSON preview mode (used by CodePage) so the UI can render readable source
    if want_json:
        files_payload = []
        if not code_output:
            resp = make_response(json.dumps({"files": []}), HTTPStatus.OK)
            resp.headers["Content-Type"] = "application/json; charset=utf-8"
            resp.headers["Cache-Control"] = "no-store"
            return resp
        if not os.path.isdir(code_output):
            with open(code_output, 'r', encoding='utf-8', errors='replace') as f:
                files_payload.append({"name": os.path.basename(code_output), "content": f.read()})
        else:
            allowed_exts = {".py", ".java", ".c", ".h", ".rkt"}
            names = sorted(os.listdir(code_output), key=lambda n: (n != "Main.java", n.lower()))
            for name in names:
                full = os.path.join(code_output, name)
                if not os.path.isfile(full):
                    continue
                _, ext = os.path.splitext(name)
                if ext.lower() not in allowed_exts:
                    continue
                with open(full, 'r', encoding='utf-8', errors='replace') as f:
                    files_payload.append({"name": name, "content": f.read()})
        resp = make_response(json.dumps({"files": files_payload}), HTTPStatus.OK)
        resp.headers["Content-Type"] = "application/json; charset=utf-8"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # Download mode (used by StudentList download) stays as attachments/zip
    if not code_output or not os.path.exists(code_output):
        return make_response("Not Found", HTTPStatus.NOT_FOUND)

    if not os.path.isdir(code_output):
        resp = send_file(
            code_output,
            as_attachment=True,
            download_name=os.path.basename(code_output),
        )
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
        return resp

    # If it's a directory, zip all relevant source files and return the zip
    allowed_exts = {".py", ".java", ".c", ".h", ".rkt"}
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # add files with stable ordering
        names = sorted(os.listdir(code_output), key=lambda n: (n != "Main.java", n.lower()))
        for name in names:
            full = os.path.join(code_output, name)
            if not os.path.isfile(full):
                continue
            _, ext = os.path.splitext(name)
            if ext.lower() not in allowed_exts:
                continue
            z.write(full, arcname=name)
    buf.seek(0)

    zip_name = f"submission_{submissionid}.zip"
    resp = send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zip_name,
    )
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return resp


def office_hours_admin_queue_payload(class_id: int) -> dict:
    expire_office_hours_entries(class_id)

    active_rows = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.ClassId == int(class_id),
        OfficeHoursQueueEntry.CompletedAt.is_(None),
    ).order_by(
        OfficeHoursQueueEntry.JoinedAt.asc(),
        OfficeHoursQueueEntry.Id.asc(),
    ).all()

    active_entries = [
        serialize_office_hours_entry(row, include_student=True)
        for row in active_rows
        if office_hours_status_for_row(row) in ("waiting", "being_helped")
    ]
    waiting = [entry for entry in active_entries if entry["status"] == "waiting"]
    helping = [entry for entry in active_entries if entry["status"] == "being_helped"]

    completed_rows = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.ClassId == int(class_id),
        OfficeHoursQueueEntry.CompletedAt.isnot(None),
    ).order_by(
        OfficeHoursQueueEntry.CompletedAt.desc(),
        OfficeHoursQueueEntry.Id.desc(),
    ).all()
    completed_entries = [
        serialize_office_hours_entry(row, include_student=True)
        for row in completed_rows
    ]
    helped = [
        entry
        for entry in completed_entries
        if entry["status"] in ("helped", "expired_waiting")
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
        "server_time": serialize_cooldown_lifted_at(current_utc_datetime()),
        "wait_duration_minutes": OFFICE_HOURS_WAIT_MINUTES,
        "help_duration_minutes": OFFICE_HOURS_HELP_MINUTES,
    }


@submission_api.route('/office-hours/session', methods=['POST'])
@jwt_required()
def office_hours_start_session():
    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)

    if class_id <= 0:
        return make_response(
            {"message": "class_id is required."},
            HTTPStatus.BAD_REQUEST,
        )
    if not user_can_access_class_id(class_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    ensure_office_hours_tables()
    now = current_utc_datetime()

    # Serialize starts per class so two admins cannot create overlapping windows.
    Classes.query.filter(Classes.Id == class_id).with_for_update().first()
    existing = OfficeHoursSession.query.filter(
        OfficeHoursSession.ClassId == class_id,
        OfficeHoursSession.StartedAt <= now,
        OfficeHoursSession.EndsAt > now,
    ).order_by(
        OfficeHoursSession.StartedAt.desc(),
        OfficeHoursSession.Id.desc(),
    ).with_for_update().first()

    if existing is not None:
        db.session.rollback()
        payload = office_hours_admin_queue_payload(class_id)
        payload["message"] = "Office hours are already active for this class."
        return jsonify(payload)

    # Close any legacy/stale active queue rows before opening a new window.
    stale_rows = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.ClassId == class_id,
        OfficeHoursQueueEntry.CompletedAt.is_(None),
    ).with_for_update().all()
    for row in stale_rows:
        row.CompletedAt = now
        if (
            getattr(row, "CooldownExemptUntil", None) is not None
            and row.CooldownExemptUntil > now
        ):
            row.CooldownExemptUntil = now

    session = OfficeHoursSession(
        ClassId=class_id,
        StartedAt=now,
        EndsAt=now + timedelta(minutes=OFFICE_HOURS_SESSION_MINUTES),
        StartedByUserId=current_user_id(),
    )
    db.session.add(session)
    db.session.commit()

    payload = office_hours_admin_queue_payload(class_id)
    payload["message"] = (
        f"Office hours started. Students can join for the next "
        f"{OFFICE_HOURS_SESSION_MINUTES // 60} hours."
    )
    return make_response(jsonify(payload), HTTPStatus.CREATED)


@submission_api.route('/office-hours/status', methods=['GET'])
@jwt_required()
def office_hours_student_status():
    class_id = parse_int(request.args.get("class_id", 0), 0)
    module_id = parse_int(request.args.get("module_id", 0), 0)
    _, error = validate_student_office_hours_scope(class_id, module_id)
    if error is not None:
        return error

    expire_office_hours_entries(class_id)
    row = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.UserId == current_user_id(),
        OfficeHoursQueueEntry.ClassId == class_id,
        OfficeHoursQueueEntry.CompletedAt.is_(None),
    ).order_by(
        OfficeHoursQueueEntry.JoinedAt.desc(),
        OfficeHoursQueueEntry.Id.desc(),
    ).first()

    payload = serialize_office_hours_entry(row) if row is not None else empty_office_hours_status()
    payload.update(serialize_office_hours_session(class_id))
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@submission_api.route('/office-hours/queue', methods=['GET', 'POST', 'DELETE'])
@jwt_required()
def office_hours_queue():
    if request.method == 'GET':
        class_id = parse_int(request.args.get("class_id", 0), 0)
        if class_id <= 0:
            return make_response(
                {"message": "class_id is required."},
                HTTPStatus.BAD_REQUEST,
            )
        if not user_can_access_class_id(class_id):
            return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

        response = jsonify(office_hours_admin_queue_payload(class_id))
        response.headers["Cache-Control"] = "no-store"
        return response

    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)
    module_id = parse_int(data.get("module_id", 0), 0)
    _, error = validate_student_office_hours_scope(class_id, module_id)
    if error is not None:
        return error

    expire_office_hours_entries(class_id)
    user_id = current_user_id()

    if request.method == 'POST':
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

    row = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.UserId == user_id,
        OfficeHoursQueueEntry.ClassId == class_id,
        OfficeHoursQueueEntry.CompletedAt.is_(None),
    ).order_by(
        OfficeHoursQueueEntry.JoinedAt.desc(),
        OfficeHoursQueueEntry.Id.desc(),
    ).with_for_update().first()
    now = current_utc_datetime()

    if request.method == 'DELETE':
        if row is not None:
            row.CompletedAt = now
            if (
                getattr(row, "CooldownExemptUntil", None) is not None
                and row.CooldownExemptUntil > now
            ):
                row.CooldownExemptUntil = now
            db.session.commit()
        else:
            db.session.rollback()

        payload = empty_office_hours_status()
        payload.update(serialize_office_hours_session(class_id))
        return jsonify(payload)

    if row is not None and office_hours_status_for_row(row) in ("waiting", "being_helped"):
        if int(row.ModuleId) != module_id:
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
    db.session.add(row)
    db.session.commit()

    payload = serialize_office_hours_entry(row)
    payload.update(serialize_office_hours_session(class_id))
    return make_response(
        jsonify(payload),
        HTTPStatus.CREATED,
    )


@submission_api.route('/office-hours/help', methods=['POST'])
@jwt_required()
def office_hours_help_student():
    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)
    student_id = parse_int(data.get("student_id", 0), 0)

    if class_id <= 0 or student_id <= 0:
        return make_response(
            {"message": "class_id and student_id are required."},
            HTTPStatus.BAD_REQUEST,
        )
    if not user_can_access_class_id(class_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)
    if not user_is_student_in_class_id(student_id, class_id):
        return make_response(
            {"message": "Student is not enrolled in this class."},
            HTTPStatus.BAD_REQUEST,
        )

    expire_office_hours_entries(class_id)
    session = active_office_hours_session(class_id)
    if session is None:
        return make_response(
            {"message": "Office hours are not active for this class."},
            HTTPStatus.CONFLICT,
        )

    row = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.ClassId == class_id,
        OfficeHoursQueueEntry.UserId == student_id,
        OfficeHoursQueueEntry.CompletedAt.is_(None),
        OfficeHoursQueueEntry.SelectedAt.is_(None),
    ).order_by(
        OfficeHoursQueueEntry.JoinedAt.desc(),
        OfficeHoursQueueEntry.Id.desc(),
    ).with_for_update().first()

    if row is None:
        db.session.rollback()
        return make_response(
            {"message": "Student is no longer waiting for office hours."},
            HTTPStatus.NOT_FOUND,
        )

    now = current_utc_datetime()
    row.SelectedAt = now
    row.SelectedByUserId = current_user_id()
    session_ends_at = parse_cooldown_lifted_at(getattr(session, "EndsAt", None))
    requested_exemption_end = now + timedelta(minutes=OFFICE_HOURS_HELP_MINUTES)
    row.CooldownExemptUntil = (
        min(requested_exemption_end, session_ends_at)
        if session_ends_at is not None
        else requested_exemption_end
    )
    db.session.commit()

    payload = office_hours_admin_queue_payload(class_id)
    payload["message"] = (
        f"Started helping the student. Cooldown-free submissions are enabled "
        f"for up to {OFFICE_HOURS_HELP_MINUTES} minutes."
    )
    return jsonify(payload)


@submission_api.route('/office-hours/complete', methods=['POST'])
@jwt_required()
def office_hours_complete_student():
    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)
    student_id = parse_int(data.get("student_id", 0), 0)

    if class_id <= 0 or student_id <= 0:
        return make_response(
            {"message": "class_id and student_id are required."},
            HTTPStatus.BAD_REQUEST,
        )
    if not user_can_access_class_id(class_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    expire_office_hours_entries(class_id)
    row = OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.ClassId == class_id,
        OfficeHoursQueueEntry.UserId == student_id,
        OfficeHoursQueueEntry.CompletedAt.is_(None),
        OfficeHoursQueueEntry.SelectedAt.isnot(None),
    ).order_by(
        OfficeHoursQueueEntry.SelectedAt.desc(),
        OfficeHoursQueueEntry.Id.desc(),
    ).with_for_update().first()

    if row is None:
        db.session.rollback()
        return make_response(
            {"message": "The student does not have an active help session."},
            HTTPStatus.NOT_FOUND,
        )

    now = current_utc_datetime()
    row.CompletedAt = now
    if (
        getattr(row, "CooldownExemptUntil", None) is not None
        and row.CooldownExemptUntil > now
    ):
        row.CooldownExemptUntil = now
    db.session.commit()

    payload = office_hours_admin_queue_payload(class_id)
    payload["message"] = "The help session ended."
    return jsonify(payload)


@submission_api.route('/incentive-state', methods=['GET'])
@jwt_required()
def incentive_state():
    class_id = parse_int(request.args.get("class_id", 0), 0)
    project_id = parse_int(request.args.get("project_id", 0), 0)
    checkpoint = parse_bool(request.args.get("checkpoint", False))
    checkpoint_id = parse_int(request.args.get("checkpoint_id", 0), 0) if checkpoint else 0

    if class_id <= 0:
        return make_response({"message": "class_id is required."}, HTTPStatus.BAD_REQUEST)

    if project_id > 0 and not current_user_can_use_upload_state(class_id, project_id, checkpoint_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    response = jsonify(
        incentive_payload(
            int(current_user.Id),
            class_id,
            project_id,
            checkpoint,
            checkpoint_id,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@submission_api.route('/skip-submission-cooldown', methods=['POST'])
@jwt_required()
def skip_submission_cooldown():
    data = request.get_json(silent=True) or {}
    class_id, project_id, checkpoint, checkpoint_id = upload_state_scope_from_mapping(data)

    if class_id <= 0 or project_id <= 0:
        return make_response({"message": "class_id and project_id are required."}, HTTPStatus.BAD_REQUEST)

    if not current_user_can_use_upload_state(class_id, project_id, checkpoint_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    cooldown_state = submission_cooldown_state(
        int(current_user.Id),
        class_id,
        project_id,
        checkpoint,
        checkpoint_id,
    )
    remaining_seconds = int(cooldown_state["cooldown_remaining_seconds"])
    if remaining_seconds <= 0:
        row = get_student_upload_state_row(class_id, project_id, checkpoint_id)
        if row is not None:
            db.session.delete(row)
            db.session.commit()
        payload = incentive_payload(int(current_user.Id), class_id, project_id, checkpoint, checkpoint_id)
        payload["cooldown_remaining_seconds"] = 0
        return jsonify(payload)

    ensure_incentive_tables()

    # Serialize purchases for this student while the balance is calculated.
    locked_user = Users.query.filter(Users.Id == int(current_user.Id)).with_for_update().first()
    if locked_user is None:
        db.session.rollback()
        return make_response({"message": "User not found."}, HTTPStatus.NOT_FOUND)

    cooldown_skip_cost = submission_cooldown_skip_cost(checkpoint)
    cooldown_skip_star_label = "star" if cooldown_skip_cost == 1 else "stars"
    balance = get_star_balance(int(current_user.Id), class_id)
    if balance < cooldown_skip_cost:
        db.session.rollback()
        return make_response({
            "message": f"You need {cooldown_skip_cost} {cooldown_skip_star_label} to skip the submission timer.",
            "stars": balance,
            "required_stars": cooldown_skip_cost,
            "cooldown_remaining_seconds": remaining_seconds,
        }, HTTPStatus.BAD_REQUEST)

    db.session.add(StudentCooldownSkips(
        UserId=int(current_user.Id),
        ClassId=int(class_id),
        ProjectId=int(project_id),
        CheckpointId=int(checkpoint_id or 0),
        SpentStars=cooldown_skip_cost,
        CreatedAt=current_utc_datetime(),
        UsedAt=None,
    ))

    row = get_student_upload_state_row(class_id, project_id, checkpoint_id)
    if row is not None:
        db.session.delete(row)

    db.session.commit()

    payload = incentive_payload(int(current_user.Id), class_id, project_id, checkpoint, checkpoint_id)
    payload["cooldown_remaining_seconds"] = 0
    payload["skipped_cooldown"] = True
    return jsonify(payload)

@submission_api.route('/student-upload-state', methods=['GET', 'POST', 'DELETE'])
@jwt_required()
@inject
def student_upload_state(submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    source = request.args if request.method == 'GET' else (request.get_json(silent=True) or {})
    class_id, project_id, checkpoint, checkpoint_id = upload_state_scope_from_mapping(source)

    if class_id <= 0 or project_id <= 0:
        return make_response({"message": "class_id and project_id are required."}, HTTPStatus.BAD_REQUEST)

    if not current_user_can_use_upload_state(class_id, project_id, checkpoint_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    row = get_student_upload_state_row(class_id, project_id, checkpoint_id)

    if request.method == 'GET':
        response = jsonify(
            serialize_student_upload_state(
                submission_repo,
                class_id,
                project_id,
                checkpoint,
                checkpoint_id,
            )
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    if request.method == 'DELETE':
        if row is not None:
            db.session.delete(row)
            db.session.commit()

        return jsonify({
            "last_submission_id": None,
            "previous_submission_id": None,
            "cooldown_lifted_at": None,
            "cooldown_remaining_seconds": 0,
        })

    if row is not None:
        db.session.delete(row)
        db.session.commit()

    return jsonify(
        serialize_student_upload_state(
            submission_repo,
            class_id,
            project_id,
            checkpoint,
            checkpoint_id,
        )
    )


@submission_api.route('/recentsubproject', methods=['POST'])
@jwt_required()
@inject
def recentsubproject(submission_repo: SubmissionRepository = Provide[Container.submission_repo], user_repo: UserRepository = Provide[Container.user_repo], project_repo: ProjectRepository = Provide[Container.project_repo]):
    input_json = request.get_json() or {}
    projectid = input_json['project_id']

    if not user_can_access_project_id(int(projectid)):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    checkpoint_raw = input_json.get('checkpoint', False)
    checkpoint = str(checkpoint_raw).strip().lower() in ('1', 'true', 'yes', 'y', 'on')

    ppid_raw = input_json.get('checkpoint_id', None)
    try:
        checkpoint_id = int(ppid_raw) if ppid_raw is not None else None
    except (TypeError, ValueError):
        checkpoint_id = None

    project = project_repo.get_selected_project(projectid)
    if project is None:
        return make_response("Not Found", HTTPStatus.NOT_FOUND)

    class_id = int(getattr(project, "ClassId", 0) or 0)
    users = [
        user
        for user in user_repo.get_all_users_by_cid(class_id)
        if user_is_student_in_class_id(int(getattr(user, "Id", 0) or 0), class_id)
    ]

    studentattempts = {}
    userids = [user.Id for user in users]

    if checkpoint and hasattr(Submissions, "IsCheckpoint"):
        bucket = {}
        submission_counter_dict = {uid: 0 for uid in userids}

        q = Submissions.query.filter(
            Submissions.Project == projectid,
            Submissions.User.in_(userids),
            Submissions.IsCheckpoint == True,
        )

        if checkpoint_id is not None and hasattr(Submissions, "CheckpointId"):
            q = q.filter(Submissions.CheckpointId == checkpoint_id)

        subs = q.order_by(Submissions.User.asc(), Submissions.Time.desc()).all()

        for s in subs:
            uid = int(getattr(s, "User", 0) or 0)
            if uid in submission_counter_dict:
                submission_counter_dict[uid] += 1
                if uid not in bucket:
                    bucket[uid] = s
    else:
        bucket = submission_repo.get_most_recent_submission_by_project(projectid, userids)
        submission_counter_dict = submission_repo.submission_counter(projectid, userids)

    user_lectures_dict = user_repo.get_user_lectures(userids, class_id)
    user_labs_dict = user_repo.get_user_labs(userids, class_id)

    for user in users:
        if user.Id in bucket:
            if checkpoint:
                manual_grade = submission_repo.get_manual_grade_for_submission(bucket[user.Id].Id)
                student_grade = manual_grade.get('grade') if manual_grade and manual_grade.get('grade') is not None else 0
            else:
                student_grade = project_repo.get_student_grade(projectid, user.Id)

            student_id = user_repo.get_StudentNumber(user.Id)
            studentattempts[user.Id] = [
                user.Lastname,
                user.Firstname,
                user_lectures_dict.get(user.Id, ""),
                user_labs_dict.get(user.Id, ""),
                submission_counter_dict.get(user.Id, 0),
                bucket[user.Id].Time.isoformat(),
                bucket[user.Id].IsPassing,
                bucket[user.Id].Id,
                str(class_id),
                student_grade,
                student_id,
                user.IsLocked,
            ]
        else:
            student_id = user_repo.get_StudentNumber(user.Id)
            studentattempts[user.Id] = [
                user.Lastname,
                user.Firstname,
                user_lectures_dict.get(user.Id, ""),
                user_labs_dict.get(user.Id, ""),
                "N/A",
                "N/A",
                "N/A",
                "N/A",
                -1,
                str(class_id),
                "0",
                student_id,
                user.IsLocked,
            ]

    return make_response(json.dumps(studentattempts), HTTPStatus.OK)

@submission_api.route('/GetSubmissionDetails', methods=['GET'])
@jwt_required()
@inject
def get_submission_details(project_repo: ProjectRepository = Provide[Container.project_repo]):
    class_id = int(request.args.get("class_id"))
    if not current_user_can_view_class_id(class_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    proj = project_repo.get_current_project_by_class(class_id)
    if proj is None:
        return make_response(["None", "0", "None", "", "", "-1"], HTTPStatus.OK)

    end_val = getattr(proj, "End", None)
    if isinstance(end_val, datetime):
        end_str = end_val.isoformat(timespec="seconds")
    else:
        end_str = str(end_val or "")

    # Preserve the existing array shape used by StudentUpload, but removed timing fields are inert.
    return make_response([
        "None",
        "0",
        "None",
        str(getattr(proj, "Name", "") or ""),
        end_str,
        str(int(getattr(proj, "Id", 0) or 0)),
    ], HTTPStatus.OK)


@submission_api.route('/submitgrades', methods=['POST'])
@jwt_required()
@inject
def submit_grades(project_repo: ProjectRepository = Provide[Container.project_repo]):
    #spacing issue
    data = request.get_json() or {}
    project_id = data['projectID']
    if not user_can_access_project_id(int(project_id)):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)
    userId = data['userId']
    grade = data['grade']
    project_repo.set_student_grade(int(project_id), int(userId), int(grade))
    return make_response("Grades Submitted", HTTPStatus.OK)

@submission_api.route('/getprojectscores', methods=['GET'])
@jwt_required()
@inject
def getprojectscores(project_repo: ProjectRepository = Provide[Container.project_repo], submission_repo: SubmissionRepository = Provide[Container.submission_repo], user_repo: UserRepository = Provide[Container.user_repo]):
    project_id = str(request.args.get("projectID"))
    if not user_can_access_project_id(int(project_id)):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    data = []
    student_scores = submission_repo.get_project_scores(project_id)
    projectname = project_repo.get_selected_project(project_id).Name
    for score in student_scores:
        user_info = user_repo.get_user(score[0])
        data.append([user_info.StudentNumber, score[1], user_info.Id])
    return make_response(json.dumps({"studentData": data, "projectName": projectname}), HTTPStatus.OK)

@submission_api.route('/submit_suggestion', methods=['POST'])
@jwt_required()
@inject
def submit_Suggestion(submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    data = request.get_json()
    suggestion = data['suggestion']
    submission_repo.submitSuggestion(current_user.Id ,suggestion)
    return make_response("Suggestion Submitted", HTTPStatus.OK)


@submission_api.route('/log_ui', methods=['POST'])
@jwt_required()
def log_ui_click():
    data = request.get_json(silent=True) or {}
    class_id = data.get('class_id', -1)
    submission_id = data.get('id', -1)
    action = str(data.get('action', '')).strip()
    started_state = data.get('started_state', None)
    previous_state_label = data.get('previous_state_label', None)
    next_state_label = data.get('next_state_label', None)
    checkpoint = parse_bool(data.get('checkpoint', False))
    checkpoint_id = data.get('checkpoint_id', None)

    username = getattr(current_user, 'Username', None) or 'unknown'
    role = current_user_effective_role()

    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    log_path = ui_clicks_log
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    line = (
        f"{ts} | user:{username} | role:{role} | class:{class_id} | submission:{submission_id}"
        f" | action:{action} | checkpoint:{checkpoint}"
    )
    if checkpoint_id not in (None, ''):
        line += f" | checkpoint_id:{checkpoint_id}"
    if action == 'Diff Finder' and started_state is not None:
        line += f" | started:{bool(started_state)}"
    if previous_state_label not in (None, ''):
        line += f" | from:{previous_state_label}"
    if next_state_label not in (None, ''):
        line += f" | to:{next_state_label}"

    line += "\n"

    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(line)
    return make_response({'status': 'logged'}, HTTPStatus.CREATED)

@submission_api.route('/save-grading', methods=['POST'])
@jwt_required()
@inject
def save_grading(submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    
    # get the data from frontend
    input_json = request.get_json()
    submission_id = input_json.get('submissionId')
    submission = submission_repo.get_submission_by_submission_id(int(submission_id))
    if not user_can_access_submission(submission):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    grade = input_json.get('grade')
    scoring_mode = input_json.get('scoringMode')
    error_points = input_json.get('errorPoints')
    error_defs = input_json.get('errorDefs')
    errors = input_json.get('errors')  # Expecting list: [{startLine,endLine,errorId,count}, ...]
    checkpoint = str(input_json.get('checkpoint', False)).strip().lower() in ('1', 'true', 'yes', 'y', 'on')
    checkpoint_id_raw = input_json.get('checkpoint_id', None)
    try:
        checkpoint_id = int(checkpoint_id_raw) if checkpoint_id_raw not in (None, '') else None
    except (TypeError, ValueError):
        checkpoint_id = None

    success = submission_repo.save_manual_grading(
        submission_id,
        grade,
        scoring_mode,
        error_points,
        errors,
        error_defs,
        checkpoint=checkpoint,
        checkpoint_id=checkpoint_id,
    )

    # 3. Respond to the frontend
    if success:
        return make_response(json.dumps({'success': True, 'msg': 'Grading saved'}), HTTPStatus.OK)
    else:
        return make_response("Failed to save grading", HTTPStatus.INTERNAL_SERVER_ERROR)

@submission_api.route('/get-grading/<int:submission_id>', methods=['GET'])
@jwt_required()
@inject
def get_grading(submission_id, submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    submission = submission_repo.get_submission_by_submission_id(int(submission_id))
    if not user_can_access_submission(submission):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    # get errors from db
    error_list = submission_repo.get_manual_errors(submission_id)
    
    cfg = submission_repo.get_manual_grade_config(submission_id)
    return jsonify({
        'success': True,
        'errors': error_list,
        'grade': cfg.get('grade'),
        'scoringMode': cfg.get('scoringMode'),
        'errorPoints': cfg.get('errorPoints'),
        'errorDefs': cfg.get('errorDefs'),
    })

@submission_api.route('/exportprojectgrades', methods=['GET'])
@jwt_required()
@inject
def export_project_grades(submission_repo: SubmissionRepository = Provide[Container.submission_repo], project_repo: ProjectRepository = Provide[Container.project_repo]):
    project_id = int(request.args.get("project_id"))
    if not user_can_access_project_id(project_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    checkpoint = str(request.args.get("checkpoint", False)).strip().lower() in ('1', 'true', 'yes', 'y', 'on')
    checkpoint_id_raw = request.args.get("checkpoint_id", None)
    try:
        checkpoint_id = int(checkpoint_id_raw) if checkpoint_id_raw not in (None, '') else None
    except (TypeError, ValueError):
        checkpoint_id = None

    grade_list = submission_repo.get_project_grade_info(project_id, checkpoint=checkpoint, checkpoint_id=checkpoint_id)
    project_name = project_repo.get_selected_project(project_id).Name

    sio = StringIO()
    writer = csv.writer(sio, lineterminator="\n")

    headers = ['OrgDefinedId', f'{project_name} Points Grade', f'{project_name} Text Grade', 'End-of-Line Indicator']
    writer.writerow(headers)

    # Create excel rows
    base_defs_map = dict(GRADING_DEFAULT_DEFS_MAP)

    for row in grade_list:
        pts_dict = row['points'] or {}
        scoring_mode = row['scoring_mode']
        error_data = row['description']
        row_defs = row.get('error_defs') or {}
        defs_map = dict(base_defs_map)
        for k, v in (row_defs.items() if isinstance(row_defs, dict) else []):
            if isinstance(v, dict):
                defs_map[str(k)] = {
                    'label': str(v.get('label', k)),
                    'description': str(v.get('description', '')),
                    'points': int(v.get('points', 0) or 0),
                }
        desc_lines = []
        description = ''

        for error in error_data:
            start = error['startLine']
            end = error['endLine']
            errorId = error['errorId']
            count = error['count']
            note = error.get('note', '')
            error_def = defs_map.get(errorId, {'label': errorId, 'description': '', 'points': 0})
            line_str = ''

            base_pts = int(error_def.get('points', 0) or 0)
            # ErrorPointsJson now stores overrides only. If there is no override, use base_pts.
            override_raw = pts_dict.get(errorId) if isinstance(pts_dict, dict) else None
            if override_raw is None:
                eff_pts = base_pts
            else:
                try:
                    eff_pts = max(0, int(override_raw))
                except Exception:
                    eff_pts = base_pts

            if scoring_mode == "perInstance":
                pts = eff_pts * count
                if count > 1:
                    line_str += f'{count}x '
            else:
                pts = eff_pts

            line_str += f"[{error_def.get('label', errorId)}] "

            if start == end:
                line_str += f'Line {start}'
            else:
                line_str += f'Lines {start}-{end}'

            line_str += f' (-{pts} pts):'
            desc_lines.append(line_str)

            if isinstance(note, str) and note.strip():
                desc_lines.append(f"Note: {note.strip()}")
            desc_lines.append('')

        if not error_data:
            desc_lines.append('Great Job!')

        description = "\n".join(desc_lines)
        writer.writerow([row.get('number'), row.get('grade'), description, '#'])

    buffer = BytesIO(sio.getvalue().encode("utf-8"))
    buffer.seek(0)

    resp = send_file(
        buffer,
        mimetype='text/csv; charset=utf-8',
        download_name=f'{project_name}-grades.csv',
        as_attachment=True
    )

    resp.headers["Project-Name"] = project_name
    resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition, Project-Name"
    resp.headers["Cache-Control"] = "no-store"
    return resp
