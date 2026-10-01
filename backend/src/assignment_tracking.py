"""Build assignment overviews and track student completion and grading.

Serve project, module, and checkpoint listings with submission counts, completion
state, rewards, and skip information. Handle checkpoint skips, submission history,
staff grading, and submission-account unlocks. Payload helpers combine repository
results with access rules and assignment availability information.

Endpoints use /api/assignment_tracking/<handler_name>."""

from src.core.models import StudentCheckpointSkips
from src.core.models import StudentCooldownSkips
from src.core.models import StudentStarAwards
from src.core.database import db
from sqlalchemy import func
from src.repositories.assignment_repository import AssignmentRepository
from src.core.models import Submissions
from flask_jwt_extended import current_user
from src.repositories.submission_repository import SubmissionRepository
import os
from src.core.container import Container
from dependency_injector.wiring import Provide
from dependency_injector.wiring import inject
import json
from flask import jsonify
from flask_jwt_extended import jwt_required
from src.core.blueprints import assignment_tracking_api
from http import HTTPStatus
from flask import make_response
from flask import request
from src.core.models import Checkpoints
from src.core.models import Classes
from src.core.models import Modules
from src.core.models import Projects
from collections import defaultdict
from src.repositories.class_repository import ClassRepository
from src.repositories.user_repository import UserRepository
from src.core.models import Users
from src.core.constants import chicago_iso
from src.core.constants import chicago_now

CHECKPOINT_COMPLETION_STARS = 1


MAIN_PROJECT_COMPLETION_STARS = 3


EARLY_START_MULTIPLIER = 2


CHECKPOINT_SKIP_COST_STARS = 6


CHECKPOINT_SUBMISSION_COOLDOWN_SKIP_COST_STARS = 1


MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS = 2


# Read project-level completion awards, skipped checkpoints, and star balances.


def get_star_balance(user_id: int, class_id: int) -> int:
    """Use the shared balance calculation, including testcase input purchases."""
    from src.submissions import get_star_balance as submission_star_balance

    return submission_star_balance(user_id, class_id)


def incentive_summary(user_id: int, class_id: int) -> dict:
    """Handle incentive summary for this component.

    Inputs: user_id, class_id."""
    balance = get_star_balance(user_id, class_id)
    return {
        "stars": balance,
        "star_balance": balance,
        "checkpoint_completion_stars": CHECKPOINT_COMPLETION_STARS,
        "main_project_completion_stars": MAIN_PROJECT_COMPLETION_STARS,
        "early_start_multiplier": EARLY_START_MULTIPLIER,
        "checkpoint_skip_cost": CHECKPOINT_SKIP_COST_STARS,
        "checkpoint_cooldown_skip_cost": CHECKPOINT_SUBMISSION_COOLDOWN_SKIP_COST_STARS,
        "main_project_cooldown_skip_cost": MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS,
        "cooldown_skip_cost": MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS,
    }


def skipped_checkpoint_ids_for_project(user_id: int, project_id: int) -> set[int]:
    """Handle skipped checkpoint ids for project for this component.

    Inputs: user_id, project_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    # Execute the database lookup with the filters specified below.
    rows = StudentCheckpointSkips.query.filter(
        StudentCheckpointSkips.UserId == int(user_id),
        StudentCheckpointSkips.ProjectId == int(project_id),
    ).all()
    return {parse_int(getattr(row, "CheckpointId", 0), 0) for row in rows}


def checkpoint_awards_for_project(user_id: int, project_id: int) -> dict[int, dict]:
    """Handle checkpoint awards for project for this component.

    Inputs: user_id, project_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    # Execute the database lookup with the filters specified below.
    rows = StudentStarAwards.query.filter(
        StudentStarAwards.UserId == int(user_id),
        StudentStarAwards.ProjectId == int(project_id),
        StudentStarAwards.AwardType == "checkpoint_completion",
    ).all()

    return {
        parse_int(getattr(row, "CheckpointId", 0), 0): {
            "stars": parse_int(getattr(row, "AwardedStars", 0), 0),
            "base_stars": parse_int(getattr(row, "BaseAwardStars", 0), 0),
            "multiplier": parse_int(getattr(row, "Multiplier", 1), 1),
            "started_early": bool(getattr(row, "StartedEarly", False)),
        }
        for row in rows
    }


def main_award_for_project(user_id: int, project_id: int) -> dict | None:
    """Handle main award for project for this component.

    Inputs: user_id, project_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    # Execute the database lookup with the filters specified below.
    row = StudentStarAwards.query.filter(
        StudentStarAwards.UserId == int(user_id),
        StudentStarAwards.ProjectId == int(project_id),
        StudentStarAwards.CheckpointId == 0,
        StudentStarAwards.AwardType == "main_completion",
    ).first()

    # Return an empty or negative result when this guard matches.
    if row is None:
        return None

    return {
        "stars": parse_int(getattr(row, "AwardedStars", 0), 0),
        "base_stars": parse_int(getattr(row, "BaseAwardStars", 0), 0),
        "multiplier": parse_int(getattr(row, "Multiplier", 1), 1),
        "started_early": bool(getattr(row, "StartedEarly", False)),
    }


# Checkpoint progress, submission counts, and assignment completion queries.


def student_checkpoint_rows(project_repo: AssignmentRepository, project_id: int) -> list[dict]:
    """
    Student-safe checkpoint list rows.
    Returns only enabled checkpoints and includes setup/progress fields used by student pages.
    """
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import default_checkpoint_name
    from src.assignment_setup import normalize_default_checkpoint_names
    from src.assignment_setup import parse_int
    from src.assignment_materials import project_setup_status

    project_id = parse_int(project_id, 0)
    # Return an empty or negative result when this guard matches.
    if project_id <= 0:
        return []

    try:
        rows = normalize_default_checkpoint_names(
            project_repo,
            project_repo.list_checkpoints(project_id),
        )
    # Convert this failure into the fallback result or error response below.
    except Exception as exc:
        print(
            f"[student_checkpoint_rows] failed to list checkpoints for project {project_id}: {exc}",
            flush=True,
        )
        return []

    user_id = int(getattr(current_user, "Id", 0) or 0)
    skipped_checkpoint_ids = skipped_checkpoint_ids_for_project(user_id, project_id)
    checkpoint_awards = checkpoint_awards_for_project(user_id, project_id)
    passed_checkpoint_ids: set[int] = set()
    try:
        if hasattr(Submissions, "IsCheckpoint") and hasattr(Submissions, "CheckpointId"):
            # Execute the database lookup with the filters specified below.
            passed_rows = (
                db.session.query(Submissions.CheckpointId)
                .filter(
                    Submissions.Project == int(project_id),
                    Submissions.User == user_id,
                    Submissions.IsCheckpoint == True,
                    Submissions.IsPassing == True,
                    Submissions.CheckpointId.isnot(None),
                )
                .distinct()
                .all()
            )
            passed_checkpoint_ids = {
                int(row[0])
                for row in passed_rows
                if row and row[0] is not None and parse_int(row[0], 0) > 0
            }
    except Exception as exc:
        print(
            f"[student_checkpoint_rows] failed to load solved checkpoints for project {project_id}: {exc}",
            flush=True,
        )
        passed_checkpoint_ids = set()

    out = []
    # Process each (index, row) from enumerate(rows or []).
    for index, row in enumerate(rows or []):
        checkpoint_id = parse_int(getattr(row, "Id", 0), 0)
        if checkpoint_id <= 0:
            continue

        enabled = bool(getattr(row, "Enabled", True))
        if not enabled:
            continue

        status = project_setup_status(
            project_repo,
            project_id,
            checkpoint_id=checkpoint_id,
        )
        name = str(getattr(row, "Name", "") or default_checkpoint_name(index + 1))
        passed = checkpoint_id in passed_checkpoint_ids
        skipped = checkpoint_id in skipped_checkpoint_ids
        solved = passed or skipped
        award = checkpoint_awards.get(checkpoint_id, {})

        out.append(
            {
                "id": checkpoint_id,
                "checkpointId": checkpoint_id,
                "Id": checkpoint_id,
                "CheckpointId": checkpoint_id,
                "number": index + 1,
                "Number": index + 1,
                "name": name,
                "Name": name,
                "enabled": enabled,
                "Enabled": enabled,
                "solved": solved,
                "Solved": solved,
                "passed": passed,
                "Passed": passed,
                "skipped": skipped,
                "Skipped": skipped,
                "rewarded": bool(award),
                "Rewarded": bool(award),
                "rewardStars": int(award.get("stars", 0) or 0),
                "RewardStars": int(award.get("stars", 0) or 0),
                "rewardMultiplier": int(award.get("multiplier", 1) or 1),
                "RewardMultiplier": int(award.get("multiplier", 1) or 1),
                "startedEarly": bool(award.get("started_early", False)),
                "StartedEarly": bool(award.get("started_early", False)),
                "hasSolutionProgram": bool(status.get("HasSolutionProgram", False)),
                "HasSolutionProgram": bool(status.get("HasSolutionProgram", False)),
                "hasTestcases": bool(status.get("HasTestcases", False)),
                "HasTestcases": bool(status.get("HasTestcases", False)),
                "testcaseCount": int(status.get("TestcaseCount", 0) or 0),
                "TestcaseCount": int(status.get("TestcaseCount", 0) or 0),
            }
        )

    return out


def checkpoint_submission_count_map(project_id: int) -> dict[int, int]:
    """Handle checkpoint submission count map for this component.

    Inputs: project_id."""
    try:
        if hasattr(Submissions, "CheckpointId"):
            # Execute the database lookup with the filters specified below.
            rows = (
                db.session.query(
                    Submissions.CheckpointId,
                    func.count(func.distinct(Submissions.User)),
                )
                .filter(Submissions.Project == int(project_id), Submissions.IsCheckpoint == True)
                .group_by(Submissions.CheckpointId)
                .all()
            )
            return {int(ppid): int(count or 0) for ppid, count in rows if ppid is not None}
    # Ignore this failure and allow the surrounding operation to continue.
    except Exception:
        pass
    return {}


def checkpoint_unique_user_counts(project_ids: list[int]) -> dict[int, int]:
    """Handle checkpoint unique user counts for this component.

    Inputs: project_ids."""
    ids = [int(pid) for pid in (project_ids or []) if int(pid or 0) > 0]
    # Return an empty or negative result when this guard matches.
    if not ids:
        return {}

    try:
        # Execute the database lookup with the filters specified below.
        rows = (
            db.session.query(
                Submissions.Project,
                func.count(func.distinct(Submissions.User)),
            )
            .filter(Submissions.Project.in_(ids), Submissions.IsCheckpoint == True)
            .group_by(Submissions.Project)
            .all()
        )
        return {
            int(project_id): int(count or 0) for project_id, count in rows if project_id is not None
        }
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return {}


def main_completed_project_ids(project_ids: list[int]) -> set[int]:
    """Handle main completed project ids for this component.

    Inputs: project_ids."""
    ids = [int(pid) for pid in (project_ids or []) if int(pid or 0) > 0]
    # Handle the case where not ids.
    if not ids:
        return set()

    try:
        # Execute the database lookup with the filters specified below.
        rows = (
            db.session.query(Submissions.Project)
            .filter(
                Submissions.Project.in_(ids),
                Submissions.User == int(current_user.Id),
                Submissions.IsCheckpoint == False,
                Submissions.IsPassing == True,
            )
            .distinct()
            .all()
        )
        return {int(row[0]) for row in rows if row and row[0] is not None}
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return set()


def count_checkpoint_unique_users(project_id: int) -> int:
    """Count checkpoint unique users.

    Inputs: project_id."""
    try:
        # Handle the case where hasattr(Submissions, 'IsCheckpoint').
        if hasattr(Submissions, "IsCheckpoint"):
            return int(
                db.session.query(func.count(func.distinct(Submissions.User)))
                .filter(Submissions.Project == int(project_id), Submissions.IsCheckpoint == True)
                .scalar()
                or 0
            )
    # Ignore this failure and allow the surrounding operation to continue.
    except Exception:
        pass
    return 0


def module_payload(
    module,
    project_repo: AssignmentRepository,
    submission_repo: SubmissionRepository,
    total_submission_counts: dict[int, int] | None = None,
    checkpoint_total_counts: dict[int, int] | None = None,
    main_completed_project_ids: set[int] | None = None,
):
    """Handle module payload for this component.

    Inputs: module, project_repo, submission_repo, total_submission_counts, checkpoint_total_counts, main_completed_project_ids.
    """
    # Defer shared feature imports until the request or helper call.
    from src.assignment_materials import module_presentation_path

    project = project_repo.get_main_project_for_module(int(module.Id)) if module else None
    total_submissions = 0
    checkpoint_total = 0
    main_completed = False
    main_award = None

    if project:
        project_id = int(project.Id)

        try:
            if total_submission_counts is None:
                total_submission_counts = submission_repo.get_total_submission_for_all_projects()
            total_submissions = int(total_submission_counts.get(project_id, 0) or 0)
        except Exception:
            total_submissions = 0

        try:
            if checkpoint_total_counts is None:
                checkpoint_total = count_checkpoint_unique_users(project_id)
            else:
                checkpoint_total = int(checkpoint_total_counts.get(project_id, 0) or 0)
        except Exception:
            checkpoint_total = 0

        try:
            main_award = main_award_for_project(int(current_user.Id), project_id)
        except Exception:
            main_award = None

        try:
            if main_completed_project_ids is not None:
                main_completed = project_id in main_completed_project_ids
            else:
                main_completed = bool(
                    Submissions.query.filter(
                        Submissions.Project == project_id,
                        Submissions.User == int(current_user.Id),
                        Submissions.IsCheckpoint == False,
                        Submissions.IsPassing == True,
                    ).first()
                )
        except Exception:
            main_completed = False

    presentation_path = module_presentation_path(module)

    return {
        "Id": module.Id,
        "ClassId": module.ClassId,
        "Name": module.Name,
        "Start": module.Start.strftime("%x %X") if module.Start else "",
        "End": module.End.strftime("%x %X") if module.End else "",
        "MainProjectId": getattr(project, "Id", None),
        "MainProjectName": getattr(project, "Name", "") if project else "",
        "TotalSubmissions": total_submissions,
        "CheckpointTotalSubmissions": int(checkpoint_total),
        "CheckpointsEnabled": True,
        "MainCompleted": main_completed,
        "MainRewarded": bool(main_award),
        "MainRewardStars": int((main_award or {}).get("stars", 0) or 0),
        "MainRewardMultiplier": int((main_award or {}).get("multiplier", 1) or 1),
        "MainStartedEarly": bool((main_award or {}).get("started_early", False)),
        "HasPresentation": bool(presentation_path),
        "PresentationFileName": os.path.basename(presentation_path) if presentation_path else "",
    }


def project_payload(
    project,
    submission_repo: SubmissionRepository,
    project_repo: AssignmentRepository,
    total_submission_counts: dict[int, int] | None = None,
    checkpoint_total_counts: dict[int, int] | None = None,
):
    """Handle project payload for this component.

    Inputs: project, submission_repo, project_repo, total_submission_counts, checkpoint_total_counts.
    """
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import project_end
    from src.assignment_materials import project_setup_status
    from src.assignment_setup import project_start

    # Return an empty or negative result when this guard matches.
    if not project:
        return None

    project_id = int(project.Id)

    try:
        if total_submission_counts is None:
            total_submission_counts = submission_repo.get_total_submission_for_all_projects()
        total_submissions = int(total_submission_counts.get(project_id, 0) or 0)
    except Exception:
        total_submissions = 0

    try:
        if checkpoint_total_counts is None:
            checkpoint_total = count_checkpoint_unique_users(project_id)
        else:
            checkpoint_total = int(checkpoint_total_counts.get(project_id, 0) or 0)
    except Exception:
        checkpoint_total = 0

    return {
        "Id": project.Id,
        "Name": project.Name,
        "Start": project_start(project).strftime("%x %X") if project_start(project) else "",
        "End": project_end(project).strftime("%x %X") if project_end(project) else "",
        "TotalSubmissions": total_submissions,
        "CheckpointTotalSubmissions": int(checkpoint_total),
        "CheckpointsEnabled": True,
        **project_setup_status(project_repo, project_id),
    }


# Assignment creation, editing, submission history, and staff grading endpoints.


# Projects HTTP endpoints for assignments.


@assignment_tracking_api.route('/all_projects', methods=["GET"])
@jwt_required()
@inject
def all_projects(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    """Handle all projects for this component.

    HTTP: GET /api/assignment_tracking/all_projects.

    Inputs: project_repo, submission_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import filter_projects_for_current_user
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import project_end
    from src.assignment_setup import project_start

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()
    data = filter_projects_for_current_user(project_repo.get_all_projects())
    new_projects = []
    thisdic = submission_repo.get_total_submission_for_all_projects()
    # Process each proj from data.
    for proj in data:

        checkpoint_total = count_checkpoint_unique_users(int(proj.Id))

        new_projects.append(
            json.dumps(
                {
                    "Id": proj.Id,
                    "Name": proj.Name,
                    "Start": project_start(proj).strftime("%x %X") if project_start(proj) else "",
                    "End": project_end(proj).strftime("%x %X") if project_end(proj) else "",
                    "TotalSubmissions": int(thisdic.get(proj.Id, 0) or 0),
                    "CheckpointTotalSubmissions": int(checkpoint_total),
                    "CheckpointsEnabled": True,
                    "ModuleId": getattr(proj, "ModuleId", None),
                }
            )
        )
    return jsonify(new_projects)


@assignment_tracking_api.route('/get_projects_by_user', methods=["GET"])
@jwt_required()
@inject
def get_projects_by_user(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    """Return projects by user.

    HTTP: GET /api/assignment_tracking/get_projects_by_user.

    Inputs: project_repo, submission_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import project_is_hidden_for_current_student

    projects = project_repo.get_all_projects()
    student_submissions = {}
    # Process each project from projects.
    for project in projects:
        if project_is_hidden_for_current_student(project):
            continue

        subs = submission_repo.get_most_recent_submission_by_project(project.Id, [current_user.Id])
        class_name = project_repo.get_className_by_projectId(project.Id)
        if current_user.Id in subs:
            sub = subs[current_user.Id]
            student_submissions[project.Name] = [
                sub.Id,
                0,
                sub.Time.strftime("%x %X"),
                class_name,
                str(project.ClassId),
            ]
    return jsonify(student_submissions)


@assignment_tracking_api.route('/get_project', methods=["GET"])
@jwt_required()
@inject
def get_project(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Return project.

    HTTP: GET /api/assignment_tracking/get_project.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import opt_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    pid_raw = (request.args.get("id") or "").strip()
    # Return the response below when this validation or access check matches.
    if not pid_raw.isdigit():
        return make_response(json.dumps({}), HTTPStatus.OK)
    pid = int(pid_raw)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    ppid = opt_int(request.args.get("checkpoint_id", ""))
    project_info = project_repo.get_project(pid, checkpoint_id=ppid)

    return make_response(json.dumps(project_info), HTTPStatus.OK)


@assignment_tracking_api.route('/get_projects_by_class_id', methods=["GET"])
@jwt_required()
@inject
def get_projects_by_class_id(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    """Return projects by class id.

    HTTP: GET /api/assignment_tracking/get_projects_by_class_id.

    Inputs: project_repo, submission_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import parse_int
    from src.assignment_setup import project_end
    from src.assignment_setup import project_start
    from src.assignment_permissions import user_can_access_class_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()
    # Read this input from the incoming HTTP request.
    class_id = request.args.get("id")
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    data = project_repo.get_projects_by_class_id(class_id)

    new_projects = []
    thisdic = submission_repo.get_total_submission_for_all_projects()
    # Process each proj from data.
    for proj in data:

        checkpoint_total = count_checkpoint_unique_users(int(proj.Id))

        new_projects.append(
            json.dumps(
                {
                    "Id": proj.Id,
                    "Name": proj.Name,
                    "Start": project_start(proj).strftime("%x %X") if project_start(proj) else "",
                    "End": project_end(proj).strftime("%x %X") if project_end(proj) else "",
                    "TotalSubmissions": int(thisdic.get(proj.Id, 0) or 0),
                    "CheckpointTotalSubmissions": int(checkpoint_total),
                    "CheckpointsEnabled": True,
                    "ModuleId": getattr(proj, "ModuleId", None),
                }
            )
        )
    return jsonify(new_projects)


# Projects HTTP endpoints for history.


@assignment_tracking_api.route('/past_submissions', methods=["GET"])
@jwt_required()
def past_submissions():
    """
    Student past submissions grouped by project.
    Returns:
      [
        {
          projectId, projectName, classId, className, start, end,
          main: {submissionId,time,passed} | null,
          checkpoints: [{checkpointId,number,name,submissionId,time,passed}, ...]
        }, ...
      ]
    """
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import project_end
    from src.assignment_permissions import project_is_hidden_for_current_student
    from src.assignment_setup import project_start

    uid = int(getattr(current_user, "Id", 0) or 0)
    # Return the response below when this validation or access check matches.
    if uid <= 0:
        return jsonify([])

    # Projects where this student has ANY submissions (main or checkpoint)
    proj_ids = [
        int(r[0])
        for r in (
            db.session.query(Submissions.Project).filter(Submissions.User == uid).distinct().all()
        )
        if r and r[0] is not None
    ]
    # Return the response below when this validation or access check matches.
    if not proj_ids:
        return jsonify([])

    # Execute the database lookup with the filters specified below.
    projects = (
        Projects.query.outerjoin(Modules, Projects.ModuleId == Modules.Id)
        .filter(Projects.Id.in_(proj_ids))
        .order_by(Modules.Start.asc(), Projects.Id.asc())
        .all()
    )

    if not is_staff_user():
        projects = [
            project
            for project in (projects or [])
            if not project_is_hidden_for_current_student(project)
        ]
        proj_ids = [int(getattr(project, "Id", 0) or 0) for project in projects]
        # Return the response below when this validation or access check matches.
        if not proj_ids:
            return jsonify([])

    class_ids = {int(getattr(p, "ClassId", 0) or 0) for p in (projects or [])}
    class_ids.discard(0)
    class_name_by_id = {}
    if class_ids:
        # Process each c from Classes.query.filter(Classes.Id.in_(list(class_ids))).all().
        for c in Classes.query.filter(Classes.Id.in_(list(class_ids))).all():
            class_name_by_id[int(getattr(c, "Id", 0) or 0)] = str(getattr(c, "Name", "") or "")

    def iso(val):
        """Serialize stored timestamps as America/Chicago values."""
        # Return an empty or negative result when this guard matches.
        if val is None:
            return ""
        try:
            return chicago_iso(val) or ""
        # Convert this failure into the fallback result or error response below.
        except Exception:
            return str(val)

    # Most recent MAIN submission per project
    main_by_project = {}
    # Execute the database lookup with the filters specified below.
    main_rows = (
        Submissions.query.filter(
            Submissions.User == uid,
            Submissions.Project.in_(proj_ids),
            Submissions.IsCheckpoint == False,
        )
        .order_by(Submissions.Project.asc(), Submissions.Time.desc(), Submissions.Id.desc())
        .all()
    )
    # Process each s from main_rows or [].
    for s in main_rows or []:
        pid = int(getattr(s, "Project", 0) or 0)
        if pid and pid not in main_by_project:
            main_by_project[pid] = s

    # Most recent PRACTICE submission per (project, checkpoint_id)
    latest_checkpoint = {}
    pp_ids = set()
    # Execute the database lookup with the filters specified below.
    checkpoint_rows = (
        Submissions.query.filter(
            Submissions.User == uid,
            Submissions.Project.in_(proj_ids),
            Submissions.IsCheckpoint == True,
        )
        .filter(Submissions.CheckpointId.isnot(None))
        .order_by(
            Submissions.Project.asc(), Submissions.CheckpointId.asc(), Submissions.Time.desc(), Submissions.Id.desc()
        )
        .all()
    )
    # Process each s from checkpoint_rows or [].
    for s in checkpoint_rows or []:
        pid = int(getattr(s, "Project", 0) or 0)
        ppid = getattr(s, "CheckpointId", None)
        if not pid or ppid is None:
            continue
        ppid_int = int(ppid)
        key = (pid, ppid_int)
        if key not in latest_checkpoint:
            latest_checkpoint[key] = s
            pp_ids.add(ppid_int)

    # Execute the database lookup with the filters specified below.
    all_checkpoints = (
        Checkpoints.query.filter(Checkpoints.ProjectId.in_(proj_ids))
        .order_by(
            Checkpoints.ProjectId.asc(),
            Checkpoints.CheckpointNumber.asc(),
            Checkpoints.Id.asc(),
        )
        .all()
    )
    pp_map = {
        int(getattr(checkpoint, "Id", 0) or 0): checkpoint
        for checkpoint in all_checkpoints
        if int(getattr(checkpoint, "Id", 0) or 0) > 0
    }

    checkpoint_number_by_project_and_id = defaultdict(dict)
    enabled_counts = defaultdict(int)
    # Process each checkpoint from all_checkpoints.
    for checkpoint in all_checkpoints:
        if not bool(getattr(checkpoint, "Enabled", True)):
            continue
        pid = int(getattr(checkpoint, "ProjectId", 0) or 0)
        checkpoint_id = int(getattr(checkpoint, "Id", 0) or 0)
        if pid <= 0 or checkpoint_id <= 0:
            continue
        enabled_counts[pid] += 1
        checkpoint_number_by_project_and_id[pid][checkpoint_id] = enabled_counts[pid]

    checkpoints_by_project = defaultdict(list)
    # Process each ((pid, ppid), s) from latest_checkpoint.items().
    for (pid, ppid), s in latest_checkpoint.items():
        pp = pp_map.get(ppid)
        number = int(checkpoint_number_by_project_and_id.get(pid, {}).get(ppid, 0) or 0)
        name = str(getattr(pp, "Name", "") or "") if pp else ""
        if not name:
            name = f"Checkpoint {number}" if number else "Checkpoint"
        checkpoints_by_project[pid].append(
            {
                "checkpointId": int(ppid),
                "number": int(number),
                "name": name,
                "submissionId": int(getattr(s, "Id", 0) or 0),
                "time": iso(getattr(s, "Time", "")),
                "passed": bool(getattr(s, "IsPassing", False)),
            }
        )
    # Process each pid from checkpoints_by_project.
    for pid in checkpoints_by_project:
        checkpoints_by_project[pid].sort(
            key=lambda x: (int(x.get("number", 0) or 0), int(x.get("checkpointId", 0) or 0))
        )

    out = []
    # Process each p from projects or [].
    for p in projects or []:
        pid = int(getattr(p, "Id", 0) or 0)
        cid = int(getattr(p, "ClassId", 0) or 0)
        main_s = main_by_project.get(pid)
        out.append(
            {
                "projectId": pid,
                "projectName": str(getattr(p, "Name", "") or ""),
                "classId": str(cid),
                "className": class_name_by_id.get(cid, ""),
                "start": iso(project_start(p)),
                "end": iso(project_end(p)),
                "main": (
                    None
                    if not main_s
                    else {
                        "submissionId": int(getattr(main_s, "Id", 0) or 0),
                        "time": iso(getattr(main_s, "Time", "")),
                        "passed": bool(getattr(main_s, "IsPassing", False)),
                    }
                ),
                "checkpoints": checkpoints_by_project.get(pid, []),
            }
        )

    return jsonify(out)


# Projects HTTP endpoints for grading.


@assignment_tracking_api.route('/ProjectGrading', methods=["POST"])
@jwt_required()
@inject
def ProjectGrading(
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    class_repo: ClassRepository = Provide[Container.class_repo],
    user_repo: UserRepository = Provide[Container.user_repo],
):
    """Handle project grading for this component.

    HTTP: POST /api/assignment_tracking/ProjectGrading.

    Inputs: submission_repo, project_repo, class_repo, user_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Read the JSON request body using the parsing options below.
    input_json = request.get_json()
    project_id = input_json["ProjectId"]
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    user_id = input_json["userID"]
    checkpoint_raw = (input_json or {}).get("checkpoint", False)
    checkpoint = str(checkpoint_raw).strip().lower() in ("1", "true", "yes", "y", "on")

    ppid_raw = (input_json or {}).get("checkpoint_id", None)
    try:
        checkpoint_id = int(ppid_raw) if ppid_raw is not None else None
    except (TypeError, ValueError):
        checkpoint_id = None

    if checkpoint and hasattr(Submissions, "IsCheckpoint"):

        # Execute the database lookup with the filters specified below.
        q = Submissions.query.filter(
            Submissions.Project == project_id,
            Submissions.User == user_id,
            Submissions.IsCheckpoint == True,
        )
        # If grading a specific checkpoint, restrict to that checkpoint_id.
        if checkpoint_id is not None and hasattr(Submissions, "CheckpointId"):
            q = q.filter(Submissions.CheckpointId == checkpoint_id)
        # Execute the database lookup with the filters specified below.
        sub = q.order_by(Submissions.Time.desc(), Submissions.Id.desc()).first()

        submissions = {user_id: sub} if sub else {}
    else:
        submissions = submission_repo.get_most_recent_submission_by_project(project_id, [user_id])

    test_info = []
    grading_data = {}
    student_code = ""
    project_language = project_repo.get_selected_project(project_id).Language

    if user_id in submissions:
        student_code = submission_repo.read_code_file(submissions[user_id].CodeFilepath)
        student_output = submission_repo.read_output_file(submissions[user_id].OutputFilepath)
        try:
            payload = json.loads(student_output) if student_output else {}
        except Exception:
            payload = {}
        # Process each r from (payload or {}).get('results', []).
        for r in (payload or {}).get("results", []):
            test_info.append(
                {
                    "name": (r or {}).get("name", ""),
                    "passed": bool((r or {}).get("passed", False)),
                    "State": bool((r or {}).get("passed", False)),
                    "shortDiff": (r or {}).get("shortDiff", ""),
                    "longDiff": (r or {}).get("longDiff", ""),
                }
            )

        grading_data[user_id] = [student_code, test_info]
    else:
        grading_data[user_id] = ["", ""]

    return make_response(
        json.dumps({"Code": student_code, "TestResults": test_info, "Language": project_language}),
        HTTPStatus.OK,
    )


@assignment_tracking_api.route('/unlockStudentAccount', methods=["POST"])
@jwt_required()
@inject
def unlockStudentAccount(user_repo: UserRepository = Provide[Container.user_repo]):
    """Handle unlock student account for this component.

    HTTP: POST /api/assignment_tracking/unlockStudentAccount.

    Inputs: user_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_student_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response(HTTPStatus.FORBIDDEN)
    # Read the JSON request body using the parsing options below.
    input_json = request.get_json()
    user_Id = input_json["UserId"]
    # Stop here when the caller does not have the required access.
    if not user_can_access_student_id(user_Id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    user_repo.unlock_student_account(user_Id)
    message = {"message": "Success"}
    return make_response(message, HTTPStatus.OK)


# Module management, student visibility, default-material import, and presentations.


# Projects HTTP endpoints for modules.


@assignment_tracking_api.route('/get_modules_by_class_id', methods=["GET"])
@jwt_required()
@inject
def get_modules_by_class_id(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    """Return modules by class id.

    HTTP: GET /api/assignment_tracking/get_modules_by_class_id.

    Inputs: project_repo, submission_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_class_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    # Read this input from the incoming HTTP request.
    class_id = request.args.get("id")
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    modules = project_repo.get_modules_by_class_id(class_id)
    module_projects = [
        project_repo.get_main_project_for_module(int(module.Id)) for module in modules
    ]
    project_ids = [int(project.Id) for project in module_projects if project is not None]
    total_submission_counts = submission_repo.get_total_submission_for_all_projects()
    checkpoint_total_counts = checkpoint_unique_user_counts(project_ids)
    main_completed_ids = main_completed_project_ids(project_ids)

    return jsonify(
        [
            module_payload(
                module,
                project_repo,
                submission_repo,
                total_submission_counts=total_submission_counts,
                checkpoint_total_counts=checkpoint_total_counts,
                main_completed_project_ids=main_completed_ids,
            )
            for module in modules
        ]
    )


@assignment_tracking_api.route('/get_modules_by_class_id_student', methods=["GET"])
@jwt_required()
@inject
def get_modules_by_class_id_student(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    """Return modules by class id student.

    HTTP: GET /api/assignment_tracking/get_modules_by_class_id_student.

    Inputs: project_repo, submission_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import current_user_is_enrolled_in_class
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import parse_int

    class_id = parse_int(request.args.get("id"), 0)

    # Return the response below when this validation or access check matches.
    if class_id <= 0:
        return jsonify([])

    # Stop here when the caller does not have the required access.
    if not is_staff_user() and not current_user_is_enrolled_in_class(class_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    modules = list(project_repo.get_modules_by_class_id(class_id) or [])
    if not is_staff_user():
        hidden_module_ids = project_repo.get_hidden_module_ids_for_student(
            class_id,
            int(current_user.Id),
        )
        modules = [
            module
            for module in modules
            if int(getattr(module, "Id", 0) or 0) not in hidden_module_ids
        ]

    module_projects = [
        project_repo.get_main_project_for_module(int(module.Id)) for module in modules
    ]
    project_ids = [int(project.Id) for project in module_projects if project is not None]
    total_submission_counts = submission_repo.get_total_submission_for_all_projects()
    checkpoint_total_counts = checkpoint_unique_user_counts(project_ids)
    main_completed_ids = main_completed_project_ids(project_ids)

    return jsonify(
        [
            module_payload(
                module,
                project_repo,
                submission_repo,
                total_submission_counts=total_submission_counts,
                checkpoint_total_counts=checkpoint_total_counts,
                main_completed_project_ids=main_completed_ids,
            )
            for module in modules
        ]
    )


@assignment_tracking_api.route('/get_module_overview_student', methods=["GET"])
@jwt_required()
@inject
def get_module_overview_student(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    """Return module overview student.

    HTTP: GET /api/assignment_tracking/get_module_overview_student.

    Inputs: project_repo, submission_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import current_user_can_access_visible_module_id
    from src.assignment_setup import parse_int

    module_id = parse_int(request.args.get("module_id", 0), 0)
    # Return the response below when this validation or access check matches.
    if module_id <= 0:
        return make_response({"message": "Module not found"}, HTTPStatus.NOT_FOUND)

    module = project_repo.get_module(module_id)
    # Return the response below when this validation or access check matches.
    if not module:
        return make_response({"message": "Module not found"}, HTTPStatus.NOT_FOUND)
    # Stop here when the caller does not have the required access.
    if not current_user_can_access_visible_module_id(module_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    project = project_repo.get_main_project_for_module(int(module.Id))
    checkpoint_rows = student_checkpoint_rows(project_repo, int(project.Id)) if project else []

    return jsonify(
        {
            "module": module_payload(module, project_repo, submission_repo),
            "checkpoints": checkpoint_rows,
            "practiceProblems": checkpoint_rows,
            "incentives": incentive_summary(int(current_user.Id), int(module.ClassId)),
        }
    )


@assignment_tracking_api.route('/get_module_overview', methods=["GET"])
@jwt_required()
@inject
def get_module_overview(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
    submission_repo: SubmissionRepository = Provide[Container.submission_repo],
):
    """Return module overview.

    HTTP: GET /api/assignment_tracking/get_module_overview.

    Inputs: project_repo, submission_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_setup import ensure_default_checkpoint_for_project
    from src.assignment_permissions import is_staff_user
    from src.assignment_materials import project_setup_status
    from src.assignment_permissions import user_can_access_module_id
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    module_id = int(str(request.args.get("module_id", 0)) or 0)
    project_id = int(str(request.args.get("project_id", 0)) or 0)
    # Stop here when the caller does not have the required access.
    if module_id > 0 and not user_can_access_module_id(module_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    # Stop here when the caller does not have the required access.
    if project_id > 0 and not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    module = project_repo.get_module(module_id) if module_id > 0 else None
    if not module and project_id > 0:
        module = project_repo.get_module_by_project_id(project_id)

    # Return the response below when this validation or access check matches.
    if not module:
        return make_response({"message": "Module not found"}, HTTPStatus.NOT_FOUND)

    project = project_repo.get_main_project_for_module(module.Id)
    # Return the response below when this validation or access check matches.
    if not project:
        return make_response({"message": "Main project not found"}, HTTPStatus.NOT_FOUND)

    project_id = int(project.Id)
    ensure_default_checkpoint_for_project(
        project_repo,
        project_id,
        context="get_module_overview",
    )
    total_submission_counts = submission_repo.get_total_submission_for_all_projects()
    checkpoint_total_counts = checkpoint_unique_user_counts([project_id])
    main_completed_ids = main_completed_project_ids([project_id])

    checkpoint_rows = []
    try:
        problems = project_repo.list_checkpoints(project_id)
        submission_counts = checkpoint_submission_count_map(project_id)
        testcase_counts = project_repo.count_testcases_by_checkpoint(project_id)

        # Process each (idx, pp) from enumerate(problems).
        for idx, pp in enumerate(problems):
            pp_id = int(pp.Id)
            setup_status = project_setup_status(
                project_repo,
                project_id,
                checkpoint_id=pp_id,
                testcase_count=int(testcase_counts.get(pp_id, 0) or 0),
            )
            checkpoint_rows.append(
                {
                    "id": pp_id,
                    "number": idx + 1,
                    "name": str(getattr(pp, "Name", "") or f"Checkpoint {idx + 1}"),
                    "enabled": bool(getattr(pp, "Enabled", True)),
                    "submissions": int(submission_counts.get(pp_id, 0) or 0),
                    "hasSolutionProgram": bool(setup_status["HasSolutionProgram"]),
                    "hasTestcases": bool(setup_status["HasTestcases"]),
                    "testcaseCount": int(setup_status["TestcaseCount"]),
                }
            )
    except Exception:
        checkpoint_rows = []

    return jsonify(
        {
            "module": module_payload(
                module,
                project_repo,
                submission_repo,
                total_submission_counts=total_submission_counts,
                checkpoint_total_counts=checkpoint_total_counts,
                main_completed_project_ids=main_completed_ids,
            ),
            "project": project_payload(
                project,
                submission_repo,
                project_repo,
                total_submission_counts=total_submission_counts,
                checkpoint_total_counts=checkpoint_total_counts,
            ),
            "checkpoints": checkpoint_rows,
        }
    )


@assignment_tracking_api.route('/list_checkpoints', methods=["GET"])
@jwt_required()
@inject
def list_checkpoints(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """List checkpoints.

    HTTP: GET /api/assignment_tracking/list_checkpoints.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()
    pid = parse_int(request.args.get("project_id", ""), 0)
    # Return the response below when this validation or access check matches.
    if pid <= 0:
        return jsonify({"problems": []})
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    rows = project_repo.list_checkpoints(int(pid))
    return jsonify(
        {
            "problems": [
                {
                    "id": int(r.Id),
                    "number": i + 1,
                    "name": (getattr(r, "Name", "") or f"Checkpoint {i + 1}"),
                    "enabled": bool(getattr(r, "Enabled", True)),
                }
                for i, r in enumerate(rows)
            ]
        }
    )


@assignment_tracking_api.route('/list_checkpoints_student', methods=["GET"])
@jwt_required()
@inject
def list_checkpoints_student(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """
    Student-safe checkpoint list.
    Returns only enabled checkpoints. Checkpoints are always enabled at the project level.
    """
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import current_user_can_access_visible_project_id
    from src.assignment_setup import parse_int

    project_id = parse_int(request.args.get("project_id", ""), 0)
    # Return the response below when this validation or access check matches.
    if project_id <= 0:
        return jsonify({"problems": []})
    # Stop here when the caller does not have the required access.
    if not current_user_can_access_visible_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    return jsonify({"problems": student_checkpoint_rows(project_repo, project_id)})


@assignment_tracking_api.route('/skip_checkpoint', methods=["POST"])
@jwt_required()
def skip_checkpoint():
    """Handle skip checkpoint for this component.

    HTTP: POST /api/assignment_tracking/skip_checkpoint.
    Database changes are committed at the explicit transaction boundaries below."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import current_user_can_access_visible_project_id
    from src.assignment_setup import parse_int

    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)
    project_id = parse_int(data.get("project_id", 0), 0)
    checkpoint_id = parse_int(data.get("checkpoint_id", 0), 0)
    user_id = int(getattr(current_user, "Id", 0) or 0)

    # Return the response below when this validation or access check matches.
    if class_id <= 0 or project_id <= 0 or checkpoint_id <= 0:
        return make_response(
            {"message": "class_id, project_id, and checkpoint_id are required."},
            HTTPStatus.BAD_REQUEST,
        )

    # Execute the database lookup with the filters specified below.
    project = Projects.query.filter(Projects.Id == project_id).first()
    # Return the response below when this validation or access check matches.
    if project is None or parse_int(getattr(project, "ClassId", 0), 0) != class_id:
        return make_response({"message": "Project not found."}, HTTPStatus.NOT_FOUND)

    # Stop here when the caller does not have the required access.
    if not current_user_can_access_visible_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Execute the database lookup with the filters specified below.
    checkpoint = Checkpoints.query.filter(
        Checkpoints.Id == checkpoint_id,
        Checkpoints.ProjectId == project_id,
        Checkpoints.Enabled == True,
    ).first()
    # Return the response below when this validation or access check matches.
    if checkpoint is None:
        return make_response({"message": "Checkpoint not found."}, HTTPStatus.NOT_FOUND)

    checkpoint_rows = student_checkpoint_rows(AssignmentRepository(), project_id)
    target_index = next(
        (
            i
            for i, row in enumerate(checkpoint_rows)
            if parse_int(row.get("id"), 0) == checkpoint_id
        ),
        -1,
    )

    # Return the response below when this validation or access check matches.
    if target_index < 0:
        return make_response({"message": "Checkpoint is not available."}, HTTPStatus.NOT_FOUND)

    target_row = checkpoint_rows[target_index]
    # Return the response below when this validation or access check matches.
    if bool(target_row.get("solved")):
        return jsonify(
            {
                "message": "Checkpoint is already completed.",
                "checkpoints": checkpoint_rows,
                "incentives": incentive_summary(user_id, class_id),
            }
        )

    earlier_incomplete = [
        row for row in checkpoint_rows[:target_index] if not bool(row.get("solved"))
    ]
    # Return the response below when this validation or access check matches.
    if earlier_incomplete:
        return make_response(
            {"message": "Complete or skip earlier checkpoints first."}, HTTPStatus.BAD_REQUEST
        )

    # Lock the user row so simultaneous purchases cannot overspend a derived balance.
    locked_user = Users.query.filter(Users.Id == user_id).with_for_update().first()
    if locked_user is None:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        return make_response({"message": "User not found."}, HTTPStatus.NOT_FOUND)

    # Execute the database lookup with the filters specified below.
    existing_skip = StudentCheckpointSkips.query.filter(
        StudentCheckpointSkips.UserId == user_id,
        StudentCheckpointSkips.ClassId == class_id,
        StudentCheckpointSkips.ProjectId == project_id,
        StudentCheckpointSkips.CheckpointId == checkpoint_id,
    ).first()
    if existing_skip is not None:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        return jsonify(
            {
                "message": "Checkpoint already skipped.",
                "checkpoints": student_checkpoint_rows(AssignmentRepository(), project_id),
                "incentives": incentive_summary(user_id, class_id),
            }
        )

    balance = get_star_balance(user_id, class_id)
    if balance < CHECKPOINT_SKIP_COST_STARS:
        # Undo pending database changes after the operation fails.
        db.session.rollback()
        return make_response(
            {
                "message": f"You need {CHECKPOINT_SKIP_COST_STARS} stars to skip a checkpoint.",
                "stars": balance,
                "required_stars": CHECKPOINT_SKIP_COST_STARS,
            },
            HTTPStatus.BAD_REQUEST,
        )

    # Stage the new records in the current database transaction.
    db.session.add(
        StudentCheckpointSkips(
            UserId=user_id,
            ClassId=class_id,
            ProjectId=project_id,
            CheckpointId=checkpoint_id,
            SpentStars=CHECKPOINT_SKIP_COST_STARS,
            CreatedAt=chicago_now(),
        )
    )
    # Commit the pending database changes so they persist beyond this request.
    db.session.commit()

    updated_rows = student_checkpoint_rows(AssignmentRepository(), project_id)

    return jsonify(
        {
            "message": "Checkpoint skipped.",
            "checkpoints": updated_rows,
            "practiceProblems": updated_rows,
            "incentives": incentive_summary(user_id, class_id),
        }
    )


@assignment_tracking_api.route('/checkpoint_submission_counts', methods=["GET"])
@jwt_required()
def checkpoint_submission_counts():
    """
    Returns checkpoint submission counts per checkpoint_id (and total) for a project.
    Response:
      { "total": <int>, "by_problem": { "<ppid>": <count>, ... } }
    """
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    pid = parse_int(request.args.get("project_id", ""), 0)
    # Return the response below when this validation or access check matches.
    if pid <= 0:
        return jsonify({"total": 0, "by_problem": {}})
    pid = int(pid)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Return the response below when this validation or access check matches.
    if not hasattr(Submissions, "IsCheckpoint"):
        return jsonify({"total": 0, "by_problem": {}})

    total = 0
    by_problem = {}
    try:
        total = count_checkpoint_unique_users(int(pid))

        # If your Submissions model tracks which checkpoint was submitted:
        if hasattr(Submissions, "CheckpointId"):
            # Execute the database lookup with the filters specified below.
            rows = (
                db.session.query(
                    Submissions.CheckpointId, func.count(func.distinct(Submissions.User))
                )
                .filter(Submissions.Project == pid, Submissions.IsCheckpoint == True)
                .group_by(Submissions.CheckpointId)
                .all()
            )
            # Process each (ppid, cnt) from rows.
            for ppid, cnt in rows:
                if ppid is None:
                    continue
                by_problem[str(int(ppid))] = int(cnt or 0)
    except Exception:
        total = 0
        by_problem = {}

    return jsonify({"total": int(total), "by_problem": by_problem})
