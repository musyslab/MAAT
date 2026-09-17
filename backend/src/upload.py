from flask.json import jsonify
import json
import os
import subprocess
import tempfile
from typing import Optional

from flask_jwt_extended import jwt_required
from flask_jwt_extended import current_user
from flask import Blueprint
from flask import request
from flask import make_response
from http import HTTPStatus
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from math import ceil
from dependency_injector.wiring import inject, Provide
from sqlalchemy import func

from container import Container
from src.constants import ADMIN_ROLE, STUDENT_ROLE, TEACHER_ROLE
from src.repositories.class_repository import ClassRepository
from src.repositories.models import (
    Checkpoints,
    Classes,
    ClassAssignments,
    OfficeHoursQueueEntry,
    OfficeHoursSession,
    Projects,
    StudentCheckpointSkips,
    StudentCooldownSkips,
    StudentStarAwards,
    StudentTestcaseInputPurchases,
    Submissions,
    Users,
)
from src.repositories.project_repository import ProjectRepository
from src.repositories.database import db
from src.repositories.submission_repository import SubmissionRepository
from src.repositories.user_repository import UserRepository

CHICAGO_TIMEZONE = ZoneInfo("America/Chicago")


def submission_datetime_utc(value: datetime) -> datetime:
    """Convert stored Chicago wall time to naive UTC for cooldown comparisons."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=CHICAGO_TIMEZONE)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


upload_api = Blueprint("upload_api", __name__)

ALLOWED_EXTENSIONS_BY_LANGUAGE = {
    "py": [".py"],
    "python": [".py"],
    "python3": [".py"],
    "java": [".java"],
    "c": [".c"],
    "racket": [".rkt"],
    "rkt": [".rkt"],
    "scheme": [".rkt"],
}

ALLOWED_SOURCE_EXTENSIONS = {".py", ".java", ".c", ".rkt"}
CHECKPOINT_SUBMISSION_COOLDOWN_AFTER_ATTEMPT = {1: 0, 2: 60, 3: 120, 4: 300}
CHECKPOINT_SUBMISSION_COOLDOWN_MAX_SECONDS = 300
MAIN_SUBMISSION_COOLDOWN_AFTER_ATTEMPT = {1: 0, 2: 120, 3: 300, 4: 600}
MAIN_SUBMISSION_COOLDOWN_MAX_SECONDS = 1200
CHECKPOINT_COMPLETION_STARS = 1
MAIN_PROJECT_COMPLETION_STARS = 3
EARLY_START_MULTIPLIER = 2
PYTHON_IDE_MAX_SOURCE_BYTES = 256 * 1024
PYTHON_IDE_MAX_STDIN_BYTES = 64 * 1024
PYTHON_IDE_MAX_OUTPUT_BYTES = 256 * 1024
PYTHON_IDE_TIMEOUT_SECONDS = 120


def current_star_balance(user_id: int, class_id: int) -> int:
    """Return awards minus every star purchase made in the class."""
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


def project_window(project):
    module = getattr(project, "Module", None)
    if module is None:
        module_id = parse_int(getattr(project, "ModuleId", 0), 0)
        if module_id > 0:
            try:
                from src.repositories.models import Modules

                module = Modules.query.filter(Modules.Id == module_id).first()
            except Exception:
                module = None

    start = getattr(module, "Start", None) if module else None
    end = getattr(module, "End", None) if module else None

    return start, end


def assignment_started_early(user_id: int, project, is_checkpoint: bool, checkpoint_id: int | None) -> bool:
    start, end = project_window(project)
    if not isinstance(start, datetime) or not isinstance(end, datetime) or end <= start:
        return False

    query = Submissions.query.filter(
        Submissions.User == int(user_id),
        Submissions.Project == int(project.Id),
        Submissions.IsCheckpoint == bool(is_checkpoint),
    )

    if is_checkpoint:
        query = query.filter(Submissions.CheckpointId == int(checkpoint_id or 0))
    else:
        query = query.filter(Submissions.CheckpointId.is_(None))

    rows = query.order_by(Submissions.Time.asc()).all()
    first_started_at = None

    for row in rows:
        parsed = parse_submission_datetime(getattr(row, "Time", None))
        if parsed is None:
            continue
        if first_started_at is None or parsed < first_started_at:
            first_started_at = parsed

    if first_started_at is None:
        return False

    midpoint = start + ((end - start) / 2)
    return first_started_at <= midpoint


def award_completion_stars(
    user_id: int,
    class_id: int,
    project,
    is_checkpoint: bool,
    checkpoint_id: int | None,
    submission_id: int,
) -> dict | None:

    if project is None or submission_id is None:
        return None

    checkpoint_key = int(checkpoint_id or 0) if is_checkpoint else 0
    award_type = "checkpoint_completion" if is_checkpoint else "main_completion"

    existing = StudentStarAwards.query.filter(
        StudentStarAwards.UserId == int(user_id),
        StudentStarAwards.ClassId == int(class_id),
        StudentStarAwards.ProjectId == int(project.Id),
        StudentStarAwards.CheckpointId == checkpoint_key,
        StudentStarAwards.AwardType == award_type,
    ).first()

    if existing is not None:
        return {
            "awarded": False,
            "stars": 0,
            "balance": current_star_balance(user_id, class_id),
            "reason": "already_awarded",
        }

    base_stars = CHECKPOINT_COMPLETION_STARS if is_checkpoint else MAIN_PROJECT_COMPLETION_STARS
    started_early = assignment_started_early(user_id, project, is_checkpoint, checkpoint_id)
    multiplier = EARLY_START_MULTIPLIER if started_early else 1
    stars = base_stars * multiplier

    row = StudentStarAwards(
        UserId=int(user_id),
        ClassId=int(class_id),
        ProjectId=int(project.Id),
        CheckpointId=checkpoint_key,
        AwardType=award_type,
        AwardedStars=int(stars),
        BaseAwardStars=int(base_stars),
        Multiplier=int(multiplier),
        StartedEarly=bool(started_early),
        SubmissionId=int(submission_id),
        AwardedAt=datetime.now(),
    )
    db.session.add(row)
    db.session.commit()

    balance = current_star_balance(user_id, class_id)

    return {
        "awarded": True,
        "stars": int(stars),
        "base_stars": int(base_stars),
        "multiplier": int(multiplier),
        "started_early": bool(started_early),
        "balance": int(balance),
        "award_type": award_type,
    }


def consume_pending_cooldown_skip(
    user_id: int,
    class_id: int,
    project_id: int,
    checkpoint_id: int,
    latest_submission_time: datetime | None,
) -> bool:
    query = StudentCooldownSkips.query.filter(
        StudentCooldownSkips.UserId == int(user_id),
        StudentCooldownSkips.ClassId == int(class_id),
        StudentCooldownSkips.ProjectId == int(project_id),
        StudentCooldownSkips.CheckpointId == int(checkpoint_id or 0),
        StudentCooldownSkips.UsedAt.is_(None),
    )

    if latest_submission_time is not None:
        submission_time_utc = submission_datetime_utc(latest_submission_time)
        query = query.filter(StudentCooldownSkips.CreatedAt >= submission_time_utc)

    row = query.order_by(StudentCooldownSkips.CreatedAt.asc()).first()
    if row is None:
        return False

    row.UsedAt = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    return True


def parse_int(v, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


def parse_bool(v) -> bool:
    if isinstance(v, bool):
        return v

    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def current_user_id() -> int:
    return parse_int(getattr(current_user, "Id", 0), 0)


def current_user_global_role() -> int:
    user_id = current_user_id()

    if user_id <= 0:
        return STUDENT_ROLE

    role_rows = db.session.query(ClassAssignments.Role).filter(
        ClassAssignments.UserId == user_id
    ).all()

    roles = []
    for role_row in role_rows:
        if hasattr(role_row, "Role"):
            role_value = role_row.Role
        elif isinstance(role_row, (tuple, list)):
            role_value = role_row[0]
        else:
            role_value = role_row

        roles.append(parse_int(role_value, STUDENT_ROLE))

    return max([STUDENT_ROLE] + roles)


def is_admin_user() -> bool:
    return current_user_global_role() >= ADMIN_ROLE


def is_teacher_user() -> bool:
    return current_user_global_role() == TEACHER_ROLE


def class_assignment_for_user(class_id: int, user_id: int):
    class_id = parse_int(class_id, 0)
    user_id = parse_int(user_id, 0)

    if class_id <= 0 or user_id <= 0:
        return None

    try:
        return ClassAssignments.query.filter(
            ClassAssignments.ClassId == class_id,
            ClassAssignments.UserId == user_id,
        ).first()
    except Exception:
        return None


def current_user_assignment_role_for_class(class_id: int) -> int | None:
    assignment = class_assignment_for_user(class_id, current_user_id())

    if assignment is None:
        return None

    return parse_int(getattr(assignment, "Role", None), STUDENT_ROLE)


def user_id_is_enrolled_in_class(user_id: int, class_id: int) -> bool:
    return class_assignment_for_user(class_id, user_id) is not None


def current_user_is_enrolled_in_class(class_id: int) -> bool:
    return user_id_is_enrolled_in_class(current_user_id(), class_id)


def current_user_has_staff_assignment() -> bool:
    user_id = current_user_id()

    if user_id <= 0:
        return False

    try:
        return (
            ClassAssignments.query.filter(
                ClassAssignments.UserId == user_id,
                ClassAssignments.Role >= TEACHER_ROLE,
            ).first()
            is not None
        )
    except Exception:
        return False


def is_staff_user() -> bool:
    return current_user_global_role() >= TEACHER_ROLE or current_user_has_staff_assignment()


def user_can_access_class_id(class_id: int) -> bool:
    class_id = parse_int(class_id, 0)

    if class_id <= 0:
        return False

    class_item = Classes.query.filter(Classes.Id == class_id).first()
    if class_item is None:
        return False

    if is_admin_user():
        return True

    assignment_role = current_user_assignment_role_for_class(class_id)

    return assignment_role is not None and assignment_role >= TEACHER_ROLE


def normalize_grader_language(language: str, solution_root: str = "") -> str:
    raw = str(language or "").strip().lower()

    aliases = {
        "python": "py",
        "python3": "py",
        "py": "py",
        "java": "java",
        "c": "c",
        "racket": "racket",
        "rkt": "racket",
        "scheme": "racket",
        "scm": "racket",
    }

    if raw in aliases:
        return aliases[raw]

    try:
        candidates = []

        if solution_root and os.path.isdir(solution_root):
            candidates = [
                os.path.splitext(name)[1].lower()
                for name in os.listdir(solution_root)
            ]
        elif solution_root:
            candidates = [os.path.splitext(solution_root)[1].lower()]

        if ".py" in candidates:
            return "py"
        if ".java" in candidates:
            return "java"
        if ".c" in candidates:
            return "c"
        if ".rkt" in candidates:
            return "racket"
    except Exception:
        pass

    return raw or "py"


def allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False

    _, extension = os.path.splitext(filename)
    return extension.lower() in ALLOWED_SOURCE_EXTENSIONS


def expected_extensions_for_language(language: str) -> list[str]:
    raw = str(language or "").strip().lower()
    normalized = normalize_grader_language(raw)

    if raw in ALLOWED_EXTENSIONS_BY_LANGUAGE:
        return ALLOWED_EXTENSIONS_BY_LANGUAGE[raw]

    if normalized in ALLOWED_EXTENSIONS_BY_LANGUAGE:
        return ALLOWED_EXTENSIONS_BY_LANGUAGE[normalized]

    return []


def sanitize_fs_name(value: str) -> str:
    safe = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in str(value or "").strip()
    )

    return safe or "unknown"


def safe_upload_filename(filename: str) -> str:
    base = os.path.basename(filename or "")
    stem, extension = os.path.splitext(base)

    safe_stem = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in str(stem or "").strip()
    )

    return f"{safe_stem or 'submission'}{extension.lower()}"


def path_segment(value: str, fallback: str = "unnamed") -> str:
    safe = sanitize_fs_name(value)
    return safe if safe != "unknown" else fallback


def project_files_root() -> str:
    return "/tabot-files/project-files"


def student_root_for_class(class_id: int) -> str:
    class_item = Classes.query.filter(Classes.Id == int(class_id)).first()
    class_name = path_segment(getattr(class_item, "Name", "") if class_item else f"class_{class_id}", f"class_{class_id}")
    school = getattr(class_item, "School", None) if class_item else None
    school_name = path_segment(getattr(school, "Name", "") if school else "school", "school")
    return os.path.join(project_files_root(), school_name, class_name, "student-files")


def project_module(project):
    module = getattr(project, "Module", None)
    if module:
        return module
    module_id = getattr(project, "ModuleId", None)
    if module_id:
        from src.repositories.models import Modules
        return Modules.query.filter(Modules.Id == int(module_id)).first()
    return None


def module_folder_name(project, timestamp_hint: str) -> str:
    module = project_module(project)
    fallback_name = getattr(project, "Name", "") or "module"

    if module is None:
        return f"{timestamp_hint}_{path_segment(fallback_name, 'module')}"

    changed = False

    if not getattr(module, "FileTimestamp", None):
        module.FileTimestamp = timestamp_hint
        changed = True

    if not getattr(module, "FirstName", None):
        module.FirstName = getattr(module, "Name", None) or fallback_name
        changed = True

    if changed:
        db.session.commit()

    return f"{module.FileTimestamp}_{path_segment(module.FirstName, 'module')}"


def project_first_folder(project) -> str:
    if not getattr(project, "FirstName", None):
        project.FirstName = getattr(project, "Name", None) or "project"
        db.session.commit()
    return path_segment(project.FirstName, "project")


def checkpoint_first_folder(checkpoint: Checkpoints) -> str:
    if not getattr(checkpoint, "FirstName", None):
        checkpoint.FirstName = getattr(checkpoint, "Name", None) or "checkpoint"
        db.session.commit()
    return path_segment(checkpoint.FirstName, "checkpoint")


def student_project_bucket(class_id: int, project, checkpoint: Optional[Checkpoints], timestamp_hint: str) -> str:
    module_folder = module_folder_name(project, timestamp_hint)

    if checkpoint is not None:
        scope = "checkpoint"
        item_folder = checkpoint_first_folder(checkpoint)
    else:
        scope = "main"
        item_folder = project_first_folder(project)

    return os.path.join(
        student_root_for_class(class_id),
        module_folder,
        scope,
        item_folder,
    )


def resolve_additional_files_payload(owner, solution_path: str) -> str:
    try:
        teacher_base_dir = (
            solution_path
            if solution_path and os.path.isdir(solution_path)
            else os.path.dirname(solution_path or "")
        )

        raw = str(getattr(owner, "AdditionalFilePath", "") or "").strip()

        if not raw:
            return json.dumps({"base_dir": teacher_base_dir, "files": []})

        if raw.startswith("[") or raw.startswith("{"):
            parsed = json.loads(raw)
        else:
            parsed = [raw]

        if isinstance(parsed, dict):
            parsed = parsed.get("files", [])

        abs_list = []

        for path_value in parsed or []:
            if not path_value:
                continue

            path_text = str(path_value)

            if os.path.isabs(path_text):
                abs_list.append(path_text)
            else:
                abs_list.append(
                    os.path.join(teacher_base_dir, os.path.basename(path_text))
                )

        return json.dumps({"base_dir": teacher_base_dir, "files": abs_list})
    except Exception:
        return ""


def load_grader_status(json_out: str) -> tuple[bool, dict]:
    status = False
    testcase_results = {"Passed": [], "Failed": []}

    try:
        with open(json_out, "r", encoding="utf-8", errors="replace") as f:
            payload = json.load(f) or {}

        passed = []
        failed = []

        for result in payload.get("results", []):
            name = str((result or {}).get("name", "") or "")

            if bool((result or {}).get("passed", False)):
                passed.append(name)
            else:
                failed.append(name)

        status = len(failed) == 0
        testcase_results = {"Passed": passed, "Failed": failed}
    except Exception:
        pass

    return status, testcase_results


def parse_submission_datetime(value) -> datetime | None:
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


def active_office_hours_entry_for_project(
    user_id: int,
    class_id: int,
    project_id: int,
):
    """Return a help exemption only while the class office-hours window is active."""
    project = Projects.query.filter(
        Projects.Id == int(project_id),
        Projects.ClassId == int(class_id),
    ).first()
    module_id = parse_int(getattr(project, "ModuleId", 0), 0)
    if module_id <= 0:
        return None

    try:
        OfficeHoursSession.__table__.create(db.engine, checkfirst=True)
        OfficeHoursQueueEntry.__table__.create(db.engine, checkfirst=True)
    except Exception:
        pass

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = OfficeHoursSession.query.filter(
        OfficeHoursSession.ClassId == int(class_id),
        OfficeHoursSession.StartedAt <= now,
        OfficeHoursSession.EndsAt > now,
    ).order_by(
        OfficeHoursSession.StartedAt.desc(),
        OfficeHoursSession.Id.desc(),
    ).first()
    if session is None:
        return None

    return OfficeHoursQueueEntry.query.filter(
        OfficeHoursQueueEntry.UserId == int(user_id),
        OfficeHoursQueueEntry.ClassId == int(class_id),
        OfficeHoursQueueEntry.ModuleId == module_id,
        OfficeHoursQueueEntry.CompletedAt.is_(None),
        OfficeHoursQueueEntry.SelectedAt.isnot(None),
        OfficeHoursQueueEntry.CooldownExemptUntil > now,
        OfficeHoursQueueEntry.JoinedAt >= session.StartedAt,
        OfficeHoursQueueEntry.JoinedAt < session.EndsAt,
    ).first()


def serialize_utc_datetime(value) -> str | None:
    if not isinstance(value, datetime):
        return None

    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)

    return f"{value.isoformat()}Z"


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


def student_submission_scope_query(
    user_id: int,
    project_id: int,
    is_checkpoint: bool,
    checkpoint_id: int,
):
    query = Submissions.query.filter(
        Submissions.User == int(user_id),
        Submissions.Project == int(project_id),
        Submissions.IsCheckpoint == bool(is_checkpoint),
    )

    if is_checkpoint:
        return query.filter(Submissions.CheckpointId == int(checkpoint_id or 0))

    return query.filter(Submissions.CheckpointId.is_(None))


def student_submission_cooldown_response(
    user_id: int,
    class_id: int,
    project_id: int,
    is_checkpoint: bool,
    checkpoint_id: int,
):
    if active_office_hours_entry_for_project(user_id, class_id, project_id) is not None:
        return None

    scope_query = student_submission_scope_query(
        user_id,
        project_id,
        is_checkpoint,
        checkpoint_id,
    )
    completed_attempts = scope_query.count()

    if completed_attempts <= 0:
        return None

    latest = scope_query.order_by(Submissions.Time.desc(), Submissions.Id.desc()).first()
    submitted_at = parse_submission_datetime(getattr(latest, "Time", None))

    if submitted_at is None:
        return None

    cooldown_seconds = submission_cooldown_seconds_for_attempt_count(
        completed_attempts,
        is_checkpoint,
    )
    elapsed_seconds = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        - submission_datetime_utc(submitted_at)
    ).total_seconds()
    remaining_seconds = int(ceil(cooldown_seconds - elapsed_seconds))

    if remaining_seconds <= 0:
        return None

    if consume_pending_cooldown_skip(
        user_id,
        class_id,
        project_id,
        checkpoint_id if is_checkpoint else 0,
        submitted_at,
    ):
        return None

    next_attempt = completed_attempts + 1
    response = make_response(
        {
            "message": (
                f"Submission cooldown active. Attempt {next_attempt} is available in "
                f"{remaining_seconds} seconds. Test your code in your local deployment "
                "before submitting again."
            ),
            "retry_after_seconds": remaining_seconds,
            "cooldown_seconds": cooldown_seconds,
            "submission_attempt_count": completed_attempts,
            "next_attempt_number": next_attempt,
        },
        HTTPStatus.TOO_MANY_REQUESTS,
    )
    response.headers["Retry-After"] = str(remaining_seconds)
    return response


def student_upload_targets(
    project_repo: ProjectRepository,
    user_id: int,
    project_id: int,
) -> dict:
    """
    Return the assignment targets that are open to a student.

    This mirrors StudentModuleDetails: completed checkpoints stay open, the
    first incomplete checkpoint is open, later checkpoints are locked, and the
    main problem opens only after every enabled checkpoint is passed or skipped.
    """
    checkpoint_rows = project_repo.list_checkpoints(int(project_id))
    checkpoint_ids = [
        int(getattr(checkpoint, "Id", 0) or 0)
        for checkpoint in checkpoint_rows
        if int(getattr(checkpoint, "Id", 0) or 0) > 0
    ]

    passed_checkpoint_ids: set[int] = set()
    if checkpoint_ids:
        passed_rows = (
            db.session.query(Submissions.CheckpointId)
            .filter(
                Submissions.User == int(user_id),
                Submissions.Project == int(project_id),
                Submissions.IsCheckpoint == True,
                Submissions.IsPassing == True,
                Submissions.CheckpointId.in_(checkpoint_ids),
            )
            .distinct()
            .all()
        )
        passed_checkpoint_ids = {
            int(row[0])
            for row in passed_rows
            if row and row[0] is not None
        }

    skipped_rows = StudentCheckpointSkips.query.filter(
        StudentCheckpointSkips.UserId == int(user_id),
        StudentCheckpointSkips.ProjectId == int(project_id),
    ).all()
    skipped_checkpoint_ids = {
        int(getattr(row, "CheckpointId", 0) or 0)
        for row in skipped_rows
    }

    completed_checkpoint_ids = passed_checkpoint_ids | skipped_checkpoint_ids
    first_incomplete_index = next(
        (
            index
            for index, checkpoint_id in enumerate(checkpoint_ids)
            if checkpoint_id not in completed_checkpoint_ids
        ),
        None,
    )

    targets = []
    for index, checkpoint in enumerate(checkpoint_rows):
        checkpoint_id = int(getattr(checkpoint, "Id", 0) or 0)
        completed = checkpoint_id in completed_checkpoint_ids
        available = completed or index == first_incomplete_index

        targets.append(
            {
                "id": checkpoint_id,
                "number": index + 1,
                "name": str(
                    getattr(checkpoint, "Name", "")
                    or f"Checkpoint {index + 1}"
                ),
                "enabled": bool(getattr(checkpoint, "Enabled", True)),
                "completed": completed,
                "available": available,
            }
        )

    return {
        "checkpoints": targets,
        "mainAvailable": first_incomplete_index is None,
    }


def upload_target_order_error(
    project_repo: ProjectRepository,
    user_id: int,
    project_id: int,
    is_checkpoint: bool,
    checkpoint_id: int,
):
    targets = student_upload_targets(project_repo, user_id, project_id)

    if not is_checkpoint:
        if bool(targets.get("mainAvailable")):
            return None

        return make_response(
            {
                "message": (
                    "The main problem is locked for this student. "
                    "Complete or skip all checkpoints first."
                )
            },
            HTTPStatus.BAD_REQUEST,
        )

    checkpoint_target = next(
        (
            row
            for row in targets.get("checkpoints", [])
            if int(row.get("id", 0) or 0) == int(checkpoint_id)
        ),
        None,
    )

    if checkpoint_target is not None and bool(checkpoint_target.get("available")):
        return None

    return make_response(
        {
            "message": (
                "This checkpoint is locked for this student. "
                "Complete or skip earlier checkpoints first."
            )
        },
        HTTPStatus.BAD_REQUEST,
    )


@upload_api.route("/total_students_by_cid", methods=["GET"])
@jwt_required()
@inject
def total_students(user_repo: UserRepository = Provide[Container.user_repo]):
    class_id = request.args.get("class_id")
    class_id_int = parse_int(class_id, 0)

    if class_id_int <= 0:
        return make_response({"message": "Invalid class_id"}, HTTPStatus.BAD_REQUEST)

    if not user_can_access_class_id(class_id_int):
        return make_response({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    users = (
        db.session.query(Users)
        .join(ClassAssignments, ClassAssignments.UserId == Users.Id)
        .filter(
            ClassAssignments.ClassId == class_id_int,
            ClassAssignments.Role == STUDENT_ROLE,
        )
        .order_by(Users.Lastname.asc(), Users.Firstname.asc(), Users.Id.asc())
        .all()
    )

    list_of_user_info = []

    for user in users:
        list_of_user_info.append(
            {
                "name": f"{user.Firstname} {user.Lastname}".strip(),
                "mscsnet": user.Username,
                "id": user.Id,
            }
        )

    return jsonify(list_of_user_info)


@upload_api.route("/available_targets", methods=["GET"])
@jwt_required()
@inject
def available_targets(
    project_repo: ProjectRepository = Provide[Container.project_repo],
):
    class_id = parse_int(request.args.get("class_id"), 0)
    project_id = parse_int(request.args.get("project_id"), 0)
    student_id = parse_int(request.args.get("student_id"), 0)

    if class_id <= 0 or project_id <= 0 or student_id <= 0:
        return make_response(
            {"message": "class_id, project_id, and student_id are required"},
            HTTPStatus.BAD_REQUEST,
        )

    if not user_can_access_class_id(class_id):
        return make_response({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    if not user_id_is_enrolled_in_class(student_id, class_id):
        return make_response(
            {"message": "Student is not enrolled in this class"},
            HTTPStatus.FORBIDDEN,
        )

    project = project_repo.get_selected_project(project_id)
    if (
        project is None
        or int(getattr(project, "ClassId", 0) or 0) != class_id
    ):
        return make_response(
            {"message": "Project does not belong to this class"},
            HTTPStatus.BAD_REQUEST,
        )

    return jsonify(student_upload_targets(project_repo, student_id, project_id))


def resolve_python_ide_assignment(
    project_repo: ProjectRepository,
    class_id: int,
    project_id: int,
    checkpoint_id: int = 0,
):
    if class_id <= 0 or project_id <= 0:
        return None, (
            {"message": "class_id and project_id are required"},
            HTTPStatus.BAD_REQUEST,
        )

    is_staff = user_can_access_class_id(class_id)
    if not is_staff and not current_user_is_enrolled_in_class(class_id):
        return None, ({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    project = project_repo.get_selected_project(project_id)
    if (
        project is None
        or int(getattr(project, "ClassId", 0) or 0) != class_id
    ):
        return None, (
            {"message": "Project does not belong to this class"},
            HTTPStatus.BAD_REQUEST,
        )

    checkpoint = None
    if checkpoint_id > 0:
        checkpoint = project_repo.get_checkpoint(checkpoint_id)

        if checkpoint is None:
            return None, ({"message": "Checkpoint not found"}, HTTPStatus.NOT_FOUND)

        if int(getattr(checkpoint, "ProjectId", 0) or 0) != project_id:
            return None, (
                {"message": "Checkpoint does not belong to this project"},
                HTTPStatus.BAD_REQUEST,
            )

        if not is_staff and not bool(getattr(checkpoint, "Enabled", True)):
            return None, ({"message": "Checkpoint is disabled"}, HTTPStatus.FORBIDDEN)

    owner = checkpoint if checkpoint is not None else project
    solution_path = ""

    try:
        solution_path = project_repo.get_project_path(
            project_id,
            checkpoint_id=(checkpoint_id if checkpoint is not None else None),
        )
    except Exception:
        solution_path = ""

    if not solution_path:
        solution_path = str(getattr(owner, "solutionpath", "") or "")

    effective_language = (
        getattr(owner, "Language", None)
        or getattr(project, "Language", None)
        or ""
    )

    return {
        "project": project,
        "checkpoint": checkpoint,
        "language": normalize_grader_language(effective_language, solution_path),
    }, None


def truncate_ide_output(value: str) -> tuple[str, bool]:
    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= PYTHON_IDE_MAX_OUTPUT_BYTES:
        return str(value or ""), False

    clipped = encoded[:PYTHON_IDE_MAX_OUTPUT_BYTES].decode(
        "utf-8",
        errors="ignore",
    )
    return clipped, True


@upload_api.route("/ide-context", methods=["GET"])
@jwt_required()
@inject
def python_ide_context(
    project_repo: ProjectRepository = Provide[Container.project_repo],
):
    class_id = parse_int(request.args.get("class_id"), 0)
    project_id = parse_int(request.args.get("project_id"), 0)
    checkpoint_id = parse_int(request.args.get("checkpoint_id"), 0)
    context, error = resolve_python_ide_assignment(
        project_repo,
        class_id,
        project_id,
        checkpoint_id,
    )

    if error is not None:
        payload, status = error
        return make_response(payload, status)

    language = str(context.get("language", "") or "")
    return jsonify(
        {
            "language": language,
            "python_ide_enabled": language == "py",
            "default_filename": "main.py",
            "execution_provider": "judge0",
        }
    )


@upload_api.route("/run-python", methods=["POST"])
@jwt_required()
@inject
def run_python_from_ide(
    project_repo: ProjectRepository = Provide[Container.project_repo],
):
    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id"), 0)
    project_id = parse_int(data.get("project_id"), 0)
    checkpoint_id = parse_int(data.get("checkpoint_id"), 0)
    context, error = resolve_python_ide_assignment(
        project_repo,
        class_id,
        project_id,
        checkpoint_id,
    )

    if error is not None:
        payload, status = error
        return make_response(payload, status)

    if context.get("language") != "py":
        return make_response(
            {"message": "The Python IDE is only available for Python assignments."},
            HTTPStatus.BAD_REQUEST,
        )

    source = data.get("source", "")
    stdin_text = data.get("stdin", "")
    requested_filename = str(data.get("filename", "main.py") or "main.py")

    if not isinstance(source, str) or not source.strip():
        return make_response(
            {"message": "Python source code is required."},
            HTTPStatus.BAD_REQUEST,
        )

    if not isinstance(stdin_text, str):
        return make_response(
            {"message": "Program input must be text."},
            HTTPStatus.BAD_REQUEST,
        )

    if "\x00" in stdin_text:
        return make_response(
            {"message": "Program input contains an unsupported null character."},
            HTTPStatus.BAD_REQUEST,
        )

    if len(source.encode("utf-8")) > PYTHON_IDE_MAX_SOURCE_BYTES:
        return make_response(
            {"message": "The Python program is too large to run in the IDE."},
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    if len(stdin_text.encode("utf-8")) > PYTHON_IDE_MAX_STDIN_BYTES:
        return make_response(
            {"message": "The program input is too large."},
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    safe_filename = safe_upload_filename(requested_filename)
    if (
        len(requested_filename.encode("utf-8")) > 128
        or not safe_filename.lower().endswith(".py")
    ):
        return make_response(
            {
                "message": (
                    "The Python program needs a short file name ending in .py."
                )
            },
            HTTPStatus.BAD_REQUEST,
        )

    grading_script = "/tabot-files/grading-scripts/grade.py"
    if not os.path.isfile(grading_script):
        return make_response(
            {"message": "The Judge0 grading service is not configured."},
            HTTPStatus.SERVICE_UNAVAILABLE,
        )

    try:
        with tempfile.TemporaryDirectory(prefix="maat-python-ide-") as temp_dir:
            source_path = os.path.join(temp_dir, safe_filename)

            with open(source_path, "w", encoding="utf-8", newline="\n") as source_file:
                source_file.write(source)

            process = subprocess.run(
                [
                    "python",
                    grading_script,
                    "IDE",
                    "py",
                    stdin_text,
                    source_path,
                    "[]",
                ],
                cwd=temp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=PYTHON_IDE_TIMEOUT_SECONDS,
            )
    except subprocess.TimeoutExpired:
        return make_response(
            {"message": "Judge0 did not finish the program in time."},
            HTTPStatus.GATEWAY_TIMEOUT,
        )
    except OSError:
        return make_response(
            {"message": "The Judge0 grading service could not be started."},
            HTTPStatus.SERVICE_UNAVAILABLE,
        )

    if process.returncode != 0:
        return make_response(
            {
                "message": (
                    process.stderr.strip()
                    or "Judge0 could not run the Python program."
                )
            },
            HTTPStatus.BAD_GATEWAY,
        )

    try:
        result = json.loads(process.stdout or "{}")
    except json.JSONDecodeError:
        return make_response(
            {"message": "Judge0 returned an unreadable response."},
            HTTPStatus.BAD_GATEWAY,
        )

    response_payload = {}
    was_truncated = False

    for field in ("stdout", "stdout_transcript", "stderr", "compile_output"):
        response_payload[field], field_truncated = truncate_ide_output(
            result.get(field, "")
        )
        was_truncated = was_truncated or field_truncated

    response_payload["truncated"] = was_truncated
    response_payload["waiting_for_input"] = bool(
        result.get("waiting_for_input", False)
    )
    response_payload["execution_provider"] = "judge0"

    return jsonify(response_payload)


@upload_api.route("/", methods=["POST"])
@jwt_required()
@inject
def file_upload(
    user_repository: UserRepository = Provide[Container.user_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
    project_repo: ProjectRepository = Provide[Container.project_repo],
    class_repo: ClassRepository = Provide[Container.class_repo],
):
    class_id = request.form.get("class_id", "").strip()

    if not class_id:
        return make_response({"message": "Missing class_id"}, HTTPStatus.BAD_REQUEST)

    class_id_int = parse_int(class_id, 0)

    if class_id_int <= 0:
        return make_response({"message": "Invalid class_id"}, HTTPStatus.BAD_REQUEST)

    submission_method = request.form.get("submission_method", "").strip().lower()

    if not submission_method:
        submission_method = "upload" if "student_id" in request.form else "unknown"

    if submission_method not in {"upload", "editor", "unknown"}:
        return make_response(
            {"message": "Invalid submission_method"},
            HTTPStatus.BAD_REQUEST,
        )

    is_staff_upload = user_can_access_class_id(class_id_int)

    if "student_id" in request.form and not is_staff_upload:
        return make_response({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    if not is_staff_upload and not current_user_is_enrolled_in_class(class_id_int):
        return make_response({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    username = current_user.Username
    user_id = current_user.Id

    if "student_id" in request.form:
        student_id = parse_int(request.form.get("student_id"), 0)

        if student_id <= 0:
            return make_response(
                {"message": "Invalid student_id"},
                HTTPStatus.BAD_REQUEST,
            )

        user_lookup = user_repository.get_user(student_id)
        username = getattr(user_lookup, "Username", user_lookup)

        if not username:
            return make_response(
                {"message": "Student not found"},
                HTTPStatus.NOT_FOUND,
            )

        user_obj = user_repository.getUserByName(username)

        if not user_obj:
            return make_response(
                {"message": "Student not found"},
                HTTPStatus.NOT_FOUND,
            )

        user_id = user_obj.Id

        if not user_id_is_enrolled_in_class(user_id, class_id_int):
            return make_response(
                {"message": "Student is not enrolled in this class"},
                HTTPStatus.FORBIDDEN,
            )

    project = None

    if "project_id" in request.form:
        project_id = parse_int(request.form.get("project_id"), 0)

        if project_id <= 0:
            return make_response(
                {"message": "Invalid project_id"},
                HTTPStatus.BAD_REQUEST,
            )

        project = project_repo.get_selected_project(project_id)
    else:
        project = project_repo.get_current_project_by_class(class_id)

    if project is None:
        return make_response(
            {"message": "No active project"},
            HTTPStatus.NOT_ACCEPTABLE,
        )

    if int(getattr(project, "ClassId", 0) or 0) != class_id_int:
        return make_response(
            {"message": "Project does not belong to this class"},
            HTTPStatus.BAD_REQUEST,
        )

    checkpoint_id = parse_int(request.form.get("checkpoint_id", ""), 0)
    is_checkpoint = parse_bool(request.form.get("checkpoint", "")) or checkpoint_id > 0

    checkpoint: Optional[Checkpoints] = None

    if is_checkpoint:
        if checkpoint_id <= 0:
            return make_response(
                {"message": "Missing checkpoint_id"},
                HTTPStatus.BAD_REQUEST,
            )

        checkpoint = project_repo.get_checkpoint(checkpoint_id)

        if checkpoint is None:
            return make_response(
                {"message": "Checkpoint not found"},
                HTTPStatus.NOT_FOUND,
            )

        if int(getattr(checkpoint, "ProjectId", 0) or 0) != int(project.Id):
            return make_response(
                {"message": "Checkpoint does not belong to this project"},
                HTTPStatus.BAD_REQUEST,
            )

        if not bool(getattr(checkpoint, "Enabled", True)):
            return make_response(
                {"message": "Checkpoint is disabled"},
                HTTPStatus.FORBIDDEN,
            )

    order_error = upload_target_order_error(
        project_repo,
        user_id,
        int(project.Id),
        is_checkpoint,
        checkpoint_id,
    )
    if order_error is not None:
        return order_error

    if not is_staff_upload:
        cooldown_response = student_submission_cooldown_response(
            user_id,
            class_id_int,
            int(project.Id),
            is_checkpoint,
            checkpoint_id,
        )

        if cooldown_response is not None:
            return cooldown_response

    upload_files = request.files.getlist("files")

    if not upload_files:
        single = request.files.get("file")

        if single and single.filename:
            upload_files = [single]

    upload_files = [f for f in upload_files if f and f.filename]

    if not upload_files:
        return make_response({"message": "No selected file"}, HTTPStatus.BAD_REQUEST)

    if not all(allowed_file(f.filename) for f in upload_files):
        return make_response(
            {"message": "Unsupported file type"},
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )

    owner = checkpoint if checkpoint is not None else project

    effective_language = (
        getattr(owner, "Language", None)
        or getattr(project, "Language", None)
        or ""
    )

    solution_path = ""

    try:
        solution_path = project_repo.get_project_path(
            int(project.Id),
            checkpoint_id=(checkpoint_id if is_checkpoint else None),
        )
    except Exception:
        solution_path = ""

    if not solution_path:
        solution_path = str(getattr(owner, "solutionpath", "") or "")

    if not solution_path:
        return make_response(
            {
                "message": (
                    "Checkpoint has no solution files"
                    if is_checkpoint
                    else "Assignment has no solution files"
                )
            },
            HTTPStatus.BAD_REQUEST,
        )

    grader_language = normalize_grader_language(effective_language, solution_path)

    if submission_method == "editor" and grader_language != "py":
        return make_response(
            {"message": "The Python editor can only submit Python assignments"},
            HTTPStatus.BAD_REQUEST,
        )

    expected_extensions = expected_extensions_for_language(effective_language)

    if not expected_extensions:
        expected_extensions = expected_extensions_for_language(grader_language)

    if not expected_extensions:
        return make_response(
            {"message": "Unsupported language"},
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )

    submitted_extensions = [
        os.path.splitext(f.filename)[1].lower()
        for f in upload_files
    ]

    if grader_language == "java":
        invalid_java_files = [
            f.filename
            for f in upload_files
            if os.path.splitext(f.filename)[1].lower() != ".java"
        ]

        if invalid_java_files:
            return make_response(
                {
                    "message": (
                        "Selected project expects Java: upload one or more .java files."
                    )
                },
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
    else:
        if len(upload_files) != 1:
            return make_response(
                {
                    "message": (
                        "Only Java projects support multi-file student uploads."
                    )
                },
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )

        if submitted_extensions[0] not in expected_extensions:
            readable = ", ".join(expected_extensions)
            return make_response(
                {
                    "message": (
                        f"Selected project expects {readable}: upload the correct file type."
                    )
                },
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )

    # Store submission times and folder timestamps in Chicago, including DST.
    ts_now = datetime.now(CHICAGO_TIMEZONE)
    ts_stamp = ts_now.strftime("%Y%m%d_%H%M%S")
    dt_string = ts_now.strftime("%Y/%m/%d %H:%M:%S")

    project_bucket = student_project_bucket(
        class_id_int,
        project,
        checkpoint if is_checkpoint else None,
        ts_stamp,
    )

    safe_username = sanitize_fs_name(username)
    user_bucket = os.path.join(project_bucket, safe_username)
    os.makedirs(user_bucket, exist_ok=True)

    outputpath = project_bucket
    submission_dir = os.path.join(user_bucket, ts_stamp)
    os.makedirs(submission_dir, exist_ok=True)

    for upload_file in upload_files:
        safe_filename = safe_upload_filename(upload_file.filename)
        destination = os.path.join(submission_dir, safe_filename)
        upload_file.save(destination)

    try:
        testcase_info_json = project_repo.testcases_to_json(
            int(project.Id),
            checkpoint_id=(checkpoint_id if is_checkpoint else None),
        )
    except TypeError:
        testcase_info_json = project_repo.testcases_to_json(int(project.Id))

    grading_script = "/tabot-files/grading-scripts/grade.py"
    project_id_arg = str(project.Id)
    class_id_arg = str(class_id)

    add_payload = resolve_additional_files_payload(owner, solution_path)

    cmd = [
        "python",
        grading_script,
        str(username),
        grader_language,
        str(testcase_info_json),
        submission_dir,
        add_payload,
        project_id_arg,
        class_id_arg,
    ]

    result = subprocess.run(cmd, cwd=outputpath)

    if result.returncode != 0:
        return make_response(
            {"message": "Error in running grading script!"},
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    json_out = os.path.join(submission_dir, "testcases.json")

    if not os.path.exists(json_out):
        alternate_json_out = os.path.join(submission_dir, f"{username}.json")

        if os.path.exists(alternate_json_out):
            json_out = alternate_json_out

    status, testcase_results = load_grader_status(json_out)

    submission_id = submission_repo.create_submission(
        user_id=user_id,
        output=json_out,
        codepath=submission_dir,
        time=dt_string,
        project_id=project.Id,
        status=status,
        testcase_results=testcase_results,
        is_checkpoint=is_checkpoint,
        checkpoint_id=(checkpoint_id if is_checkpoint else None),
        submission_method=submission_method,
    )

    star_award = None

    if not is_staff_upload and status:
        star_award = award_completion_stars(
            user_id=user_id,
            class_id=class_id_int,
            project=project,
            is_checkpoint=is_checkpoint,
            checkpoint_id=(checkpoint_id if is_checkpoint else None),
            submission_id=submission_id,
        )

    completed_attempts = student_submission_scope_query(
        user_id,
        int(project.Id),
        is_checkpoint,
        checkpoint_id,
    ).count()
    office_hours_entry = (
        active_office_hours_entry_for_project(
            user_id,
            class_id_int,
            int(project.Id),
        )
        if not is_staff_upload
        else None
    )
    cooldown_seconds = (
        0
        if office_hours_entry is not None
        else submission_cooldown_seconds_for_attempt_count(
            completed_attempts,
            is_checkpoint,
        )
    )

    message = {
        "message": "Success",
        "remainder": 5,
        "sid": submission_id,
        "cooldown_seconds": cooldown_seconds,
        "submission_attempt_count": completed_attempts,
        "next_attempt_number": completed_attempts + 1,
        "office_hours_cooldown_exempt": office_hours_entry is not None,
        "office_hours_cooldown_exempt_until": serialize_utc_datetime(
            getattr(office_hours_entry, "CooldownExemptUntil", None)
        ),
        "star_award": star_award,
        "stars": current_star_balance(user_id, class_id_int),
    }

    return make_response(message, HTTPStatus.OK)
