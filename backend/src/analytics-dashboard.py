"""Build class progress and activity reports for authorized users.

Combine assignment, checkpoint, and submission records into dashboard summaries
and downloadable report data. Reuse assignment permission checks and naming rules
so analytics refer to the same students and assignments as the teaching interface.

Endpoints use /api/analytics-dashboard/<handler_name>."""

from collections import defaultdict
from itertools import groupby
from sqlalchemy import and_
from src.core.database import db
from src.core.constants import STUDENT_ROLE
from src.core.constants import chicago_iso
from src.core.models import ClassAssignments
from src.core.models import Labs
from src.core.models import LectureSections
from src.core.models import Users
from src.core.models import StudentCheckpointSkips
from src.core.models import CheckpointGrades
from src.core.models import MainAssignmentGrades
from src.core.models import Submissions
from src.repositories.assignment_repository import AssignmentRepository
from src.assignment_setup import default_checkpoint_name
from src.assignment_setup import parse_int
from dependency_injector.wiring import Provide
from dependency_injector.wiring import inject
from flask import jsonify
from flask import make_response
from flask import request
from flask_jwt_extended import jwt_required, current_user
from types import SimpleNamespace
from http import HTTPStatus
from src.core.blueprints import analytics_api
from src.core.container import Container
from src.assignment_permissions import access_denied_response
from src.assignment_permissions import is_staff_user
from src.assignment_permissions import user_can_access_class_id


# Build and serve class progress dashboards.


# Build class dashboard statistics and per-student progress payloads.


def analytics_iso(value) -> str:
    """Handle analytics iso for this component.

    Inputs: value."""
    # Return an empty or negative result when this guard matches.
    if value is None:
        return ""

    try:
        return chicago_iso(value) or ""
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return str(value or "")


def analytics_student_row_payload(
    user,
    lecture_name: str,
    lab_name: str,
    class_id: int,
    *,
    submission=None,
    attempts=0,
    grade=0,
    skipped=False,
):
    """Handle analytics student row payload for this component.

    Inputs: user, lecture_name, lab_name, class_id, submission, attempts, grade, skipped."""
    student_id = str(getattr(user, "StudentNumber", "") or "")
    last_name = str(getattr(user, "Lastname", "") or "")
    first_name = str(getattr(user, "Firstname", "") or "")
    lecture = str(lecture_name or "")
    lab = str(lab_name or "")
    is_locked = bool(getattr(user, "IsLocked", False))

    # Handle the case where submission is None.
    if submission is None:
        return [
            last_name,
            first_name,
            lecture,
            lab,
            "N/A",
            "N/A",
            "N/A",
            -1,
            str(class_id),
            "0",
            student_id,
            is_locked,
            bool(skipped),
        ]

    return [
        last_name,
        first_name,
        lecture,
        lab,
        int(attempts or 0),
        analytics_iso(getattr(submission, "Time", None)),
        bool(getattr(submission, "IsPassing", False)),
        int(getattr(submission, "Id", 0) or 0),
        str(class_id),
        grade if grade is not None else 0,
        student_id,
        is_locked,
        bool(skipped),
    ]


def analytics_submission_is_newer(candidate, current) -> bool:
    """Handle analytics submission is newer for this component.

    Inputs: candidate, current."""
    # Handle the case where current is None.
    if current is None:
        return True

    candidate_time = getattr(candidate, "Time", None)
    current_time = getattr(current, "Time", None)

    # Handle the case where candidate_time is not None and current_time is not None and (candidate_time != current_time).
    if candidate_time is not None and current_time is not None and candidate_time != current_time:
        return candidate_time > current_time
    # Handle the case where candidate_time is not None and current_time is None.
    if candidate_time is not None and current_time is None:
        return True
    # Return an empty or negative result when this guard matches.
    if candidate_time is None and current_time is not None:
        return False

    return int(getattr(candidate, "Id", 0) or 0) > int(getattr(current, "Id", 0) or 0)


def analytics_dashboard_students(class_id: int):
    """Handle analytics dashboard students for this component.

    Inputs: class_id."""
    # Execute the database lookup with the filters specified below.
    rows = (
        db.session.query(
            Users,
            LectureSections.Name,
            Labs.Name,
            ClassAssignments.Role,
        )
        .join(ClassAssignments, ClassAssignments.UserId == Users.Id)
        .outerjoin(
            LectureSections,
            and_(
                ClassAssignments.LectureId == LectureSections.Id,
                LectureSections.ClassId == int(class_id),
            ),
        )
        .outerjoin(
            Labs,
            and_(
                ClassAssignments.LabId == Labs.Id,
                Labs.ClassId == int(class_id),
            ),
        )
        .filter(ClassAssignments.ClassId == int(class_id))
        .order_by(Users.Lastname.asc(), Users.Firstname.asc(), Users.Id.asc())
        .all()
    )

    student_rows = []
    # Process each (user, lecture_name, lab_name, assignment_role) from rows.
    for user, lecture_name, lab_name, assignment_role in rows:
        role = parse_int(assignment_role, STUDENT_ROLE)
        if role != STUDENT_ROLE:
            continue

        student_rows.append(
            {
                "user": user,
                "lecture": str(lecture_name or ""),
                "lab": str(lab_name or ""),
            }
        )

    return student_rows


def analytics_checkpoint_payloads(
    project_ids: list[int],
    project_repo: AssignmentRepository,
) -> dict[str, list[dict]]:
    """Handle analytics checkpoint payloads for this component.

    Inputs: project_ids, project_repo."""
    # Return an empty or negative result when this guard matches.
    if not project_ids:
        return {}

    payloads: dict[str, list[dict]] = {str(project_id): [] for project_id in project_ids}

    checkpoints = project_repo.list_checkpoints_by_project_ids(project_ids)
    # Process each (project_id, project_checkpoints) from groupby(checkpoints, key=lambda checkpoint: int(checkpoint.ProjectId)).
    for project_id, project_checkpoints in groupby(
        checkpoints, key=lambda checkpoint: int(checkpoint.ProjectId)
    ):
        # Process each (index, checkpoint) from enumerate(project_checkpoints).
        for index, checkpoint in enumerate(project_checkpoints):
            checkpoint_id = int(getattr(checkpoint, "Id", 0) or 0)
            if checkpoint_id <= 0:
                continue

            checkpoint_number = index + 1
            checkpoint_name = str(
                getattr(checkpoint, "Name", "") or default_checkpoint_name(checkpoint_number)
            )

            payloads.setdefault(str(project_id), []).append(
                {
                    "id": checkpoint_id,
                    "Id": checkpoint_id,
                    "checkpointId": checkpoint_id,
                    "CheckpointId": checkpoint_id,
                    "number": checkpoint_number,
                    "Number": checkpoint_number,
                    "name": checkpoint_name,
                    "Name": checkpoint_name,
                    "enabled": bool(getattr(checkpoint, "Enabled", True)),
                    "Enabled": bool(getattr(checkpoint, "Enabled", True)),
                }
            )

    return payloads


def analytics_dashboard_progress(
    class_id: int, project_ids: list[int], checkpoints_by_project_id: dict[str, list[dict]]
):
    """Handle analytics dashboard progress for this component.

    Inputs: class_id, project_ids, checkpoints_by_project_id."""
    students = analytics_dashboard_students(class_id)
    student_ids = [int(getattr(row["user"], "Id", 0) or 0) for row in students]

    item_ids = [f"main-{project_id}" for project_id in project_ids]
    checkpoint_ids_by_project: dict[int, list[int]] = defaultdict(list)
    # Process each (project_id_key, checkpoints) from checkpoints_by_project_id.items().
    for project_id_key, checkpoints in checkpoints_by_project_id.items():
        project_id = parse_int(project_id_key, 0)
        # Process each checkpoint from checkpoints.
        for checkpoint in checkpoints:
            checkpoint_id = parse_int(checkpoint.get("id") or checkpoint.get("Id"), 0)
            if project_id > 0 and checkpoint_id > 0:
                checkpoint_ids_by_project[project_id].append(checkpoint_id)
                item_ids.append(f"checkpoint-{project_id}-{checkpoint_id}")

    # Handle the case where not project_ids or not student_ids.
    if not project_ids or not student_ids:
        return {item_id: {} for item_id in item_ids}

    # Execute the database lookup with the filters specified below.
    submissions = (
        Submissions.query.filter(
            Submissions.Project.in_(project_ids), Submissions.User.in_(student_ids)
        )
        .yield_per(500)
    )

    skipped_checkpoint_keys = {
        (
            int(getattr(row, "ProjectId", 0) or 0),
            int(getattr(row, "CheckpointId", 0) or 0),
            int(getattr(row, "UserId", 0) or 0),
        )
        for row in StudentCheckpointSkips.query.filter(
            StudentCheckpointSkips.ClassId == int(class_id),
            StudentCheckpointSkips.ProjectId.in_(project_ids),
            StudentCheckpointSkips.UserId.in_(student_ids),
        ).all()
        if int(getattr(row, "ProjectId", 0) or 0) > 0
        and int(getattr(row, "CheckpointId", 0) or 0) > 0
        and int(getattr(row, "UserId", 0) or 0) > 0
    }

    main_attempt_counts: dict[tuple[int, int], int] = defaultdict(int)
    checkpoint_attempt_counts: dict[tuple[int, int, int], int] = defaultdict(int)
    latest_main: dict[tuple[int, int], Submissions] = {}
    latest_checkpoint: dict[tuple[int, int, int], Submissions] = {}

    # Process each submission from submissions.
    for submission in submissions:
        project_id = int(getattr(submission, "Project", 0) or 0)
        user_id = int(getattr(submission, "User", 0) or 0)
        if project_id <= 0 or user_id <= 0:
            continue

        if bool(getattr(submission, "IsCheckpoint", False)):
            checkpoint_id = parse_int(getattr(submission, "CheckpointId", None), 0)
            if checkpoint_id <= 0:
                continue
            key = (project_id, checkpoint_id, user_id)
            checkpoint_attempt_counts[key] += 1
            if analytics_submission_is_newer(submission, latest_checkpoint.get(key)):
                latest_checkpoint[key] = submission
        else:
            key = (project_id, user_id)
            main_attempt_counts[key] += 1
            if analytics_submission_is_newer(submission, latest_main.get(key)):
                latest_main[key] = submission

    main_grades = {
        (int(row.ProjectId), int(row.UserId)): row.Grade
        for row in MainAssignmentGrades.query.filter(
            MainAssignmentGrades.ProjectId.in_(project_ids),
            MainAssignmentGrades.UserId.in_(student_ids),
        ).all()
    }

    latest_checkpoint_submission_ids = [
        int(getattr(submission, "Id", 0) or 0)
        for submission in latest_checkpoint.values()
        if int(getattr(submission, "Id", 0) or 0) > 0
    ]
    checkpoint_grades = {}
    if latest_checkpoint_submission_ids:
        checkpoint_grades = {
            int(row.SubmissionId): row.Grade
            for row in CheckpointGrades.query.filter(
                CheckpointGrades.SubmissionId.in_(latest_checkpoint_submission_ids)
            ).all()
        }

    progress = {item_id: {} for item_id in item_ids}

    # Process each student_row from students.
    for student_row in students:
        user = student_row["user"]
        user_id = int(getattr(user, "Id", 0) or 0)
        lecture = student_row["lecture"]
        lab = student_row["lab"]

        # Process each project_id from project_ids.
        for project_id in project_ids:
            main_item_id = f"main-{project_id}"
            main_key = (project_id, user_id)
            main_submission = latest_main.get(main_key)
            progress[main_item_id][str(user_id)] = analytics_student_row_payload(
                user,
                lecture,
                lab,
                class_id,
                submission=main_submission,
                attempts=main_attempt_counts.get(main_key, 0),
                grade=main_grades.get(main_key, 0),
            )

            # Process each checkpoint_id from checkpoint_ids_by_project.get(project_id, []).
            for checkpoint_id in checkpoint_ids_by_project.get(project_id, []):
                checkpoint_item_id = f"checkpoint-{project_id}-{checkpoint_id}"
                checkpoint_key = (project_id, checkpoint_id, user_id)
                checkpoint_submission = latest_checkpoint.get(checkpoint_key)
                checkpoint_grade = 0
                if checkpoint_submission is not None:
                    checkpoint_grade = checkpoint_grades.get(
                        int(getattr(checkpoint_submission, "Id", 0) or 0), 0
                    )

                progress[checkpoint_item_id][str(user_id)] = analytics_student_row_payload(
                    user,
                    lecture,
                    lab,
                    class_id,
                    submission=checkpoint_submission,
                    attempts=checkpoint_attempt_counts.get(checkpoint_key, 0),
                    grade=checkpoint_grade,
                    skipped=checkpoint_key in skipped_checkpoint_keys,
                )

    return progress



def analytics_test_user_progress(
    progress: dict, class_id: int, project_ids: list[int],
):
    """Add the current staff account's virtual Test User to existing item rows.

    No account/enrollment rows are created. Explicitly load test-folder attempts
    only for projects already authorized through the class dashboard endpoint.
    """
    from src.assignment_permissions import TEST_USER_ID, TEST_USER_FOLDER_PREFIX

    virtual_user = SimpleNamespace(
        Id=TEST_USER_ID, Firstname="Test", Lastname="User",
        StudentNumber="", IsLocked=False,
    )
    attempts = defaultdict(int)
    latest = {}
    if project_ids:
        test_submissions = Submissions.query.execution_options(
            include_test_submissions=True
        ).filter(
            Submissions.Project.in_(project_ids),
            Submissions.User == int(current_user.Id),
            Submissions.CodeFilepath.contains(
                f"/{TEST_USER_FOLDER_PREFIX}", autoescape=True
            ),
        ).yield_per(500)
        for submission in test_submissions:
            project_id = int(submission.Project)
            if bool(getattr(submission, "IsCheckpoint", False)):
                checkpoint_id = parse_int(getattr(submission, "CheckpointId", None), 0)
                item_id = f"checkpoint-{project_id}-{checkpoint_id}"
            else:
                if getattr(submission, "CheckpointId", None) is not None:
                    continue
                item_id = f"main-{project_id}"
            if item_id not in progress:
                continue
            attempts[item_id] += 1
            if analytics_submission_is_newer(submission, latest.get(item_id)):
                latest[item_id] = submission

    checkpoint_submission_ids = [
        int(submission.Id) for item_id, submission in latest.items()
        if item_id.startswith("checkpoint-")
    ]
    grades = {}
    if checkpoint_submission_ids:
        grades = {
            int(row.SubmissionId): row.Grade
            for row in CheckpointGrades.query.filter(
                CheckpointGrades.SubmissionId.in_(checkpoint_submission_ids)
            ).all()
        }

    for item_id, rows in progress.items():
        submission = latest.get(item_id)
        rows[str(TEST_USER_ID)] = analytics_student_row_payload(
            virtual_user, "", "", class_id,
            submission=submission,
            attempts=attempts[item_id],
            grade=grades.get(int(submission.Id), 0) if submission is not None else 0,
        )
    return progress


# Projects HTTP endpoints for analytics.


@analytics_api.route('/analytics_dashboard', methods=["GET"])
@jwt_required()
@inject
def analytics_dashboard(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Handle analytics dashboard for this component.

    HTTP: GET /api/analytics-dashboard/analytics_dashboard.

    Inputs: project_repo."""
    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    class_id = parse_int(request.args.get("class_id") or request.args.get("id"), 0)
    # Return the response below when this validation or access check matches.
    if class_id <= 0:
        return make_response({"message": "Missing class_id"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(class_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    modules = list(project_repo.get_modules_by_class_id(class_id) or [])
    projects = list(project_repo.get_projects_by_class_id(class_id) or [])
    project_ids = [
        int(project.Id) for project in projects if int(getattr(project, "Id", 0) or 0) > 0
    ]

    checkpoints_by_project_id = analytics_checkpoint_payloads(
        project_ids,
        project_repo,
    )

    module_by_id = {
        int(module.Id): module for module in modules if int(getattr(module, "Id", 0) or 0) > 0
    }

    project_by_module_id = {
        int(getattr(project, "ModuleId", 0) or 0): project
        for project in projects
        if int(getattr(project, "ModuleId", 0) or 0) > 0
    }

    progress = analytics_dashboard_progress(
        class_id, project_ids, checkpoints_by_project_id,
    )
    if str(request.args.get("include_test_user", "")).strip().lower() in ("1", "true", "yes", "on"):
        progress = analytics_test_user_progress(progress, class_id, project_ids)

    return jsonify(
        {
            "modules": [
                {
                    "Id": int(module.Id),
                    "ClassId": int(module.ClassId),
                    "Name": str(module.Name or ""),
                    "Start": module.Start.strftime("%x %X") if module.Start else "",
                    "End": module.End.strftime("%x %X") if module.End else "",
                    "MainProjectId": (
                        int(project_by_module_id[int(module.Id)].Id)
                        if int(module.Id) in project_by_module_id
                        else None
                    ),
                    "MainProjectName": (
                        str(project_by_module_id[int(module.Id)].Name or "")
                        if int(module.Id) in project_by_module_id
                        else ""
                    ),
                }
                for module in modules
            ],
            "projects": [
                {
                    "Id": int(project.Id),
                    "Name": str(project.Name or ""),
                    "Start": (
                        module_by_id[int(getattr(project, "ModuleId", 0) or 0)].Start.strftime(
                            "%x %X"
                        )
                        if int(getattr(project, "ModuleId", 0) or 0) in module_by_id
                        and module_by_id[int(getattr(project, "ModuleId", 0) or 0)].Start
                        else ""
                    ),
                    "End": (
                        module_by_id[int(getattr(project, "ModuleId", 0) or 0)].End.strftime(
                            "%x %X"
                        )
                        if int(getattr(project, "ModuleId", 0) or 0) in module_by_id
                        and module_by_id[int(getattr(project, "ModuleId", 0) or 0)].End
                        else ""
                    ),
                    "ModuleId": getattr(project, "ModuleId", None),
                }
                for project in projects
            ],
            "checkpointsByProjectId": checkpoints_by_project_id,
            "submissionsByItemId": progress,
            "hiddenModulesByStudentId": project_repo.get_hidden_module_ids_by_student_for_class(
                class_id
            ),
        }
    )
