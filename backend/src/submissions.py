"""Serve submission results, grading records, and student incentive actions.

Resolve the requested submission and enforce class, ownership, and staff access.
Parse grading output, protect hidden test inputs, and support input purchases,
completion rewards, and cooldown skips. Include submission history and audit
endpoints, plus helpers shared with uploads and office-hours scheduling.

Endpoints use /api/submissions/<handler_name>."""

import re
from flask import request
from flask_jwt_extended import current_user
from src.core.constants import STUDENT_ROLE
from src.core.constants import chicago_iso
from src.core.constants import chicago_now
from src.core.constants import to_chicago_datetime
from src.core.models import Projects
from src.core.models import ClassAssignments
from src.core.models import Submissions
from src.repositories.assignment_repository import AssignmentRepository
from src.repositories.submission_repository import SubmissionRepository
from datetime import datetime
from datetime import timedelta
from src.core.models import StudentCooldownSkips
from sqlalchemy import func
from src.core.database import db
from src.core.models import Modules
from src.core.models import StudentCheckpointSkips
from src.core.models import StudentStarAwards
from src.core.models import StudentTestcaseInputPurchases
from http import HTTPStatus
from src.core.models import Testcases
from tap.parser import Parser
import json
import os
from dependency_injector.wiring import inject
from dependency_injector.wiring import Provide
from src.core.container import Container
from flask import jsonify
from flask import make_response
from flask import send_file
from flask_jwt_extended import jwt_required
from io import BytesIO
from src.core.blueprints import submissions_api
from src.assignment_permissions import is_test_user_request, is_test_submission
from src.assignment_permissions import current_user_global_role
from src.assignment_permissions import current_user_id
from src.assignment_permissions import current_user_is_enrolled_in_class
from src.assignment_permissions import current_user_assignment_role_for_class
from src.assignment_permissions import user_can_access_class_id
from src.assignment_permissions import user_can_access_project_id
from src.core.models import Users
import zipfile
from src.repositories.user_repository import UserRepository
from io import StringIO
from src.ai_suggestions import GRADING_DEFAULT_DEFS_MAP
import csv


# Submission permissions, request parsing, timing helpers, and policy constants.


# Policy constants for submissions.


# Convert persisted Chicago wall time before comparing submission timestamps.


ui_clicks_log = "/tabot-files/project-files/code_view_clicks.log"


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


INPUT_EVENT_PATTERN = re.compile(r"\[\[\[MAAT_INPUT_B64:[A-Za-z0-9_-]*\]\]\]")


HIDDEN_INPUT_EVENT = "[[[MAAT_INPUT_HIDDEN]]]"


# Helpers for submissions parsing.


def parse_int(v, default: int = -1) -> int:
    """Parse an integer request value, returning the supplied fallback on failure.

    Inputs: v, default."""
    try:
        return int(str(v).strip())
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return default


def parse_bool(v) -> bool:
    """Interpret a request boolean using this feature’s accepted spellings and fallback.

    Inputs: v."""
    # Handle the case where isinstance(v, bool).
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def submission_cooldown_skip_cost(checkpoint: bool) -> int:
    """Handle submission cooldown skip cost for this component.

    Inputs: checkpoint."""
    return (
        CHECKPOINT_SUBMISSION_COOLDOWN_SKIP_COST_STARS
        if checkpoint
        else MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS
    )


def testcase_input_purchase_cost(checkpoint: bool) -> int:
    """Handle testcase input purchase cost for this component.

    Inputs: checkpoint."""
    return (
        CHECKPOINT_TESTCASE_INPUT_COST_STARS
        if checkpoint
        else MAIN_PROJECT_TESTCASE_INPUT_COST_STARS
    )


# Submissions authorization and current-user scope checks.


def user_is_student_in_class_id(user_id: int, class_id: int) -> bool:
    """Return whether the user is enrolled as a student in the class."""
    user_id = parse_int(user_id, 0)
    class_id = parse_int(class_id, 0)
    # Return an empty or negative result when this guard matches.
    if user_id <= 0 or class_id <= 0:
        return False

    role = (
        db.session.query(ClassAssignments.Role)
        .filter(
            ClassAssignments.UserId == user_id,
            ClassAssignments.ClassId == class_id,
        )
        .scalar()
    )
    return parse_int(role, -1) == STUDENT_ROLE


def current_user_can_view_class_id(class_id: int) -> bool:
    """Return whether the current user can staff-access or is enrolled in a class."""
    class_id = parse_int(class_id, 0)
    # Return an empty or negative result when this guard matches.
    if class_id <= 0:
        return False
    return user_can_access_class_id(class_id) or current_user_is_enrolled_in_class(class_id)


def user_can_access_submission(submission) -> bool:
    """Determine whether user can access submission.

    Inputs: submission."""
    # Return an empty or negative result when this guard matches.
    if submission is None:
        return False

    return user_can_access_project_id(int(getattr(submission, "Project", 0) or 0))


def current_user_can_view_submission(
    submission, submission_repo: SubmissionRepository | None = None
) -> bool:
    """Determine whether current user can view submission.

    Inputs: submission, submission_repo."""
    # Return an empty or negative result when this guard matches.
    if submission is None:
        return False

    if is_test_submission(submission):
        project = Projects.query.filter(Projects.Id == submission.Project).first()
        return project is not None and user_can_access_class_id(int(project.ClassId))

    if user_can_access_submission(submission):
        return True

    # Handle the case where int(getattr(submission, 'User', -1) or -1) == int(current_user.Id).
    if int(getattr(submission, "User", -1) or -1) == int(current_user.Id):
        return True

    if submission_repo is not None:
        try:
            return submission_repo.submission_view_verification(
                int(current_user.Id),
                int(getattr(submission, "Id", -1) or -1),
            )
        # Convert this failure into the fallback result or error response below.
        except Exception:
            return False

    return False


def opt_int(raw) -> int | None:
    """Return an optional numeric request identifier.

    Inputs: raw."""
    s = str(raw or "").strip()
    return int(s) if s.isdigit() else None


def checkpoint_params_from_args() -> tuple[bool, int | None]:
    """Handle checkpoint params from args for this component."""
    want_checkpoint = parse_bool(request.args.get("checkpoint", ""))
    ppid = opt_int(request.args.get("checkpoint_id", ""))
    return want_checkpoint, ppid


def latest_checkpoint_submission(project_id: int, user_id: int, checkpoint_id: int | None):
    """Handle latest checkpoint submission for this component.

    Inputs: project_id, user_id, checkpoint_id."""
    try:
        # Execute the database lookup with the filters specified below.
        q = Submissions.query.filter(
            Submissions.Project == int(project_id),
            Submissions.User == int(user_id),
            Submissions.IsCheckpoint == True,
        )
        if checkpoint_id is not None and hasattr(Submissions, "CheckpointId"):
            q = q.filter(Submissions.CheckpointId == int(checkpoint_id))
        return q.order_by(Submissions.Time.desc(), Submissions.Id.desc()).first()
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return None


def resolve_submission_for_current_user(
    submission_repo: SubmissionRepository,
    project_repo: AssignmentRepository,
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
                sub = submission_repo.get_submission_by_user_and_projectid(
                    int(current_user.Id), int(project_id)
                )
        # Handle the case where sub is None.
        if sub is None:
            return None, int(project_id), None
        project_id = int(getattr(sub, "Project", -1) or -1)
    else:
        proj = project_repo.get_current_project_by_class(int(class_id))
        # Handle the case where proj is None.
        if proj is None:
            return None, -1, None
        project_id = int(getattr(proj, "Id", -1) or -1)
        sub = submission_repo.get_submission_by_user_and_projectid(
            int(current_user.Id), int(project_id)
        )
        # Handle the case where sub is None.
        if sub is None:
            return None, int(project_id), None

    try:
        if (
            bool(getattr(sub, "IsCheckpoint", False))
            and getattr(sub, "CheckpointId", None) is not None
        ):
            checkpoint_id = int(getattr(sub, "CheckpointId"))
    except Exception:
        checkpoint_id = None

    return sub, int(project_id), checkpoint_id


# Helpers for submissions time.


def current_chicago_datetime() -> datetime:
    """Return the current America/Chicago wall time for database comparisons."""
    return chicago_now()


def parse_cooldown_lifted_at(value) -> datetime | None:
    """Parse cooldown lifted at.

    Inputs: value."""
    # Return an empty or negative result when this guard matches.
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            raw = str(value).strip()
            # Return an empty or negative result when this guard matches.
            if not raw:
                return None
            if raw.endswith("Z"):
                raw = f"{raw[:-1]}+00:00"
            parsed = datetime.fromisoformat(raw)
        # Convert this failure into the fallback result or error response below.
        except Exception:
            return None

    # Handle the case where parsed.tzinfo is not None.
    if parsed.tzinfo is not None:
        return to_chicago_datetime(parsed)

    return parsed


def serialize_cooldown_lifted_at(value: datetime | None) -> str | None:
    """Serialize cooldown lifted at.

    Inputs: value."""
    # Return an empty or negative result when this guard matches.
    if value is None:
        return None

    return chicago_iso(value)


def seconds_until(value: datetime | None) -> int:
    """Handle seconds until for this component.

    Inputs: value."""
    # Handle the case where value is None.
    if value is None:
        return 0

    return max(0, int((value - current_chicago_datetime()).total_seconds() + 0.999))

# Submission rewards, cooldown calculation, and purchased cooldown skips.


# Compute submission cooldowns and pending star-funded skip eligibility.


def parse_submission_datetime_for_cooldown(value) -> datetime | None:
    """Return Chicago wall time, matching the existing naive submission column."""
    # Handle the case where isinstance(value, datetime).
    if isinstance(value, datetime):
        return to_chicago_datetime(value)

    raw = str(value or "").strip()
    # Return an empty or negative result when this guard matches.
    if not raw:
        return None

    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(raw)
        return to_chicago_datetime(parsed)
    # Ignore this failure and allow the surrounding operation to continue.
    except ValueError:
        pass

    # Process each fmt from ('%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M:%S', '%m/%d/%y %H:%M:%S').
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%y %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt)
        # Ignore this failure and allow the surrounding operation to continue.
        except ValueError:
            pass

    return None


def submission_cooldown_seconds_for_attempt_count(
    completed_attempts: int,
    is_checkpoint: bool,
) -> int:
    """Handle submission cooldown seconds for attempt count for this component.

    Inputs: completed_attempts, is_checkpoint."""
    completed_attempts = max(0, int(completed_attempts or 0))

    # Handle the case where completed_attempts <= 0.
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
    """Handle submission scope query for this component.

    Inputs: user_id, project_id, checkpoint, checkpoint_id."""
    # Execute the database lookup with the filters specified below.
    query = Submissions.query.filter(
        Submissions.User == int(user_id),
        Submissions.Project == int(project_id),
        Submissions.IsCheckpoint == bool(checkpoint),
    )

    # Handle the case where checkpoint.
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
    """Handle pending cooldown skip exists for this component.

    Inputs: user_id, class_id, project_id, checkpoint_id, submitted_at."""
    # Execute the database lookup with the filters specified below.
    query = StudentCooldownSkips.query.filter(
        StudentCooldownSkips.UserId == int(user_id),
        StudentCooldownSkips.ClassId == int(class_id),
        StudentCooldownSkips.ProjectId == int(project_id),
        StudentCooldownSkips.CheckpointId == int(checkpoint_id or 0),
        StudentCooldownSkips.UsedAt.is_(None),
    )

    if submitted_at is not None:
        query = query.filter(StudentCooldownSkips.CreatedAt >= submitted_at)

    return query.first() is not None


def submission_cooldown_state(
    user_id: int,
    class_id: int,
    project_id: int,
    checkpoint: bool,
    checkpoint_id: int,
) -> dict:
    """Handle submission cooldown state for this component.

    Inputs: user_id, class_id, project_id, checkpoint, checkpoint_id."""
    # Import at call time because the two features share helper functions.
    from src.office_hours import active_office_hours_entry_for_project

    # Handle the case where project_id <= 0.
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
    office_hours_entry = None if is_test_user_request() else active_office_hours_entry_for_project(
        user_id,
        class_id,
        project_id,
    )

    # Handle the case where office_hours_entry is not None.
    if office_hours_entry is not None:
        return {
            "submission_attempt_count": completed_attempts,
            "next_attempt_number": next_attempt,
            "submission_cooldown_seconds": 0,
            "cooldown_remaining_seconds": 0,
            "cooldown_lifted_at": None,
            "office_hours_cooldown_exempt": True,
            "office_hours_cooldown_exempt_until": serialize_cooldown_lifted_at(
                parse_cooldown_lifted_at(getattr(office_hours_entry, "CooldownExemptUntil", None))
            ),
        }

    cooldown_seconds = submission_cooldown_seconds_for_attempt_count(
        completed_attempts,
        checkpoint,
    )
    # Execute the database lookup with the filters specified below.
    latest = scope_query.order_by(Submissions.Time.desc(), Submissions.Id.desc()).first()
    submitted_at = parse_submission_datetime_for_cooldown(
        getattr(latest, "Time", None) if latest is not None else None
    )

    remaining_seconds = 0
    cooldown_lifted_at = None

    if submitted_at is not None and cooldown_seconds > 0:
        elapsed_seconds = (
            current_chicago_datetime() - submitted_at
        ).total_seconds()
        remaining_seconds = max(
            0,
            int(cooldown_seconds - elapsed_seconds + 0.999),
        )
        if remaining_seconds > 0:
            cooldown_lifted_at = current_chicago_datetime() + timedelta(seconds=remaining_seconds)

        if remaining_seconds > 0 and not is_test_user_request() and pending_cooldown_skip_exists(
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


# Calculate scoped completion rewards and the student incentive response.


def ensure_incentive_tables():
    """Create missing incentive tables required by the current request."""
    # Process each model from (StudentStarAwards, StudentCheckpointSkips, StudentCooldownSkips, StudentTestcaseInputPurchases).
    for model in (
        StudentStarAwards,
        StudentCheckpointSkips,
        StudentCooldownSkips,
        StudentTestcaseInputPurchases,
    ):
        try:
            model.__table__.create(db.engine, checkfirst=True)
        # Ignore this failure and allow the surrounding operation to continue.
        except Exception:
            pass


def get_star_balance(user_id: int, class_id: int) -> int:
    """Return awards minus every star purchase made in the class."""
    ensure_incentive_tables()
    user_id = int(user_id)
    class_id = int(class_id)

    awarded = (
        db.session.query(func.coalesce(func.sum(StudentStarAwards.AwardedStars), 0))
        .filter(
            StudentStarAwards.UserId == user_id,
            StudentStarAwards.ClassId == class_id,
        )
        .scalar()
    )

    checkpoint_spent = (
        db.session.query(func.coalesce(func.sum(StudentCheckpointSkips.SpentStars), 0))
        .filter(
            StudentCheckpointSkips.UserId == user_id,
            StudentCheckpointSkips.ClassId == class_id,
        )
        .scalar()
    )

    cooldown_spent = (
        db.session.query(func.coalesce(func.sum(StudentCooldownSkips.SpentStars), 0))
        .filter(
            StudentCooldownSkips.UserId == user_id,
            StudentCooldownSkips.ClassId == class_id,
        )
        .scalar()
    )

    testcase_input_spent = (
        db.session.query(func.coalesce(func.sum(StudentTestcaseInputPurchases.SpentStars), 0))
        .filter(
            StudentTestcaseInputPurchases.UserId == user_id,
            StudentTestcaseInputPurchases.ClassId == class_id,
        )
        .scalar()
    )

    return max(
        0,
        parse_int(awarded, 0)
        - parse_int(checkpoint_spent, 0)
        - parse_int(cooldown_spent, 0)
        - parse_int(testcase_input_spent, 0),
    )


def project_window(project) -> tuple[datetime | None, datetime | None]:
    """Handle project window for this component.

    Inputs: project."""
    module = getattr(project, "Module", None)

    if module is None:
        module_id = parse_int(getattr(project, "ModuleId", 0), 0)
        if module_id > 0:
            try:
                # Execute the database lookup with the filters specified below.
                module = Modules.query.filter(Modules.Id == module_id).first()
            except Exception:
                module = None

    # Handle the case where module is None.
    if module is None:
        return None, None

    start = parse_cooldown_lifted_at(getattr(module, "Start", None))
    end = parse_cooldown_lifted_at(getattr(module, "End", None))

    return start, end


def early_start_deadline_for_project(project) -> datetime | None:
    """Handle early start deadline for project for this component.

    Inputs: project."""
    start, end = project_window(project)

    # Return an empty or negative result when this guard matches.
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
    """Return whether the first submission occurred by the early-start deadline."""
    # Return an empty or negative result when this guard matches.
    if project is None or early_deadline is None:
        return False

    # Execute the database lookup with the filters specified below.
    query = Submissions.query.filter(
        Submissions.User == int(user_id),
        Submissions.Project == int(project.Id),
        Submissions.IsCheckpoint == bool(checkpoint),
    )

    if checkpoint:
        query = query.filter(Submissions.CheckpointId == int(checkpoint_id or 0))
    else:
        query = query.filter(Submissions.CheckpointId.is_(None))

    # Execute the database lookup with the filters specified below.
    first_submission = query.order_by(Submissions.Time.asc(), Submissions.Id.asc()).first()
    # Return an empty or negative result when this guard matches.
    if first_submission is None:
        return False

    first_started_at = parse_submission_datetime_for_cooldown(
        getattr(first_submission, "Time", None)
    )
    return first_started_at is not None and first_started_at <= early_deadline


def existing_star_award_for_scope(
    user_id: int,
    class_id: int,
    project_id: int,
    checkpoint: bool,
    checkpoint_id: int,
):
    """Handle existing star award for scope for this component.

    Inputs: user_id, class_id, project_id, checkpoint, checkpoint_id."""
    # Return an empty or negative result when this guard matches.
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
    """Handle scoped reward payload for this component.

    Inputs: user_id, class_id, project_id, checkpoint, checkpoint_id."""
    base_stars = CHECKPOINT_COMPLETION_STARS if checkpoint else MAIN_PROJECT_COMPLETION_STARS
    project = (
        Projects.query.filter(Projects.Id == int(project_id)).first() if project_id > 0 else None
    )
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
        awarded_stars = max(0, parse_int(getattr(existing_award, "AwardedStars", 0), 0))
        awarded_base = max(
            0, parse_int(getattr(existing_award, "BaseAwardStars", base_stars), base_stars)
        )
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
    """Handle incentive payload for this component.

    Inputs: user_id, class_id, project_id, checkpoint, checkpoint_id."""
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

# Parse TAP results and enforce hidden-test/input-purchase visibility.


def apply_hidden_flags_to_results(
    output_json: str, project_id: int, checkpoint_id: int | None
) -> str:
    """Apply hidden flags to results.

    Inputs: output_json, project_id, checkpoint_id."""
    try:
        obj = json.loads(output_json) if isinstance(output_json, str) else (output_json or {})
        results = obj.get("results", None) if isinstance(obj, dict) else None
        # Handle the case where not isinstance(results, list) or int(project_id) <= 0.
        if not isinstance(results, list) or int(project_id) <= 0:
            return output_json

        # Execute the database lookup with the filters specified below.
        q = Testcases.query.filter(Testcases.ProjectId == int(project_id))
        if checkpoint_id is not None:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))
        # Execute the database lookup with the filters specified below.
        tcs = q.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all()
        testcase_meta_by_name = {}
        # Process each (index, tc) from enumerate(tcs or [], start=1).
        for index, tc in enumerate(tcs or [], start=1):
            key = str(getattr(tc, "Name", "") or "").strip().lower()
            testcase_meta_by_name.setdefault(key, []).append(
                {
                    "hidden": bool(getattr(tc, "Hidden", False)),
                    "order": index,
                }
            )

        name_occurrences = {}
        # Process each (original_index, r) from enumerate(results).
        for original_index, r in enumerate(results):
            if not isinstance(r, dict):
                continue
            r.pop("description", None)
            name = None
            if isinstance(r.get("name"), str):
                name = r.get("name")
            elif isinstance(r.get("test"), dict) and isinstance(r["test"].get("name"), str):
                name = r["test"]["name"]

            key = str(name or "").strip().lower()
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
                (
                    parse_int(result.get("order"), len(tcs) + len(results) + 1)
                    if isinstance(result, dict)
                    else len(tcs) + len(results) + 1
                ),
                parse_int(result.get("_original_index"), 0) if isinstance(result, dict) else 0,
            )
        )
        # Process each result from results.
        for result in results:
            if isinstance(result, dict):
                result.pop("_original_index", None)

        return json.dumps(obj, sort_keys=True, indent=4)
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return output_json


def resolve_testcase_input_purchase_scope(
    submission_repo: SubmissionRepository,
    submission_id: int,
    class_id: int,
):
    """Resolve testcase input purchase scope.

    Inputs: submission_repo, submission_id, class_id."""
    # Handle the case where submission_id <= 0 or class_id <= 0.
    if submission_id <= 0 or class_id <= 0:
        return None, "submission_id and class_id are required.", HTTPStatus.BAD_REQUEST

    user_id = int(current_user.Id)
    # Handle the case where not user_is_student_in_class_id(user_id, class_id).
    if not user_is_student_in_class_id(user_id, class_id):
        return None, "Not Authorized", HTTPStatus.UNAUTHORIZED

    submission = submission_repo.get_submission_by_submission_id(submission_id)
    # Handle the case where submission is None.
    if submission is None:
        return None, "Submission not found.", HTTPStatus.NOT_FOUND

    # Handle the case where int(getattr(submission, 'User', 0) or 0) != user_id.
    if int(getattr(submission, "User", 0) or 0) != user_id:
        return None, "Not Authorized", HTTPStatus.UNAUTHORIZED

    project_id = int(getattr(submission, "Project", 0) or 0)
    # Execute the database lookup with the filters specified below.
    project = Projects.query.filter(Projects.Id == project_id).first()
    # Handle the case where project is None.
    if project is None:
        return None, "Project not found.", HTTPStatus.NOT_FOUND

    # Handle the case where int(getattr(project, 'ClassId', 0) or 0) != class_id.
    if int(getattr(project, "ClassId", 0) or 0) != class_id:
        return None, "Not Authorized", HTTPStatus.UNAUTHORIZED

    is_checkpoint = bool(getattr(submission, "IsCheckpoint", False))
    checkpoint_id = int(getattr(submission, "CheckpointId", 0) or 0) if is_checkpoint else 0
    # Handle the case where is_checkpoint and checkpoint_id <= 0.
    if is_checkpoint and checkpoint_id <= 0:
        return None, "Checkpoint not found.", HTTPStatus.NOT_FOUND

    return (
        {
            "user_id": user_id,
            "class_id": class_id,
            "project_id": project_id,
            "checkpoint_id": checkpoint_id,
            "is_checkpoint": is_checkpoint,
            "submission_id": int(getattr(submission, "Id", submission_id) or submission_id),
            "submission_output_path": str(getattr(submission, "OutputFilepath", "") or ""),
        },
        None,
        HTTPStatus.OK,
    )


def testcase_input_rows_for_scope(project_id: int, checkpoint_id: int):
    """Handle testcase input rows for scope for this component.

    Inputs: project_id, checkpoint_id."""
    # Execute the database lookup with the filters specified below.
    query = Testcases.query.filter(Testcases.ProjectId == int(project_id))
    if checkpoint_id > 0:
        query = query.filter(Testcases.CheckpointId == int(checkpoint_id))
    else:
        query = query.filter(Testcases.CheckpointId.is_(None))

    return query.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all()


def redact_input_events(value):
    """Handle redact input events for this component.

    Inputs: value."""
    # Handle the case where isinstance(value, str).
    if isinstance(value, str):
        return INPUT_EVENT_PATTERN.sub(HIDDEN_INPUT_EVENT, value)
    # Handle the case where isinstance(value, list).
    if isinstance(value, list):
        return [redact_input_events(item) for item in value]
    # Handle the case where isinstance(value, dict).
    if isinstance(value, dict):
        return {key: redact_input_events(item) for key, item in value.items()}
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

        # Process each (index, result) from enumerate(results, start=1).
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
    # Convert this failure into the fallback result or error response below.
    except Exception:
        # Fail closed: a malformed or unexpected payload must not expose input.
        return INPUT_EVENT_PATTERN.sub(HIDDEN_INPUT_EVENT, str(output_json or ""))


def testcase_result_status(result: dict) -> str:
    """Handle testcase result status for this component.

    Inputs: result."""
    # Handle the case where parse_bool(result.get('skipped', False)).
    if parse_bool(result.get("skipped", False)):
        return "skipped"

    raw_passed = result.get("passed")
    # Handle the case where isinstance(raw_passed, bool).
    if isinstance(raw_passed, bool):
        return "passed" if raw_passed else "failed"
    # Handle the case where isinstance(raw_passed, (int, float)) and raw_passed in (0, 1).
    if isinstance(raw_passed, (int, float)) and raw_passed in (0, 1):
        return "passed" if raw_passed == 1 else "failed"
    if isinstance(raw_passed, str):
        normalized = raw_passed.strip().lower()
        # Handle the case where normalized in ('1', 'true', 'yes', 'y', 'passed', 'pass').
        if normalized in ("1", "true", "yes", "y", "passed", "pass"):
            return "passed"
        # Handle the case where normalized in ('0', 'false', 'no', 'n', 'failed', 'fail').
        if normalized in ("0", "false", "no", "n", "failed", "fail"):
            return "failed"

    return "unavailable"


def testcase_result_statuses_for_scope(scope: dict, rows: list) -> dict:
    """Handle testcase result statuses for scope for this component.

    Inputs: scope, rows."""
    statuses = {int(testcase.Id): "unavailable" for testcase in rows}

    try:
        output = convert_tap_to_json(
            scope["submission_output_path"],
            current_user_global_role(),
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

        # Process each result from results.
        for result in results:
            if not isinstance(result, dict):
                continue
            order = parse_int(result.get("order"), 0)
            if order > 0:
                statuses_by_order[order] = testcase_result_status(result)

        # Process each (index, testcase) from enumerate(rows, start=1).
        for index, testcase in enumerate(rows, start=1):
            statuses[int(testcase.Id)] = statuses_by_order.get(
                index,
                "unavailable",
            )
    # Ignore this failure and allow the surrounding operation to continue.
    except Exception:
        pass

    return statuses


def serialize_testcase_input_store(scope: dict) -> dict:
    """Serialize testcase input store.

    Inputs: scope."""
    ensure_incentive_tables()
    rows = testcase_input_rows_for_scope(
        scope["project_id"],
        scope["checkpoint_id"],
    )
    result_statuses = testcase_result_statuses_for_scope(scope, rows)
    # Execute the database lookup with the filters specified below.
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
                "purchase_eligible": (result_statuses.get(int(testcase.Id)) == "failed"),
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
    """Handle convert tap to json for this component.

    Inputs: file_path, role, current_level, hasLVLSYSEnabled."""
    try:
        s = str(file_path or "").strip()
        # Handle the case where not s.
        if not s:
            return json.dumps({"results": []}, sort_keys=True, indent=4)

        # Raw JSON string fallback
        if s.startswith("{") or s.startswith("[") and "\n" in s:
            try:
                obj = json.loads(s) or {}
                return json.dumps(obj, sort_keys=True, indent=4)
            # Ignore this failure and allow the surrounding operation to continue.
            except Exception:
                pass

        # If it's a real file, try parsing its contents as JSON first (regardless of extension).
        if os.path.exists(s) and os.path.isfile(s):
            try:
                # Open the file for reading and close it automatically when this block finishes.
                with open(s, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read() or ""
                raw_strip = raw.strip()
                if raw_strip.startswith("{") or raw_strip.startswith("["):
                    obj = json.loads(raw_strip) or {}
                    return json.dumps(obj, sort_keys=True, indent=4)
            # Ignore this failure and allow the surrounding operation to continue.
            except Exception:
                pass
    # Ignore this failure and allow the surrounding operation to continue.
    except Exception:
        pass

    parser = Parser()
    test = []
    final = {}

    def sanitize_yaml_block(yaml_block: dict) -> dict:
        """Handle sanitize yaml block for this component.

        Inputs: yaml_block."""
        new_yaml = (yaml_block or {}).copy()
        new_yaml.pop("description", None)
        return new_yaml

    def parse_suite(yaml_block: dict) -> int:
        """Parse suite.

        Inputs: yaml_block."""
        try:
            return int((yaml_block or {}).get("suite", 0))
        # Convert this failure into the fallback result or error response below.
        except (TypeError, ValueError):
            return 0

    # Process each line from parser.parse_file(file_path).
    for line in parser.parse_file(file_path):
        if line.category != "test":
            continue
        if line.yaml_block is None:
            continue

        yaml_clean = sanitize_yaml_block(line.yaml_block)

        # Levels disabled: return tests as-is
        if not hasLVLSYSEnabled:
            test.append({"skipped": line.skip, "passed": line.ok, "test": yaml_clean})
            continue

        suite_req = parse_suite(yaml_clean)

        if current_level >= suite_req:
            test.append({"skipped": line.skip, "passed": line.ok, "test": yaml_clean})
        else:
            locked_yaml = {"name": yaml_clean.get("name", ""), "suite": suite_req, "locked": True}
            test.append({"skipped": "", "passed": "", "test": locked_yaml})

    final["results"] = test
    return json.dumps(final, sort_keys=True, indent=4)


# Submission results, history, grading, audit events, and incentive endpoints.


# Submissions HTTP endpoints for results.


@submissions_api.route('/get_testcase_errors', methods=["GET"])
@jwt_required()
@inject
def get_testcase_errors(
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """Return testcase errors.

    HTTP: GET /api/submissions/get_testcase_errors.

    Inputs: submission_repo, project_repo."""
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
    # Return the response below when this validation or access check matches.
    if submission is None:
        return make_response(json.dumps({"results": []}), HTTPStatus.OK)

    # Return the response below when this validation or access check matches.
    if not current_user_can_view_submission(submission, submission_repo):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    output = convert_tap_to_json(submission.OutputFilepath, current_user_global_role(), 0, False)
    output = apply_hidden_flags_to_results(output, int(projectid), checkpoint_id)

    # Execute the database lookup with the filters specified below.
    project = Projects.query.filter(Projects.Id == int(projectid)).first()
    submission_class_id = int(getattr(project, "ClassId", 0) or 0)

    if current_user_assignment_role_for_class(submission_class_id) == STUDENT_ROLE:
        output = protect_unrevealed_testcase_inputs(
            output,
            current_user_id(),
            submission_class_id,
            int(projectid),
            int(checkpoint_id or 0),
        )

    return make_response(output, HTTPStatus.OK)


@submissions_api.route('/testcase_inputs', methods=["GET", "POST"])
@jwt_required()
@inject
def testcase_inputs(
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    """Handle testcase inputs for this component.

    HTTP: GET, POST /api/submissions/testcase_inputs.

    Inputs: submission_repo.
    Database changes are committed at the explicit transaction boundaries below."""
    source = request.args if request.method == "GET" else (request.get_json(silent=True) or {})
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
    # Return the response below when this validation or access check matches.
    if scope is None:
        return make_response({"message": error_message}, error_status)

    if request.method == "GET":
        response = jsonify(serialize_testcase_input_store(scope))
        response.headers["Cache-Control"] = "no-store"
        return response

    testcase_id = parse_int(source.get("testcase_id", 0), 0)
    # Return the response below when this validation or access check matches.
    if testcase_id <= 0:
        return make_response({"message": "testcase_id is required."}, HTTPStatus.BAD_REQUEST)

    # Execute the database lookup with the filters specified below.
    testcase = Testcases.query.filter(
        Testcases.Id == testcase_id,
        Testcases.ProjectId == scope["project_id"],
    ).first()
    # Return the response below when this validation or access check matches.
    if testcase is None:
        return make_response({"message": "Testcase not found."}, HTTPStatus.NOT_FOUND)

    testcase_checkpoint_id = int(getattr(testcase, "CheckpointId", 0) or 0)
    # Return the response below when this validation or access check matches.
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
    # Execute the database lookup with the filters specified below.
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
        return make_response(
            {
                "message": message,
                "result_status": result_status,
                "star_balance": get_star_balance(
                    scope["user_id"],
                    scope["class_id"],
                ),
            },
            HTTPStatus.BAD_REQUEST,
        )

    # Lock the student row so simultaneous purchases cannot overspend the balance.
    locked_user = Users.query.filter(Users.Id == scope["user_id"]).with_for_update().first()
    if locked_user is None:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        return make_response({"message": "User not found."}, HTTPStatus.NOT_FOUND)

    # Execute the database lookup with the filters specified below.
    existing_purchase = StudentTestcaseInputPurchases.query.filter(*purchase_filter).first()
    if existing_purchase is not None:
        payload = serialize_testcase_input_store(scope)
        payload["already_purchased"] = True
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response

    cost = testcase_input_purchase_cost(scope["is_checkpoint"])
    balance = get_star_balance(scope["user_id"], scope["class_id"])
    star_label = "star" if cost == 1 else "stars"
    if balance < cost:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        return make_response(
            {
                "message": f"You need {cost} {star_label} to reveal this testcase input.",
                "star_balance": balance,
                "required_stars": cost,
            },
            HTTPStatus.BAD_REQUEST,
        )

    # Stage the new records in the current database transaction.
    db.session.add(
        StudentTestcaseInputPurchases(
            UserId=scope["user_id"],
            ClassId=scope["class_id"],
            ProjectId=scope["project_id"],
            CheckpointId=scope["checkpoint_id"],
            TestcaseId=testcase_id,
            SpentStars=cost,
            CreatedAt=current_chicago_datetime(),
        )
    )
    # Commit the pending database changes so they persist beyond this request.
    db.session.commit()

    payload = serialize_testcase_input_store(scope)
    payload["purchased_testcase_id"] = testcase_id
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@submissions_api.route('/codefinder', methods=["GET"])
@jwt_required()
@inject
def codefinder(
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """Handle codefinder for this component.

    HTTP: GET /api/submissions/codefinder.

    Inputs: submission_repo, project_repo."""
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
        # Return the response below when this validation or access check matches.
        if not current_user_can_view_class_id(class_id):
            return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

        current_project = project_repo.get_current_project_by_class(class_id)
        # Return the response below when this validation or access check matches.
        if current_project is None:
            return make_response("Not Found", HTTPStatus.NOT_FOUND)

        projectid = current_project.Id
        latest_submission = submission_repo.get_submission_by_user_and_projectid(
            current_user.Id, projectid
        )
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
            # Open the file for reading and close it automatically when this block finishes.
            with open(code_output, "r", encoding="utf-8", errors="replace") as f:
                files_payload.append({"name": os.path.basename(code_output), "content": f.read()})
        else:
            allowed_exts = {".py", ".java", ".c", ".h", ".rkt"}
            names = sorted(os.listdir(code_output), key=lambda n: (n != "Main.java", n.lower()))
            # Process each name from names.
            for name in names:
                full = os.path.join(code_output, name)
                if not os.path.isfile(full):
                    continue
                _, ext = os.path.splitext(name)
                if ext.lower() not in allowed_exts:
                    continue
                # Open the file for reading and close it automatically when this block finishes.
                with open(full, "r", encoding="utf-8", errors="replace") as f:
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
    # Open the ZIP archive for the operations below and close it on exit.
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # add files with stable ordering
        names = sorted(os.listdir(code_output), key=lambda n: (n != "Main.java", n.lower()))
        # Process each name from names.
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


# Submissions HTTP endpoints for history.


@submissions_api.route('/recentsubproject', methods=["POST"])
@jwt_required()
@inject
def recentsubproject(
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
    user_repo: UserRepository = Provide[Container.user_repo],
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """Handle recentsubproject for this component.

    HTTP: POST /api/submissions/recentsubproject.

    Inputs: submission_repo, user_repo, project_repo."""
    input_json = request.get_json() or {}
    projectid = input_json["project_id"]

    # Return the response below when this validation or access check matches.
    if not user_can_access_project_id(int(projectid)):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    checkpoint_raw = input_json.get("checkpoint", False)
    checkpoint = str(checkpoint_raw).strip().lower() in ("1", "true", "yes", "y", "on")

    ppid_raw = input_json.get("checkpoint_id", None)
    try:
        checkpoint_id = int(ppid_raw) if ppid_raw is not None else None
    except (TypeError, ValueError):
        checkpoint_id = None

    project = project_repo.get_selected_project(projectid)
    # Return the response below when this validation or access check matches.
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

        # Execute the database lookup with the filters specified below.
        q = Submissions.query.filter(
            Submissions.Project == projectid,
            Submissions.User.in_(userids),
            Submissions.IsCheckpoint == True,
        )

        if checkpoint_id is not None and hasattr(Submissions, "CheckpointId"):
            q = q.filter(Submissions.CheckpointId == checkpoint_id)

        # Execute the database lookup with the filters specified below.
        subs = q.order_by(Submissions.User.asc(), Submissions.Time.desc(), Submissions.Id.desc()).all()

        # Process each s from subs.
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

    # Process each user from users.
    for user in users:
        if user.Id in bucket:
            if checkpoint:
                manual_grade = submission_repo.get_manual_grade_for_submission(bucket[user.Id].Id)
                student_grade = (
                    manual_grade.get("grade")
                    if manual_grade and manual_grade.get("grade") is not None
                    else 0
                )
            else:
                student_grade = project_repo.get_student_grade(projectid, user.Id)

            student_id = str(getattr(user, "StudentNumber", "") or "")
            studentattempts[user.Id] = [
                user.Lastname,
                user.Firstname,
                user_lectures_dict.get(user.Id, ""),
                user_labs_dict.get(user.Id, ""),
                submission_counter_dict.get(user.Id, 0),
                chicago_iso(bucket[user.Id].Time),
                bucket[user.Id].IsPassing,
                bucket[user.Id].Id,
                str(class_id),
                student_grade,
                student_id,
                user.IsLocked,
            ]
        else:
            student_id = str(getattr(user, "StudentNumber", "") or "")
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

    # The virtual test identity has no Users/enrollment row. Include it only
    # for this authorized roster view, scoped to the authenticated admin's work.
    if parse_bool(input_json.get("include_test_user", False)):
        from src.assignment_permissions import TEST_USER_ID, TEST_USER_FOLDER_PREFIX

        test_query = Submissions.query.execution_options(
            include_test_submissions=True
        ).filter(
            Submissions.Project == int(projectid),
            Submissions.User == int(current_user.Id),
            Submissions.IsCheckpoint == checkpoint,
            Submissions.CodeFilepath.contains(
                f"/{TEST_USER_FOLDER_PREFIX}", autoescape=True
            ),
        )
        if checkpoint and checkpoint_id is not None:
            test_query = test_query.filter(Submissions.CheckpointId == checkpoint_id)
        elif not checkpoint:
            test_query = test_query.filter(Submissions.CheckpointId.is_(None))

        test_attempt_count = test_query.count()
        latest_test = test_query.order_by(
            Submissions.Time.desc(), Submissions.Id.desc()
        ).first()
        if latest_test is not None:
            manual_grade = submission_repo.get_manual_grade_for_submission(latest_test.Id)
            test_grade = (manual_grade or {}).get("grade") or 0
            studentattempts[TEST_USER_ID] = [
                "User", "Test", "", "", test_attempt_count,
                chicago_iso(latest_test.Time), latest_test.IsPassing,
                latest_test.Id, str(class_id), test_grade, "", False,
            ]
        else:
            studentattempts[TEST_USER_ID] = [
                "User", "Test", "", "", "N/A", "N/A", "N/A",
                "N/A", -1, str(class_id), "0", "", False,
            ]

    return make_response(json.dumps(studentattempts), HTTPStatus.OK)


@submissions_api.route('/get_submission_details', methods=["GET"])
@jwt_required()
@inject
def get_submission_details(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Return submission details.

    HTTP: GET /api/submissions/get_submission_details.

    Inputs: project_repo."""
    class_id = int(request.args.get("class_id"))
    # Return the response below when this validation or access check matches.
    if not current_user_can_view_class_id(class_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    proj = project_repo.get_current_project_by_class(class_id)
    # Return the response below when this validation or access check matches.
    if proj is None:
        return make_response(["None", "0", "None", "", "", "-1"], HTTPStatus.OK)

    end_val = getattr(proj, "End", None)
    if isinstance(end_val, datetime):
        end_str = chicago_iso(end_val) or ""
    else:
        end_str = str(end_val or "")

    # Preserve the existing array shape used by StudentUpload, but removed timing fields are inert.
    return make_response(
        [
            "None",
            "0",
            "None",
            str(getattr(proj, "Name", "") or ""),
            end_str,
            str(int(getattr(proj, "Id", 0) or 0)),
        ],
        HTTPStatus.OK,
    )


# Submissions HTTP endpoints for grading.


@submissions_api.route('/submit_grades', methods=["POST"])
@jwt_required()
@inject
def submit_grades(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    # spacing issue
    """Submit grades.

    HTTP: POST /api/submissions/submit_grades.

    Inputs: project_repo."""
    data = request.get_json() or {}
    project_id = data["projectID"]
    # Return the response below when this validation or access check matches.
    if not user_can_access_project_id(int(project_id)):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)
    userId = data["userId"]
    grade = data["grade"]
    project_repo.set_student_grade(int(project_id), int(userId), int(grade))
    return make_response("Grades Submitted", HTTPStatus.OK)


@submissions_api.route('/getprojectscores', methods=["GET"])
@jwt_required()
@inject
def getprojectscores(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
    user_repo: UserRepository = Provide[Container.user_repo],
):
    """Handle getprojectscores for this component.

    HTTP: GET /api/submissions/getprojectscores.

    Inputs: project_repo, submission_repo, user_repo."""
    project_id = str(request.args.get("projectID"))
    # Return the response below when this validation or access check matches.
    if not user_can_access_project_id(int(project_id)):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    data = []
    student_scores = submission_repo.get_project_scores(project_id)
    projectname = project_repo.get_selected_project(project_id).Name
    # Process each score from student_scores.
    for score in student_scores:
        user_info = user_repo.get_user(score[0])
        data.append([user_info.StudentNumber, score[1], user_info.Id])
    return make_response(
        json.dumps({"studentData": data, "projectName": projectname}), HTTPStatus.OK
    )


@submissions_api.route('/submit_Suggestion', methods=["POST"])
@jwt_required()
@inject
def submit_Suggestion(submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    """Submit suggestion.

    HTTP: POST /api/submissions/submit_Suggestion.

    Inputs: submission_repo."""
    # Read the JSON request body using the parsing options below.
    data = request.get_json()
    suggestion = data["suggestion"]
    submission_repo.submitSuggestion(current_user.Id, suggestion)
    return make_response("Suggestion Submitted", HTTPStatus.OK)


@submissions_api.route('/save_grading', methods=["POST"])
@jwt_required()
@inject
def save_grading(submission_repo: SubmissionRepository = Provide[Container.submission_repo]):

    # get the data from frontend
    """Save grading.

    HTTP: POST /api/submissions/save_grading.

    Inputs: submission_repo."""
    # Read the JSON request body using the parsing options below.
    input_json = request.get_json()
    submission_id = input_json.get("submissionId")
    submission = submission_repo.get_submission_by_submission_id(int(submission_id))
    # Return the response below when this validation or access check matches.
    if not user_can_access_submission(submission):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    grade = input_json.get("grade")
    scoring_mode = input_json.get("scoringMode")
    error_points = input_json.get("errorPoints")
    error_defs = input_json.get("errorDefs")
    errors = input_json.get("errors")  # Expecting list: [{startLine,endLine,errorId,count}, ...]
    checkpoint = str(input_json.get("checkpoint", False)).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )
    checkpoint_id_raw = input_json.get("checkpoint_id", None)
    try:
        checkpoint_id = int(checkpoint_id_raw) if checkpoint_id_raw not in (None, "") else None
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
        return make_response(json.dumps({"success": True, "msg": "Grading saved"}), HTTPStatus.OK)
    else:
        return make_response("Failed to save grading", HTTPStatus.INTERNAL_SERVER_ERROR)


@submissions_api.route('/get_grading/<int:submission_id>', methods=["GET"])
@jwt_required()
@inject
def get_grading(
    submission_id, submission_repo: SubmissionRepository = Provide[Container.submission_repo]
):
    """Return grading.

    HTTP: GET /api/submissions/get_grading/<int:submission_id>.

    Inputs: submission_id, submission_repo."""
    submission = submission_repo.get_submission_by_submission_id(int(submission_id))
    # Return the response below when this validation or access check matches.
    if not user_can_access_submission(submission):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    # get errors from db
    error_list = submission_repo.get_manual_errors(submission_id)

    cfg = submission_repo.get_manual_grade_config(submission_id)
    return jsonify(
        {
            "success": True,
            "errors": error_list,
            "grade": cfg.get("grade"),
            "scoringMode": cfg.get("scoringMode"),
            "errorPoints": cfg.get("errorPoints"),
            "errorDefs": cfg.get("errorDefs"),
        }
    )


@submissions_api.route('/export_project_grades', methods=["GET"])
@jwt_required()
@inject
def export_project_grades(
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """Handle export project grades for this component.

    HTTP: GET /api/submissions/export_project_grades.

    Inputs: submission_repo, project_repo."""
    project_id = int(request.args.get("project_id"))
    # Return the response below when this validation or access check matches.
    if not user_can_access_project_id(project_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    checkpoint = str(request.args.get("checkpoint", False)).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )
    # Read this input from the incoming HTTP request.
    checkpoint_id_raw = request.args.get("checkpoint_id", None)
    try:
        checkpoint_id = int(checkpoint_id_raw) if checkpoint_id_raw not in (None, "") else None
    except (TypeError, ValueError):
        checkpoint_id = None

    grade_list = submission_repo.get_project_grade_info(
        project_id, checkpoint=checkpoint, checkpoint_id=checkpoint_id
    )
    project_name = project_repo.get_selected_project(project_id).Name

    sio = StringIO()
    writer = csv.writer(sio, lineterminator="\n")

    headers = [
        "OrgDefinedId",
        f"{project_name} Points Grade",
        f"{project_name} Text Grade",
        "End-of-Line Indicator",
    ]
    writer.writerow(headers)

    # Create excel rows
    base_defs_map = dict(GRADING_DEFAULT_DEFS_MAP)

    # Process each row from grade_list.
    for row in grade_list:
        pts_dict = row["points"] or {}
        scoring_mode = row["scoring_mode"]
        error_data = row["description"]
        row_defs = row.get("error_defs") or {}
        defs_map = dict(base_defs_map)
        # Process each (k, v) from row_defs.items() if isinstance(row_defs, dict) else [].
        for k, v in row_defs.items() if isinstance(row_defs, dict) else []:
            if isinstance(v, dict):
                defs_map[str(k)] = {
                    "label": str(v.get("label", k)),
                    "description": str(v.get("description", "")),
                    "points": int(v.get("points", 0) or 0),
                }
        desc_lines = []
        description = ""

        # Process each error from error_data.
        for error in error_data:
            start = error["startLine"]
            end = error["endLine"]
            errorId = error["errorId"]
            count = error["count"]
            note = error.get("note", "")
            error_def = defs_map.get(errorId, {"label": errorId, "description": "", "points": 0})
            line_str = ""

            base_pts = int(error_def.get("points", 0) or 0)
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
                    line_str += f"{count}x "
            else:
                pts = eff_pts

            line_str += f"[{error_def.get('label', errorId)}] "

            if start == end:
                line_str += f"Line {start}"
            else:
                line_str += f"Lines {start}-{end}"

            line_str += f" (-{pts} pts):"
            desc_lines.append(line_str)

            if isinstance(note, str) and note.strip():
                desc_lines.append(f"Note: {note.strip()}")
            desc_lines.append("")

        if not error_data:
            desc_lines.append("Great Job!")

        description = "\n".join(desc_lines)
        writer.writerow([row.get("number"), row.get("grade"), description, "#"])

    buffer = BytesIO(sio.getvalue().encode("utf-8"))
    buffer.seek(0)

    resp = send_file(
        buffer,
        mimetype="text/csv; charset=utf-8",
        download_name=f"{project_name}-grades.csv",
        as_attachment=True,
    )

    resp.headers["Project-Name"] = project_name
    resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition, Project-Name"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# Submissions HTTP endpoints for audit.


@submissions_api.route('/log_ui_click', methods=["POST"])
@jwt_required()
def log_ui_click():
    """Handle log ui click for this component.

    HTTP: POST /api/submissions/log_ui_click."""
    data = request.get_json(silent=True) or {}
    class_id = data.get("class_id", -1)
    submission_id = data.get("id", -1)
    action = str(data.get("action", "")).strip()
    started_state = data.get("started_state", None)
    previous_state_label = data.get("previous_state_label", None)
    next_state_label = data.get("next_state_label", None)
    checkpoint = parse_bool(data.get("checkpoint", False))
    checkpoint_id = data.get("checkpoint_id", None)

    username = getattr(current_user, "Username", None) or "unknown"
    role = current_user_global_role()

    ts = chicago_now().strftime("%Y-%m-%d %H:%M:%S")

    log_path = ui_clicks_log
    # Ensure the destination directory exists before writing files there.
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    line = (
        f"{ts} | user:{username} | role:{role} | class:{class_id} | submission:{submission_id}"
        f" | action:{action} | checkpoint:{checkpoint}"
    )
    if checkpoint_id not in (None, ""):
        line += f" | checkpoint_id:{checkpoint_id}"
    if action == "Diff Finder" and started_state is not None:
        line += f" | started:{bool(started_state)}"
    if previous_state_label not in (None, ""):
        line += f" | from:{previous_state_label}"
    if next_state_label not in (None, ""):
        line += f" | to:{next_state_label}"

    line += "\n"

    # Open the file for writing and close it automatically when this block finishes.
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)
    return make_response({"status": "logged"}, HTTPStatus.CREATED)


# Submissions HTTP endpoints for rewards.


@submissions_api.route('/incentive_state', methods=["GET"])
@jwt_required()
def incentive_state():
    """Handle incentive state for this component.

    HTTP: GET /api/submissions/incentive_state."""
    # Import at call time because the two features share helper functions.
    from src.upload import current_user_can_use_upload_state

    class_id = parse_int(request.args.get("class_id", 0), 0)
    project_id = parse_int(request.args.get("project_id", 0), 0)
    checkpoint = parse_bool(request.args.get("checkpoint", False))
    checkpoint_id = parse_int(request.args.get("checkpoint_id", 0), 0) if checkpoint else 0

    # Return the response below when this validation or access check matches.
    if class_id <= 0:
        return make_response({"message": "class_id is required."}, HTTPStatus.BAD_REQUEST)

    # Return the response below when this validation or access check matches.
    if project_id > 0 and not current_user_can_use_upload_state(
        class_id, project_id, checkpoint_id
    ):
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


@submissions_api.route('/skip_submission_cooldown', methods=["POST"])
@jwt_required()
def skip_submission_cooldown():
    """Handle skip submission cooldown for this component.

    HTTP: POST /api/submissions/skip_submission_cooldown.
    Database changes are committed at the explicit transaction boundaries below."""
    # Import at call time because the two features share helper functions.
    from src.upload import current_user_can_use_upload_state
    from src.upload import get_student_upload_state_row
    from src.upload import upload_state_scope_from_mapping

    data = request.get_json(silent=True) or {}
    class_id, project_id, checkpoint, checkpoint_id = upload_state_scope_from_mapping(data)

    # Return the response below when this validation or access check matches.
    if class_id <= 0 or project_id <= 0:
        return make_response(
            {"message": "class_id and project_id are required."}, HTTPStatus.BAD_REQUEST
        )

    # Return the response below when this validation or access check matches.
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
            # Mark this record for deletion in the current transaction.
            db.session.delete(row)
            # Commit the pending database changes so they persist beyond this request.
            db.session.commit()
        payload = incentive_payload(
            int(current_user.Id), class_id, project_id, checkpoint, checkpoint_id
        )
        payload["cooldown_remaining_seconds"] = 0
        return jsonify(payload)

    ensure_incentive_tables()

    # Serialize purchases for this student while the balance is calculated.
    locked_user = Users.query.filter(Users.Id == int(current_user.Id)).with_for_update().first()
    if locked_user is None:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        return make_response({"message": "User not found."}, HTTPStatus.NOT_FOUND)

    cooldown_skip_cost = submission_cooldown_skip_cost(checkpoint)
    cooldown_skip_star_label = "star" if cooldown_skip_cost == 1 else "stars"
    balance = get_star_balance(int(current_user.Id), class_id)
    if balance < cooldown_skip_cost:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        return make_response(
            {
                "message": f"You need {cooldown_skip_cost} {cooldown_skip_star_label} to skip the submission timer.",
                "stars": balance,
                "required_stars": cooldown_skip_cost,
                "cooldown_remaining_seconds": remaining_seconds,
            },
            HTTPStatus.BAD_REQUEST,
        )

    # Stage the new records in the current database transaction.
    db.session.add(
        StudentCooldownSkips(
            UserId=int(current_user.Id),
            ClassId=int(class_id),
            ProjectId=int(project_id),
            CheckpointId=int(checkpoint_id or 0),
            SpentStars=cooldown_skip_cost,
            CreatedAt=current_chicago_datetime(),
            UsedAt=None,
        )
    )

    row = get_student_upload_state_row(class_id, project_id, checkpoint_id)
    if row is not None:
        # Mark this record for deletion in the current transaction.
        db.session.delete(row)

    # Commit the pending database changes so they persist beyond this request.
    db.session.commit()

    payload = incentive_payload(
        int(current_user.Id), class_id, project_id, checkpoint, checkpoint_id
    )
    payload["cooldown_remaining_seconds"] = 0
    payload["skipped_cooldown"] = True
    return jsonify(payload)
