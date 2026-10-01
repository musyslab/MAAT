"""Accept student submissions and preserve resumable upload state.

Check enrollment, assignment targets, filenames, and cooldowns before saving
uploads and invoking grading. Record submission outcomes and completion rewards.
The student_upload_state endpoint saves, retrieves, or clears the student's last
submission state so the frontend can resume the same assignment after a reload.
Upload-state parsing retains the submission feature's interpretation of inputs.

Endpoints use /api/upload/<handler_name>."""

from flask_jwt_extended import current_user
from src.core.models import ClassAssignments
from src.core.database import db
from src.core.constants import STUDENT_ROLE
from datetime import datetime
from src.core.models import StudentCheckpointSkips
from src.core.models import StudentCooldownSkips
from src.core.models import StudentStarAwards
from src.core.models import StudentTestcaseInputPurchases
from sqlalchemy import func
from src.core.models import Submissions
from src.core.models import Projects
from http import HTTPStatus
from math import ceil
from flask import make_response
import json
import os
from src.core.models import Checkpoints
from src.repositories.assignment_repository import AssignmentRepository
from src.repositories.assignment_repository import normalize_grader_language
from src.repositories.class_repository import ClassRepository
from src.core.container import Container
from dependency_injector.wiring import Provide
from src.repositories.submission_repository import SubmissionRepository
from src.repositories.user_repository import UserRepository
from dependency_injector.wiring import inject
from flask_jwt_extended import jwt_required
from flask import request
import subprocess
import sys
import tempfile
from src.core.blueprints import upload_api
from src.core.models import Users
from flask import jsonify
from src.core.models import StudentUploadState
from src.core.constants import chicago_now
from src.assignment_permissions import is_test_user_request, TEST_USER_ID, TEST_USER_FOLDER_PREFIX
from src.assignment_permissions import current_user_is_enrolled_in_class
from src.assignment_permissions import user_can_access_class_id
from src.assignment_materials import module_folder_name
from src.assignment_materials import stable_checkpoint_first_name
from src.assignment_materials import stable_project_first_name
from src.assignment_materials import student_root_for_class
from src.assignment_setup import project_module
from src.submissions import assignment_started_early
from src.submissions import parse_submission_datetime_for_cooldown
from src.submissions import project_window
from src.submissions import serialize_cooldown_lifted_at
from src.submissions import submission_cooldown_seconds_for_attempt_count
from src.submissions import submission_scope_query
from src.office_hours import active_office_hours_entry_for_project

# Upload permissions, filenames, target order, cooldowns, rewards, and grader status.


# Parse upload request values before authorization and file handling.


def parse_int(v, default: int = 0) -> int:
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


# Uploads authorization and current-user scope checks.


def user_id_is_enrolled_in_class(user_id: int, class_id: int) -> bool:
    """Return whether the user has a class-assignment row for the class."""
    user_id = parse_int(user_id, 0)
    class_id = parse_int(class_id, 0)
    # Return an empty or negative result when this guard matches.
    if user_id <= 0 or class_id <= 0:
        return False

    return (
        db.session.query(ClassAssignments.UserId)
        .filter(
            ClassAssignments.UserId == user_id,
            ClassAssignments.ClassId == class_id,
        )
        .first()
        is not None
    )


# Policy constants for uploads.




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


# Helpers for uploads time.


# Award completion stars and consume purchased cooldown skips.


def current_star_balance(user_id: int, class_id: int) -> int:
    """Return awards minus every star purchase made in the class."""
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


def award_completion_stars(
    user_id: int,
    class_id: int,
    project,
    is_checkpoint: bool,
    checkpoint_id: int | None,
    submission_id: int,
) -> dict | None:
    """Persist the completion reward for the authorized assignment scope.

    Inputs: user_id, class_id, project, is_checkpoint, checkpoint_id, submission_id.
    Database changes are committed at the explicit transaction boundaries below."""
    # Return an empty or negative result when this guard matches.
    if project is None or submission_id is None:
        return None

    locked_user = Users.query.filter(Users.Id == int(user_id)).with_for_update().first()
    if locked_user is None:
        return None

    checkpoint_key = int(checkpoint_id or 0) if is_checkpoint else 0
    award_type = "checkpoint_completion" if is_checkpoint else "main_completion"

    # Execute the database lookup with the filters specified below.
    existing = StudentStarAwards.query.filter(
        StudentStarAwards.UserId == int(user_id),
        StudentStarAwards.ClassId == int(class_id),
        StudentStarAwards.ProjectId == int(project.Id),
        StudentStarAwards.CheckpointId == checkpoint_key,
        StudentStarAwards.AwardType == award_type,
    ).with_for_update().first()

    # Handle the case where existing is not None.
    if existing is not None:
        return {
            "awarded": False,
            "stars": 0,
            "balance": current_star_balance(user_id, class_id),
            "reason": "already_awarded",
        }

    base_stars = CHECKPOINT_COMPLETION_STARS if is_checkpoint else MAIN_PROJECT_COMPLETION_STARS
    start, end = project_window(project)
    early_deadline = (
        start + ((end - start) / 2)
        if isinstance(start, datetime) and isinstance(end, datetime) and end > start
        else None
    )
    started_early = assignment_started_early(
        user_id,
        project,
        is_checkpoint,
        checkpoint_key,
        early_deadline,
    )
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
        AwardedAt=chicago_now(),
    )
    # Stage the new records in the current database transaction.
    db.session.add(row)
    # Commit the pending database changes so they persist beyond this request.
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
    """Mark an unused purchased skip as consumed by the next submission.

    Inputs: user_id, class_id, project_id, checkpoint_id, latest_submission_time.
    Database changes are committed at the explicit transaction boundaries below."""
    # Execute the database lookup with the filters specified below.
    query = StudentCooldownSkips.query.filter(
        StudentCooldownSkips.UserId == int(user_id),
        StudentCooldownSkips.ClassId == int(class_id),
        StudentCooldownSkips.ProjectId == int(project_id),
        StudentCooldownSkips.CheckpointId == int(checkpoint_id or 0),
        StudentCooldownSkips.UsedAt.is_(None),
    )

    if latest_submission_time is not None:
        submitted_at = parse_submission_datetime_for_cooldown(latest_submission_time)
        if submitted_at is not None:
            query = query.filter(StudentCooldownSkips.CreatedAt >= submitted_at)

    # Execute the database lookup with the filters specified below.
    row = query.order_by(StudentCooldownSkips.CreatedAt.asc()).first()
    # Return an empty or negative result when this guard matches.
    if row is None:
        return False

    row.UsedAt = chicago_now()
    # Commit the pending database changes so they persist beyond this request.
    db.session.commit()
    return True


# Evaluate upload-time cooldowns and office-hours exemptions before grading.


def student_submission_cooldown_response(
    user_id: int,
    class_id: int,
    project_id: int,
    is_checkpoint: bool,
    checkpoint_id: int,
):
    """Handle student submission cooldown response for this component.

    Inputs: user_id, class_id, project_id, is_checkpoint, checkpoint_id."""
    # Return an empty or negative result when this guard matches.
    if not is_test_user_request() and active_office_hours_entry_for_project(user_id, class_id, project_id) is not None:
        return None

    scope_query = submission_scope_query(
        user_id,
        project_id,
        is_checkpoint,
        checkpoint_id,
    )
    completed_attempts = scope_query.count()

    # Return an empty or negative result when this guard matches.
    if completed_attempts <= 0:
        return None

    # Execute the database lookup with the filters specified below.
    latest = scope_query.order_by(Submissions.Time.desc(), Submissions.Id.desc()).first()
    submitted_at = parse_submission_datetime_for_cooldown(getattr(latest, "Time", None))

    # Return an empty or negative result when this guard matches.
    if submitted_at is None:
        return None

    cooldown_seconds = submission_cooldown_seconds_for_attempt_count(
        completed_attempts,
        is_checkpoint,
    )
    elapsed_seconds = (
        chicago_now() - submitted_at
    ).total_seconds()
    remaining_seconds = int(ceil(cooldown_seconds - elapsed_seconds))

    # Return an empty or negative result when this guard matches.
    if remaining_seconds <= 0:
        return None

    # Return an empty or negative result when this guard matches.
    if not is_test_user_request() and consume_pending_cooldown_skip(
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


# Read the external grader status payload associated with an upload.


def load_grader_status(json_out: str) -> tuple[bool, dict]:
    """Treat missing, malformed, or empty grading results as unsuccessful."""
    empty = {"Passed": [], "Failed": []}
    try:
        with open(json_out, "r", encoding="utf-8", errors="replace") as grading_file:
            payload = json.load(grading_file)
        if not isinstance(payload, dict):
            return False, empty
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            return False, empty
        passed, failed = [], []
        for result in results:
            if not isinstance(result, dict) or type(result.get("passed")) is not bool:
                return False, empty
            name = str(result.get("name", "") or "")
            (passed if result["passed"] else failed).append(name)
        return not failed, {"Passed": passed, "Failed": failed}
    except (OSError, ValueError, TypeError):
        return False, empty


# Helpers for uploads language.


# Sanitize uploaded filenames and build stable student assignment directories.


def allowed_file(filename: str) -> bool:
    """Handle allowed file for this component.

    Inputs: filename."""
    # Return an empty or negative result when this guard matches.
    if not filename or "." not in filename:
        return False

    _, extension = os.path.splitext(filename)
    return extension.lower() in ALLOWED_SOURCE_EXTENSIONS


def expected_extensions_for_language(language: str) -> list[str]:
    """Handle expected extensions for language for this component.

    Inputs: language."""
    raw = str(language or "").strip().lower()
    normalized = normalize_grader_language(raw)

    # Handle the case where raw in ALLOWED_EXTENSIONS_BY_LANGUAGE.
    if raw in ALLOWED_EXTENSIONS_BY_LANGUAGE:
        return ALLOWED_EXTENSIONS_BY_LANGUAGE[raw]

    # Handle the case where normalized in ALLOWED_EXTENSIONS_BY_LANGUAGE.
    if normalized in ALLOWED_EXTENSIONS_BY_LANGUAGE:
        return ALLOWED_EXTENSIONS_BY_LANGUAGE[normalized]

    return []


def sanitize_fs_name(value: str) -> str:
    """Handle sanitize fs name for this component.

    Inputs: value."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(value or "").strip())

    return safe or "unknown"


def safe_upload_filename(filename: str) -> str:
    """Handle safe upload filename for this component.

    Inputs: filename."""
    base = os.path.basename(str(filename or "").replace("\\", "/"))
    stem, extension = os.path.splitext(base)

    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(stem or "").strip())

    return f"{safe_stem or 'submission'}{extension.lower()}"


def student_project_bucket(
    class_id: int, project, checkpoint: Checkpoints | None, timestamp_hint: str
) -> str:
    """Return the stable student submission directory for an assignment scope."""
    module = project_module(project)
    module_folder = module_folder_name(
        module,
        getattr(project, "Name", "module"),
        timestamp_hint,
    )

    if checkpoint is not None:
        scope = "checkpoint"
        item_folder = stable_checkpoint_first_name(
            checkpoint,
            getattr(checkpoint, "Name", "checkpoint"),
        )
    else:
        scope = "main"
        item_folder = stable_project_first_name(project, getattr(project, "Name", "project"))

    return os.path.join(student_root_for_class(class_id), module_folder, scope, item_folder)


def resolve_additional_files_payload(owner, solution_path: str) -> str:
    """Resolve additional files payload.

    Inputs: owner, solution_path."""
    try:
        teacher_base_dir = (
            solution_path
            if solution_path and os.path.isdir(solution_path)
            else os.path.dirname(solution_path or "")
        )

        raw = str(getattr(owner, "AdditionalFilePath", "") or "").strip()

        # Handle the case where not raw.
        if not raw:
            return json.dumps({"base_dir": teacher_base_dir, "files": []})

        if raw.startswith("[") or raw.startswith("{"):
            parsed = json.loads(raw)
        else:
            parsed = [raw]

        if isinstance(parsed, dict):
            parsed = parsed.get("files", [])

        abs_list = []

        # Process each path_value from parsed or [].
        for path_value in parsed or []:
            if not path_value:
                continue

            path_text = str(path_value)

            if os.path.isabs(path_text):
                abs_list.append(path_text)
            else:
                abs_list.append(os.path.join(teacher_base_dir, os.path.basename(path_text)))

        return json.dumps({"base_dir": teacher_base_dir, "files": abs_list})
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return ""


# Determine valid upload destinations and enforce checkpoint submission order.


def student_upload_targets(
    project_repo: AssignmentRepository,
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
        # Execute the database lookup with the filters specified below.
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
        passed_checkpoint_ids = {int(row[0]) for row in passed_rows if row and row[0] is not None}

    # Execute the database lookup with the filters specified below.
    skipped_rows = StudentCheckpointSkips.query.filter(
        StudentCheckpointSkips.UserId == int(user_id),
        StudentCheckpointSkips.ProjectId == int(project_id),
    ).all()
    skipped_checkpoint_ids = {int(getattr(row, "CheckpointId", 0) or 0) for row in skipped_rows}

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
    # Process each (index, checkpoint) from enumerate(checkpoint_rows).
    for index, checkpoint in enumerate(checkpoint_rows):
        checkpoint_id = int(getattr(checkpoint, "Id", 0) or 0)
        completed = checkpoint_id in completed_checkpoint_ids
        available = completed or index == first_incomplete_index

        targets.append(
            {
                "id": checkpoint_id,
                "number": index + 1,
                "name": str(getattr(checkpoint, "Name", "") or f"Checkpoint {index + 1}"),
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
    project_repo: AssignmentRepository,
    user_id: int,
    project_id: int,
    is_checkpoint: bool,
    checkpoint_id: int,
):
    """Handle upload target order error for this component.

    Inputs: project_repo, user_id, project_id, is_checkpoint, checkpoint_id."""
    targets = student_upload_targets(project_repo, user_id, project_id)

    if not is_checkpoint:
        # Return an empty or negative result when this guard matches.
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

    # Return an empty or negative result when this guard matches.
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


# Authorize uploads, select destinations, invoke grading, and record the result.


# Uploads HTTP endpoints for submission.


@upload_api.route('/file_upload', methods=["POST"])
@jwt_required()
@inject
def file_upload(
    user_repository: UserRepository = Provide[Container.user_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    class_repo: ClassRepository = Provide[Container.class_repo],
):
    """Authorize, store, grade, and record an uploaded or editor-submitted program.

    HTTP: POST /api/upload/file_upload.

    Inputs: user_repository, submission_repo, project_repo, class_repo.
    Invokes the configured external runner; preserve its timeout and output handling."""
    # Read this input from the incoming HTTP request.
    class_id = request.form.get("class_id", "").strip()

    # Return the response below when this validation or access check matches.
    if not class_id:
        return make_response({"message": "Missing class_id"}, HTTPStatus.BAD_REQUEST)

    class_id_int = parse_int(class_id, 0)

    # Return the response below when this validation or access check matches.
    if class_id_int <= 0:
        return make_response({"message": "Invalid class_id"}, HTTPStatus.BAD_REQUEST)

    # Read this input from the incoming HTTP request.
    submission_method = request.form.get("submission_method", "").strip().lower()

    if not submission_method:
        submission_method = "upload" if "student_id" in request.form else "unknown"

    # Return the response below when this validation or access check matches.
    if submission_method not in {"upload", "editor", "unknown"}:
        return make_response(
            {"message": "Invalid submission_method"},
            HTTPStatus.BAD_REQUEST,
        )

    is_staff_upload = user_can_access_class_id(class_id_int)

    # Return the response below when this validation or access check matches.
    if "student_id" in request.form and not is_staff_upload:
        return make_response({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    # Return the response below when this validation or access check matches.
    if not is_staff_upload and not current_user_is_enrolled_in_class(class_id_int):
        return make_response({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    username = current_user.Username
    user_id = current_user.Id

    is_test_upload = is_test_user_request() and is_staff_upload
    if request.headers.get("X-MAAT-Test-User") == "1" and not is_staff_upload:
        return make_response({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)
    if is_test_upload:
        username = f"{TEST_USER_FOLDER_PREFIX}{current_user.Id}"

    if "student_id" in request.form and not is_test_upload:
        student_id = parse_int(request.form.get("student_id"), 0)

        # Return the response below when this validation or access check matches.
        if student_id <= 0:
            return make_response(
                {"message": "Invalid student_id"},
                HTTPStatus.BAD_REQUEST,
            )

        user_lookup = user_repository.get_user(student_id)
        username = getattr(user_lookup, "Username", user_lookup)

        # Return the response below when this validation or access check matches.
        if not username:
            return make_response(
                {"message": "Student not found"},
                HTTPStatus.NOT_FOUND,
            )

        user_obj = user_repository.getUserByName(username)

        # Return the response below when this validation or access check matches.
        if not user_obj:
            return make_response(
                {"message": "Student not found"},
                HTTPStatus.NOT_FOUND,
            )

        user_id = user_obj.Id

        # Return the response below when this validation or access check matches.
        if not user_id_is_enrolled_in_class(user_id, class_id_int):
            return make_response(
                {"message": "Student is not enrolled in this class"},
                HTTPStatus.FORBIDDEN,
            )

    project = None

    if "project_id" in request.form:
        project_id = parse_int(request.form.get("project_id"), 0)

        # Return the response below when this validation or access check matches.
        if project_id <= 0:
            return make_response(
                {"message": "Invalid project_id"},
                HTTPStatus.BAD_REQUEST,
            )

        project = project_repo.get_selected_project(project_id)
    else:
        project = project_repo.get_current_project_by_class(class_id)

    # Return the response below when this validation or access check matches.
    if project is None:
        return make_response(
            {"message": "No active project"},
            HTTPStatus.NOT_ACCEPTABLE,
        )

    # Return the response below when this validation or access check matches.
    if int(getattr(project, "ClassId", 0) or 0) != class_id_int:
        return make_response(
            {"message": "Project does not belong to this class"},
            HTTPStatus.BAD_REQUEST,
        )

    checkpoint_id = parse_int(request.form.get("checkpoint_id", ""), 0)
    is_checkpoint = parse_bool(request.form.get("checkpoint", "")) or checkpoint_id > 0

    checkpoint: Checkpoints | None = None

    if is_checkpoint:
        # Return the response below when this validation or access check matches.
        if checkpoint_id <= 0:
            return make_response(
                {"message": "Missing checkpoint_id"},
                HTTPStatus.BAD_REQUEST,
            )

        checkpoint = project_repo.get_checkpoint(checkpoint_id)

        # Return the response below when this validation or access check matches.
        if checkpoint is None:
            return make_response(
                {"message": "Checkpoint not found"},
                HTTPStatus.NOT_FOUND,
            )

        # Return the response below when this validation or access check matches.
        if int(getattr(checkpoint, "ProjectId", 0) or 0) != int(project.Id):
            return make_response(
                {"message": "Checkpoint does not belong to this project"},
                HTTPStatus.BAD_REQUEST,
            )

        # Return the response below when this validation or access check matches.
        if not bool(getattr(checkpoint, "Enabled", True)):
            return make_response(
                {"message": "Checkpoint is disabled"},
                HTTPStatus.FORBIDDEN,
            )

    order_error = None if is_test_upload else upload_target_order_error(
        project_repo,
        user_id,
        int(project.Id),
        is_checkpoint,
        checkpoint_id,
    )
    # Handle the case where order_error is not None.
    if order_error is not None:
        return order_error

    if not is_staff_upload or (is_test_upload and request.headers.get("X-MAAT-Test-User") == "1"):
        cooldown_response = student_submission_cooldown_response(
            user_id,
            class_id_int,
            int(project.Id),
            is_checkpoint,
            checkpoint_id,
        )

        # Handle the case where cooldown_response is not None.
        if cooldown_response is not None:
            return cooldown_response

    # Read this input from the incoming HTTP request.
    upload_files = request.files.getlist("files")

    if not upload_files:
        # Read this input from the incoming HTTP request.
        single = request.files.get("file")

        if single and single.filename:
            upload_files = [single]

    upload_files = [f for f in upload_files if f and f.filename]

    # Return the response below when this validation or access check matches.
    if not upload_files:
        return make_response({"message": "No selected file"}, HTTPStatus.BAD_REQUEST)

    # Return the response below when this validation or access check matches.
    if not all(allowed_file(f.filename) for f in upload_files):
        return make_response(
            {"message": "Unsupported file type"},
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )

    safe_filenames = [safe_upload_filename(f.filename) for f in upload_files]
    if len(safe_filenames) != len(set(safe_filenames)):
        return make_response(
            {"message": "Uploaded filenames must be unique after sanitization"},
            HTTPStatus.BAD_REQUEST,
        )

    owner = checkpoint if checkpoint is not None else project

    effective_language = (
        getattr(owner, "Language", None) or getattr(project, "Language", None) or ""
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

    # Return the response below when this validation or access check matches.
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

    # Return the response below when this validation or access check matches.
    if submission_method == "editor" and grader_language != "py":
        return make_response(
            {"message": "The Python editor can only submit Python assignments"},
            HTTPStatus.BAD_REQUEST,
        )

    expected_extensions = expected_extensions_for_language(effective_language)

    if not expected_extensions:
        expected_extensions = expected_extensions_for_language(grader_language)

    # Return the response below when this validation or access check matches.
    if not expected_extensions:
        return make_response(
            {"message": "Unsupported language"},
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )

    submitted_extensions = [os.path.splitext(f.filename)[1].lower() for f in upload_files]

    if grader_language == "java":
        invalid_java_files = [
            f.filename for f in upload_files if os.path.splitext(f.filename)[1].lower() != ".java"
        ]

        # Return the response below when this validation or access check matches.
        if invalid_java_files:
            return make_response(
                {"message": ("Selected project expects Java: upload one or more .java files.")},
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
    else:
        # Return the response below when this validation or access check matches.
        if len(upload_files) != 1:
            return make_response(
                {"message": ("Only Java projects support multi-file student uploads.")},
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
    ts_now = chicago_now()
    ts_stamp = ts_now.strftime("%Y%m%d_%H%M%S")

    project_bucket = student_project_bucket(
        class_id_int,
        project,
        checkpoint if is_checkpoint else None,
        ts_stamp,
    )

    safe_username = sanitize_fs_name(username)
    user_bucket = os.path.join(project_bucket, safe_username)
    # Ensure the destination directory exists before writing files there.
    os.makedirs(user_bucket, exist_ok=True)

    outputpath = project_bucket
    submission_dir = tempfile.mkdtemp(prefix=f"{ts_stamp}_", dir=user_bucket)

    # Process each upload_file from upload_files.
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
        sys.executable,
        grading_script,
        str(username),
        grader_language,
        str(testcase_info_json),
        submission_dir,
        add_payload,
        project_id_arg,
        class_id_arg,
    ]

    # Launch the external command with the execution options below.
    try:
        result = subprocess.run(cmd, cwd=outputpath, timeout=300)
    except subprocess.TimeoutExpired:
        return make_response(
            {"message": "The grading script timed out"}, HTTPStatus.GATEWAY_TIMEOUT
        )
    except OSError:
        return make_response(
            {"message": "The grading script could not be started"},
            HTTPStatus.SERVICE_UNAVAILABLE,
        )

    # Return the response below when this validation or access check matches.
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
        time=ts_now,
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

    completed_attempts = submission_scope_query(
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
        "office_hours_cooldown_exempt_until": serialize_cooldown_lifted_at(
            getattr(office_hours_entry, "CooldownExemptUntil", None)
        ),
        "star_award": star_award,
        "stars": current_star_balance(user_id, class_id_int),
    }

    return make_response(message, HTTPStatus.OK)


# Uploads HTTP endpoints for targets.


@upload_api.route('/total_students', methods=["GET"])
@jwt_required()
@inject
def total_students(user_repo: UserRepository = Provide[Container.user_repo]):
    """Handle total students for this component.

    HTTP: GET /api/upload/total_students.

    Inputs: user_repo."""
    # Read this input from the incoming HTTP request.
    class_id = request.args.get("class_id")
    class_id_int = parse_int(class_id, 0)

    # Return the response below when this validation or access check matches.
    if class_id_int <= 0:
        return make_response({"message": "Invalid class_id"}, HTTPStatus.BAD_REQUEST)

    # Return the response below when this validation or access check matches.
    if not user_can_access_class_id(class_id_int):
        return make_response({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    # Execute the database lookup with the filters specified below.
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

    list_of_user_info = [{"name": "Test User", "mscsnet": "Admin testing", "id": TEST_USER_ID}]

    # Process each user from users.
    for user in users:
        list_of_user_info.append(
            {
                "name": f"{user.Firstname} {user.Lastname}".strip(),
                "mscsnet": user.Username,
                "id": user.Id,
            }
        )

    return jsonify(list_of_user_info)


@upload_api.route('/available_targets', methods=["GET"])
@jwt_required()
@inject
def available_targets(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """Handle available targets for this component.

    HTTP: GET /api/upload/available_targets.

    Inputs: project_repo."""
    class_id = parse_int(request.args.get("class_id"), 0)
    project_id = parse_int(request.args.get("project_id"), 0)
    student_id = parse_int(request.args.get("student_id"), 0)

    # Return the response below when this validation or access check matches.
    if class_id <= 0 or project_id <= 0 or (student_id <= 0 and student_id != TEST_USER_ID):
        return make_response(
            {"message": "class_id, project_id, and student_id are required"},
            HTTPStatus.BAD_REQUEST,
        )

    # Return the response below when this validation or access check matches.
    if not user_can_access_class_id(class_id):
        return make_response({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    # Return the response below when this validation or access check matches.
    if student_id != TEST_USER_ID and not user_id_is_enrolled_in_class(student_id, class_id):
        return make_response(
            {"message": "Student is not enrolled in this class"},
            HTTPStatus.FORBIDDEN,
        )

    project = project_repo.get_selected_project(project_id)
    # Return the response below when this validation or access check matches.
    if project is None or int(getattr(project, "ClassId", 0) or 0) != class_id:
        return make_response(
            {"message": "Project does not belong to this class"},
            HTTPStatus.BAD_REQUEST,
        )

    targets = student_upload_targets(project_repo, current_user.Id if student_id == TEST_USER_ID else student_id, project_id)
    if student_id == TEST_USER_ID:
        targets["mainAvailable"] = True
        for target in targets["checkpoints"]:
            target["available"] = bool(target["enabled"])
    return jsonify(targets)


# Validate, persist, and return resumable student upload state.


# Resolve and serialize resumable student upload state and its submission scope.


def upload_state_scope_from_mapping(mapping) -> tuple[int, int, bool, int]:
    """Handle upload state scope from mapping for this component.

    Inputs: mapping."""
    # Defer shared feature imports until the request or helper call.
    from src.submissions import parse_bool as upload_state_parse_bool
    from src.submissions import parse_int as upload_state_parse_int

    class_id = upload_state_parse_int(mapping.get("class_id", 0), 0)
    project_id = upload_state_parse_int(mapping.get("project_id", 0), 0)
    checkpoint = upload_state_parse_bool(mapping.get("checkpoint", False))
    checkpoint_id = upload_state_parse_int(mapping.get("checkpoint_id", 0), 0) if checkpoint else 0

    return class_id, project_id, checkpoint, max(0, checkpoint_id)


def current_user_can_use_upload_state(class_id: int, project_id: int, checkpoint_id: int) -> bool:
    """Determine whether current user can use upload state.

    Inputs: class_id, project_id, checkpoint_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import current_user_can_access_visible_project_id
    from src.submissions import parse_int as upload_state_parse_int

    class_id = upload_state_parse_int(class_id, 0)
    project_id = upload_state_parse_int(project_id, 0)
    checkpoint_id = upload_state_parse_int(checkpoint_id, 0)

    # Return an empty or negative result when this guard matches.
    if class_id <= 0 or project_id <= 0:
        return False

    # Execute the database lookup with the filters specified below.
    project = Projects.query.filter(Projects.Id == project_id).first()
    # Return an empty or negative result when this guard matches.
    if project is None:
        return False

    # Return an empty or negative result when this guard matches.
    if upload_state_parse_int(getattr(project, "ClassId", 0), 0) != class_id:
        return False

    # Return an empty or negative result when this guard matches.
    if not current_user_can_access_visible_project_id(project_id):
        return False

    if checkpoint_id > 0:
        # Execute the database lookup with the filters specified below.
        checkpoint = Checkpoints.query.filter(
            Checkpoints.Id == checkpoint_id,
            Checkpoints.ProjectId == project_id,
        ).first()
        return checkpoint is not None

    return True


def get_student_upload_state_row(class_id: int, project_id: int, checkpoint_id: int):
    """Return student upload state row.

    Inputs: class_id, project_id, checkpoint_id."""
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
    """Handle latest submission for upload state for this component.

    Inputs: submission_repo, project_id, checkpoint, checkpoint_id."""
    # Defer shared feature imports until the request or helper call.
    from src.submissions import latest_checkpoint_submission

    # Handle the case where checkpoint.
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


def submission_matches_upload_state(
    submission, project_id: int, checkpoint: bool, checkpoint_id: int
) -> bool:
    """Handle submission matches upload state for this component.

    Inputs: submission, project_id, checkpoint, checkpoint_id."""
    # Return an empty or negative result when this guard matches.
    if submission is None:
        return False

    # Return an empty or negative result when this guard matches.
    if int(getattr(submission, "User", -1) or -1) != int(current_user.Id):
        return False

    # Return an empty or negative result when this guard matches.
    if int(getattr(submission, "Project", -1) or -1) != int(project_id):
        return False

    if checkpoint:
        # Return an empty or negative result when this guard matches.
        if not bool(getattr(submission, "IsCheckpoint", False)):
            return False

        # Return an empty or negative result when this guard matches.
        if checkpoint_id > 0 and int(getattr(submission, "CheckpointId", 0) or 0) != int(
            checkpoint_id
        ):
            return False

    # Return an empty or negative result when this guard matches.
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
    """Serialize student upload state.

    Inputs: submission_repo, class_id, project_id, checkpoint, checkpoint_id."""
    # Defer shared feature imports until the request or helper call.
    from src.submissions import submission_cooldown_state

    latest_submission = latest_submission_for_upload_state(
        submission_repo,
        int(project_id),
        bool(checkpoint),
        int(checkpoint_id or 0),
    )
    latest_submission_id = (
        int(getattr(latest_submission, "Id", 0) or 0) if latest_submission is not None else None
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


# Submissions HTTP endpoints for upload state.


@upload_api.route('/student_upload_state', methods=["GET", "POST", "DELETE"])
@jwt_required()
@inject
def student_upload_state(
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    """Handle student upload state for this component.

    HTTP: GET, POST, DELETE /api/upload/student_upload_state.

    Inputs: submission_repo.
    Database changes are committed at the explicit transaction boundaries below."""
    source = request.args if request.method == "GET" else (request.get_json(silent=True) or {})
    class_id, project_id, checkpoint, checkpoint_id = upload_state_scope_from_mapping(source)

    # Return the response below when this validation or access check matches.
    if class_id <= 0 or project_id <= 0:
        return make_response(
            {"message": "class_id and project_id are required."}, HTTPStatus.BAD_REQUEST
        )

    # Return the response below when this validation or access check matches.
    if not current_user_can_use_upload_state(class_id, project_id, checkpoint_id):
        return make_response("Not Authorized", HTTPStatus.UNAUTHORIZED)

    row = get_student_upload_state_row(class_id, project_id, checkpoint_id)

    if request.method == "GET":
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

    if request.method == "DELETE":
        if row is not None:
            # Mark this record for deletion in the current transaction.
            db.session.delete(row)
            # Commit the pending database changes so they persist beyond this request.
            db.session.commit()

        return jsonify(
            {
                "last_submission_id": None,
                "previous_submission_id": None,
                "cooldown_lifted_at": None,
                "cooldown_remaining_seconds": 0,
            }
        )

    if row is not None:
        # Mark this record for deletion in the current transaction.
        db.session.delete(row)
        # Commit the pending database changes so they persist beyond this request.
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
