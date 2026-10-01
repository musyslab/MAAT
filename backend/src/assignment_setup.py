"""Create and maintain the assignment hierarchy: modules, projects, and checkpoints.

Handle creation, editing, naming, checkpoint ordering and deletion, availability
checks, and imports of default teaching materials. Shared helpers resolve module
windows and create missing default checkpoints. Permissions, progress summaries,
and resource files are owned by the other assignment feature modules.

Endpoints use /api/assignment_setup/<handler_name>."""

import re
from src.core.models import Modules
from src.repositories.assignment_repository import AssignmentRepository
import json
import os
from src.core.container import Container
from http import HTTPStatus
from dependency_injector.wiring import Provide
from datetime import datetime
from src.core.constants import chicago_iso
from src.core.constants import chicago_now
from src.core.constants import to_chicago_datetime
from dependency_injector.wiring import inject
from flask import jsonify
from flask_jwt_extended import jwt_required
from flask import make_response
from src.core.blueprints import assignment_setup_api
from flask import request
from werkzeug.utils import secure_filename

# Assignment file paths, stable directory identities, language detection, and file readiness.


# Parse assignment identifiers without changing request fallback behavior.


def parse_int(v, default: int = 0) -> int:
    """Parse an integer request value, returning the supplied fallback on failure.

    Inputs: v, default."""
    try:
        return int(str(v).strip())
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return default


DEFAULT_CHECKPOINT_NAME_RE = re.compile(r"^(checkpoint)\s+\d+$", re.IGNORECASE)


# Resolve an assignment module and its inherited availability window.


def project_module(project):
    """Handle project module for this component.

    Inputs: project."""
    # Return an empty or negative result when this guard matches.
    if not project:
        return None
    module = getattr(project, "Module", None)
    # Handle the case where module.
    if module:
        return module
    module_id = getattr(project, "ModuleId", None)
    # Handle the case where module_id.
    if module_id:
        return Modules.query.filter(Modules.Id == int(module_id)).first()
    return None


def project_start(project):
    """Handle project start for this component.

    Inputs: project."""
    module = project_module(project)
    return getattr(module, "Start", None) if module else None


def project_end(project):
    """Handle project end for this component.

    Inputs: project."""
    module = project_module(project)
    return getattr(module, "End", None) if module else None


# Assignment progress, checkpoint setup, completion rewards, and response payloads.


# Create and name default checkpoints while preserving existing assignments.


def default_checkpoint_name(number: int) -> str:
    """Handle default checkpoint name for this component.

    Inputs: number."""
    return f"Checkpoint {int(number)}"


def should_renumber_default_checkpoint_name(name: str) -> bool:
    """Determine whether should renumber default checkpoint name.

    Inputs: name."""
    return bool(DEFAULT_CHECKPOINT_NAME_RE.match(str(name or "").strip()))


def normalize_default_checkpoint_names(project_repo: AssignmentRepository, rows):
    """Normalize default checkpoint names.

    Inputs: project_repo, rows."""
    normalized_rows = []

    # Process each (index, row) from enumerate(rows or []).
    for index, row in enumerate(rows or []):
        next_name = default_checkpoint_name(index + 1)
        current_name = str(getattr(row, "Name", "") or "").strip()

        if should_renumber_default_checkpoint_name(current_name) and current_name != next_name:
            try:
                project_repo.update_checkpoint_name(int(row.Id), next_name)
                setattr(row, "Name", next_name)
            except Exception as exc:
                print(
                    f"[checkpoint] could not normalize checkpoint name for {getattr(row, 'Id', '')}: {exc}",
                    flush=True,
                )

        normalized_rows.append(row)

    return normalized_rows


def ensure_default_checkpoint_for_project(
    project_repo: AssignmentRepository,
    project_id: int,
    *,
    context: str = "project",
) -> int | None:
    """Ensure default checkpoint for project.

    Inputs: project_repo, project_id, context."""
    project_id = parse_int(project_id, 0)
    # Return an empty or negative result when this guard matches.
    if project_id <= 0:
        return None

    try:
        existing_checkpoints = project_repo.list_checkpoints(project_id)
        # Handle the case where existing_checkpoints.
        if existing_checkpoints:
            return int(existing_checkpoints[0].Id)

        checkpoint_id = project_repo.create_checkpoint(
            project_id,
            name=default_checkpoint_name(1),
        )
        return int(checkpoint_id) if checkpoint_id else None
    # Convert this failure into the fallback result or error response below.
    except Exception as exc:
        print(
            f"[{context}] default checkpoint creation failed for project {project_id}: {exc}",
            flush=True,
        )
        return None


def ensure_default_checkpoint_for_module(
    project_repo: AssignmentRepository,
    module_id: int,
    *,
    context: str = "module",
) -> int | None:
    """Ensure default checkpoint for module.

    Inputs: project_repo, module_id, context."""
    module_id = parse_int(module_id, 0)
    # Return an empty or negative result when this guard matches.
    if module_id <= 0:
        return None

    try:
        project = project_repo.get_main_project_for_module(module_id)
    # Convert this failure into the fallback result or error response below.
    except Exception as exc:
        print(
            f"[{context}] could not load main project for module {module_id}: {exc}",
            flush=True,
        )
        return None

    if not project:
        print(
            f"[{context}] no main project exists for module {module_id}; default checkpoint was not created",
            flush=True,
        )
        return None

    return ensure_default_checkpoint_for_project(
        project_repo,
        int(project.Id),
        context=context,
    )


# Build module and assignment response payloads for the existing frontend.


def opt_int(raw) -> int | None:
    """Return an optional numeric request identifier.

    Inputs: raw."""
    s = str(raw or "").strip()
    return int(s) if s.isdigit() else None


def json_list_field(raw: str) -> list[str]:
    """
    Accepts:
      - '["a","b"]'
      - 'a'
      - '' / None
    Returns list[str] of basenames.
    """
    try:
        s = (raw or "").strip()
        # Return an empty or negative result when this guard matches.
        if not s:
            return []
        vals = json.loads(s) if s.startswith("[") else [s]
        return [os.path.basename(v) for v in (vals or []) if v]
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return []


@assignment_setup_api.route('/check_time_conflict', methods=["POST"])
@jwt_required()
@inject
def check_time_conflict(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """
    JSON body:
      {
        "project_id": <int>,     # current project id (exclude from comparison)
        "class_id": <int>,       # class scope for comparison
        "start_date": "YYYY-MM-DDTHH:MM",
        "end_date":   "YYYY-MM-DDTHH:MM"
      }
    Returns: { "conflict": bool, "conflicts": [ {id,name,start,end}, ... ] }
    """
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_class_id
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    pid = int(str(data.get("project_id", 0)) or 0)
    class_id = str(data.get("class_id", "")).strip()
    start_s = str(data.get("start_date", "")).strip()
    end_s = str(data.get("end_date", "")).strip()

    # Return the response below when this validation or access check matches.
    if not class_id or not start_s or not end_s:
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    # Stop here when the caller does not have the required access.
    if pid > 0 and not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    try:
        start_dt = to_chicago_datetime(datetime.fromisoformat(start_s))
        end_dt = to_chicago_datetime(datetime.fromisoformat(end_s))
    # Convert this failure into the fallback result or error response below.
    except ValueError:
        return make_response({"message": "Invalid datetime format"}, HTTPStatus.BAD_REQUEST)

    conflicts = []
    try:
        current_module_id = None
        if pid > 0:
            current_project = project_repo.get_selected_project(pid)
            current_module_id = (
                getattr(current_project, "ModuleId", None) if current_project else None
            )

        modules = project_repo.get_modules_by_class_id(class_id)
        # Process each module from modules.
        for module in modules:
            if current_module_id and int(getattr(module, "Id", 0) or 0) == int(current_module_id):
                continue
            p_start = getattr(module, "Start", None)
            p_end = getattr(module, "End", None)
            if not p_start or not p_end:
                continue
            # strict overlap: allows back-to-back intervals without conflict
            if (start_dt < p_end) and (p_start < end_dt):
                conflicts.append(
                    {
                        "id": getattr(module, "Id", None),
                        "name": getattr(module, "Name", ""),
                        "start": chicago_iso(p_start),
                        "end": chicago_iso(p_end),
                    }
                )
    except Exception:
        # Fail-safe: treat as no conflicts if repo call fails
        conflicts = []

    return jsonify({"conflict": bool(conflicts), "conflicts": conflicts})


@assignment_setup_api.route('/create_project', methods=["POST"])
@jwt_required()
@inject
def create_project(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Create project.

    HTTP: POST /api/assignment_setup/create_project.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_materials import ALLOWED_SOURCE_EXTS
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_materials import teacher_main_project_dir_for_new
    from src.assignment_permissions import user_can_access_class_id
    from src.assignment_materials import version_dir


    def ts_str() -> str:
        """Handle ts str for this component."""
        return chicago_now().strftime("%Y%m%d_%H%M%S")

    def safe_name(s: str) -> str:
        # normalize and remove unsafe chars; also collapse spaces
        """Handle safe name for this component.

        Inputs: s."""
        return secure_filename(s or "").replace(" ", "_")

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    # Validate solution files (multi-file)
    solution_uploads = request.files.getlist("solutionFiles")
    solution_uploads = [f for f in solution_uploads if f and f.filename]
    # Return the response below when this validation or access check matches.
    if not solution_uploads:
        return make_response({"message": "No selected solution files"}, HTTPStatus.BAD_REQUEST)
    # Return the response below when this validation or access check matches.
    if "assignmentdesc" not in request.files or not request.files["assignmentdesc"].filename:
        return make_response({"message": "No assignment description file"}, HTTPStatus.BAD_REQUEST)

    # Read form
    name = request.form.get("name", "")
    # Read this input from the incoming HTTP request.
    language = request.form.get("language", "")
    # Read this input from the incoming HTTP request.
    class_id = request.form.get("class_id", "")
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    checkpoint_enabled = True
    # Read this input from the incoming HTTP request.
    module_id = request.form.get("module_id", "").strip()

    # Return the response below when this validation or access check matches.
    if name == "" or language == "":
        return make_response("Error in form", HTTPStatus.BAD_REQUEST)

    ts = ts_str()
    module_id_int = int(module_id) if module_id.isdigit() else None
    proj_dir_path = teacher_main_project_dir_for_new(
        int(class_id),
        module_id_int,
        name,
        ts,
    )
    # Ensure the destination directory exists before writing files there.
    os.makedirs(proj_dir_path, exist_ok=True)

    # Save solution + description + additional into a timestamped version directory.
    # The module folder uses the first module creation timestamp, not the current project name.
    path = version_dir(proj_dir_path, ts)
    # Ensure the destination directory exists before writing files there.
    os.makedirs(path, exist_ok=True)
    # Process each up from solution_uploads.
    for up in solution_uploads:
        orig = safe_name(up.filename)
        ext = os.path.splitext(orig)[1].lower()
        # Return the response below when this validation or access check matches.
        if ext not in ALLOWED_SOURCE_EXTS:
            return make_response(
                {"message": f"Unsupported file type: {ext}"}, HTTPStatus.BAD_REQUEST
            )
        dst = os.path.join(path, orig)
        up.save(dst)

    ad = request.files["assignmentdesc"]
    ad_name = safe_name(ad.filename or "assignment.pdf")
    assignmentdesc_path = os.path.join(path, ad_name)
    ad.save(assignmentdesc_path)

    # Multiple additional files: save into version folder, but store only basenames in DB (short)
    add_names = []
    # Process each add_up from request.files.getlist('additionalFiles').
    for add_up in request.files.getlist("additionalFiles"):
        if add_up and add_up.filename:
            orig_name = safe_name(add_up.filename)
            dst = os.path.join(path, orig_name)
            add_up.save(dst)
            add_names.append(orig_name)

    selected_path = path
    new_project_id = project_repo.create_project(
        name,
        language,
        class_id,
        selected_path,
        assignmentdesc_path,
        json.dumps(add_names),
        checkpoint_enabled,
        module_id_int,
    )

    try:
        new_project_id_int = int(new_project_id)
    except Exception:
        new_project_id_int = 0

    # Automatically create the first checkpoint for every newly-created project.
    # The admin detail page will show this alongside the main project.
    ensure_default_checkpoint_for_project(
        project_repo,
        new_project_id_int,
        context="create_project",
    )

    return make_response(str(new_project_id), HTTPStatus.OK)


@assignment_setup_api.route('/edit_project', methods=["POST"])
@jwt_required()
@inject
def edit_project(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Handle edit project for this component.

    HTTP: POST /api/assignment_setup/edit_project.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_materials import ALLOWED_SOURCE_EXTS
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_materials import pick_latest_version_dir
    from src.assignment_materials import seed_version_dir
    from src.assignment_materials import teacher_main_project_dir
    from src.assignment_permissions import user_can_access_project_id
    from src.assignment_materials import version_dir

    # Import at call time because the two features share helper functions.
    from src.assignment_materials import recompute_expected_outputs


    def ts_str() -> str:
        """Handle ts str for this component."""
        return chicago_now().strftime("%Y%m%d_%H%M%S")

    def safe_name(s: str) -> str:
        """Handle safe name for this component.

        Inputs: s."""
        return secure_filename(s or "").replace(" ", "_")

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    # Read this input from the incoming HTTP request.
    pid_str = request.form.get("id", "").strip()
    # Return the response below when this validation or access check matches.
    if not pid_str.isdigit():
        return make_response({"message": "Invalid or missing project id"}, HTTPStatus.BAD_REQUEST)
    pid = int(pid_str)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Read this input from the incoming HTTP request.
    name = request.form.get("name", "")
    # Read this input from the incoming HTTP request.
    language = request.form.get("language", "")
    checkpoint_enabled = True
    # Read this input from the incoming HTTP request.
    module_id = request.form.get("module_id", "").strip()

    # Return the response below when this validation or access check matches.
    if name == "" or language == "":
        return make_response("Error in form", HTTPStatus.BAD_REQUEST)

    ts = ts_str()
    existing_path = project_repo.get_project_path(pid)
    existing_proj = project_repo.get_selected_project(pid)
    # Return the response below when this validation or access check matches.
    if not existing_proj:
        return make_response({"message": "Project not found"}, HTTPStatus.NOT_FOUND)

    # Preserve the original filesystem project name even if the display name changes.
    proj_dir = teacher_main_project_dir(existing_proj, timestamp_hint=ts)
    # Ensure the destination directory exists before writing files there.
    os.makedirs(proj_dir, exist_ok=True)

    # Default to existing paths if no new files are uploaded
    path = existing_path
    assignmentdesc_path = project_repo.get_project_desc_path(pid)
    add_path = getattr(existing_proj, "AdditionalFilePath", "") if existing_proj else ""

    # Determine whether we need to mint a new version directory
    solution_uploads = request.files.getlist("solutionFiles")
    solution_uploads = [f for f in solution_uploads if f and f.filename]
    solution_changed = False
    # Read this input from the incoming HTTP request.
    ad = request.files.get("assignmentdesc")
    desc_changed = bool(ad and ad.filename)
    # Read this input from the incoming HTTP request.
    remove_add = request.form.get("removeAdditionalFiles", "").strip()
    clear_add = request.form.get("clearAdditionalFiles", "").strip().lower() == "true"
    new_add_uploads = [f for f in request.files.getlist("additionalFiles") if f and f.filename]
    try:
        to_remove = json.loads(remove_add) if remove_add else []
    except Exception:
        to_remove = []
    additional_ops = bool(clear_add or to_remove or new_add_uploads)
    needs_new_version = bool(solution_uploads or desc_changed or additional_ops)

    # Seed a new version folder so history is preserved and teacher layout is consistent
    if needs_new_version and existing_path and not os.path.isdir(existing_path):
        return make_response(
            {"message": "Assignment resources must use a resource directory before editing."},
            HTTPStatus.BAD_REQUEST,
        )
    current_version_dir = existing_path if existing_path and os.path.isdir(existing_path) else None
    if needs_new_version:
        new_version = version_dir(proj_dir, ts)
        seed_version_dir(new_version, seed_from_dir=current_version_dir)
        path = new_version
        # After seeding, rewrite assignmentdesc_path into this version folder if it existed
        if assignmentdesc_path:
            bn = os.path.basename(assignmentdesc_path)
            cand = os.path.join(path, bn)
            if os.path.exists(cand):
                assignmentdesc_path = cand

    # If new solution file(s) were uploaded, replace solution sources inside the current version folder
    if solution_uploads:
        # Remove old source files only in this (new) version directory
        try:
            # Process each fn from os.listdir(path).
            for fn in os.listdir(path):
                full = os.path.join(path, fn)
                if os.path.isfile(full) and os.path.splitext(fn)[1].lower() in ALLOWED_SOURCE_EXTS:
                    # Delete the file at the selected path.
                    os.remove(full)
        # Ignore this failure and allow the surrounding operation to continue.
        except Exception:
            pass
        # Process each up from solution_uploads.
        for up in solution_uploads:
            orig = safe_name(up.filename)
            ext = os.path.splitext(orig)[1].lower()
            # Return the response below when this validation or access check matches.
            if ext not in ALLOWED_SOURCE_EXTS:
                return make_response(
                    {"message": f"Unsupported file type: {ext}"}, HTTPStatus.BAD_REQUEST
                )
            dst = os.path.join(path, orig)
            up.save(dst)
        solution_changed = True

    # If a new assignment description was uploaded, save into the version folder
    ad = request.files.get("assignmentdesc")
    if ad and ad.filename:
        ad_name = safe_name(ad.filename or "assignment.pdf")
        assignmentdesc_path = os.path.join(path, ad_name)
        ad.save(assignmentdesc_path)

    # Multiple additional files: store only basenames in DB; operate on files inside `path`.
    existing_add = getattr(existing_proj, "AdditionalFilePath", "") if existing_proj else ""
    try:
        add_names = (
            json.loads(existing_add)
            if (existing_add or "").startswith("[")
            else ([existing_add] if existing_add else [])
        )
    except Exception:
        add_names = []
    add_names = [os.path.basename(p) for p in (add_names or []) if p]
    # If we minted a new version dir, keep only names that exist in the new folder.
    if needs_new_version:
        add_names = [n for n in add_names if os.path.exists(os.path.join(path, n))]
    additional_file_changed = False
    # Remove selected files (match by basename)
    if to_remove:
        keep = []
        # Process each n from add_names.
        for n in add_names:
            if n in to_remove:
                try:
                    # Delete the file at the selected path.
                    os.remove(os.path.join(path, n))
                # Ignore this failure and allow the surrounding operation to continue.
                except Exception:
                    pass
                additional_file_changed = True
            else:
                keep.append(n)
        add_names = keep
    # Clear all
    if clear_add and add_names:
        # Process each n from add_names.
        for n in add_names:
            try:
                # Delete the file at the selected path.
                os.remove(os.path.join(path, n))
            # Ignore this failure and allow the surrounding operation to continue.
            except Exception:
                pass
        add_paths = []
        additional_file_changed = True
    # Append newly uploaded additional files
    for add_up in new_add_uploads:
        if add_up and add_up.filename:
            orig_name = safe_name(add_up.filename)
            dst = os.path.join(path, orig_name)
            add_up.save(dst)
            add_names.append(orig_name)
            additional_file_changed = True

    # Always point the project at the newest version directory when available
    latest_version = pick_latest_version_dir(proj_dir)
    if latest_version:
        path = latest_version

    project_repo.edit_project(
        name, language, pid, path, assignmentdesc_path, json.dumps(add_names), checkpoint_enabled
    )

    # Recompute testcase outputs **against the path we just wrote**, so we don't depend on
    # any cached ORM objects or delayed reads.
    try:
        # Recompute if either the solution OR the additional file changed.
        # If only the additional file changed, let recompute pick up the project's saved solution.
        if solution_changed or additional_file_changed:
            recompute_expected_outputs(
                project_repo,
                int(pid),
                solution_override_path=(path if solution_changed else None),
                language_override=language,
            )
    except Exception as e:
        # Don't block the edit on recompute failures, but surface why outputs didn't refresh.
        import traceback

        print(f"[edit_project] recompute_expected_outputs failed: {e}", flush=True)
        traceback.print_exc()

    return make_response("Project Edited", HTTPStatus.OK)


@assignment_setup_api.route('/update_project_name', methods=["POST"])
@jwt_required()
@inject
def update_project_name(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Update project name.

    HTTP: POST /api/assignment_setup/update_project_name.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    project_id = int(str(data.get("project_id", 0)) or 0)
    name = str(data.get("name", "")).strip()

    # Return the response below when this validation or access check matches.
    if project_id <= 0 or not name:
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    project = project_repo.update_project_name(project_id, name)
    # Return the response below when this validation or access check matches.
    if not project:
        return make_response({"message": "Project not found"}, HTTPStatus.NOT_FOUND)

    return jsonify({"message": "Project name updated"})


@assignment_setup_api.route('/create_module', methods=["POST"])
@jwt_required()
@inject
def create_module(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Create module.

    HTTP: POST /api/assignment_setup/create_module.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_class_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    class_id = str(data.get("class_id", "")).strip()
    start_date = str(data.get("start_date", "")).strip()
    end_date = str(data.get("end_date", "")).strip()

    # Return the response below when this validation or access check matches.
    if not name or not class_id or not start_date or not end_date:
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    try:
        module_id = project_repo.create_module(
            int(class_id),
            name,
            to_chicago_datetime(datetime.fromisoformat(start_date)),
            to_chicago_datetime(datetime.fromisoformat(end_date)),
        )
    # Convert this failure into the fallback result or error response below.
    except Exception as exc:
        return make_response({"message": f"Could not create module: {exc}"}, HTTPStatus.BAD_REQUEST)

    default_checkpoint_id = ensure_default_checkpoint_for_module(
        project_repo,
        int(module_id),
        context="create_module",
    )

    return jsonify(
        {
            "module_id": int(module_id),
            "checkpoint_id": int(default_checkpoint_id) if default_checkpoint_id else None,
        }
    )


@assignment_setup_api.route('/update_module', methods=["POST"])
@jwt_required()
@inject
def update_module(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Update module.

    HTTP: POST /api/assignment_setup/update_module.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_module_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    module_id = int(str(data.get("module_id", 0)) or 0)
    name = str(data.get("name", "")).strip()
    start_date = str(data.get("start_date", "")).strip()
    end_date = str(data.get("end_date", "")).strip()

    # Return the response below when this validation or access check matches.
    if module_id <= 0 or not name or not start_date or not end_date:
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_module_id(module_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    module = project_repo.update_module(
        module_id,
        name,
        to_chicago_datetime(datetime.fromisoformat(start_date)),
        to_chicago_datetime(datetime.fromisoformat(end_date)),
    )

    # Return the response below when this validation or access check matches.
    if not module:
        return make_response({"message": "Module not found"}, HTTPStatus.NOT_FOUND)

    return jsonify({"message": "Module updated"})


# Projects HTTP endpoints for default content.


@assignment_setup_api.route('/default_content', methods=["GET", "POST"])
@jwt_required()
@inject
def default_content(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Handle default content for this component.

    HTTP: GET, POST /api/assignment_setup/default_content.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_class_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()
    data = request.get_json(silent=True) if request.method == "POST" else None
    # Return the response below when this validation or access check matches.
    if request.method == "POST" and not isinstance(data, dict):
        return make_response({"message": "Expected a JSON object"}, HTTPStatus.BAD_REQUEST)
    class_id = parse_int(data.get("class_id") if data else request.args.get("class_id"), 0)
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(class_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    try:
        # Return the response below when this validation or access check matches.
        if request.method == "POST":
            return jsonify(project_repo.import_default_content(class_id, data.get("selected")))
        return jsonify(project_repo.default_content_options(class_id))
    # Convert this failure into the fallback result or error response below.
    except PermissionError as exc:
        return make_response({"message": str(exc)}, HTTPStatus.FORBIDDEN)
    # Convert this failure into the fallback result or error response below.
    except (ValueError, OSError) as exc:
        return make_response({"message": str(exc)}, HTTPStatus.BAD_REQUEST)
    # Convert this failure into the fallback result or error response below.
    except Exception:
        from flask import current_app

        current_app.logger.exception("Default content operation failed for class %s", class_id)
        return make_response(
            {"message": "Import failed. Refresh the catalog before retrying."},
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )


# Projects HTTP endpoints for checkpoints.


@assignment_setup_api.route('/set_checkpoints_enabled', methods=["POST"])
@jwt_required()
@inject
def set_checkpoints_enabled(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Set checkpoints enabled.

    HTTP: POST /api/assignment_setup/set_checkpoints_enabled.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    pid = parse_int(data.get("project_id", 0), 0)
    enabled = True

    # Return the response below when this validation or access check matches.
    if pid <= 0:
        return make_response({"message": "Invalid project_id"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    try:
        project_repo.set_checkpoints_enabled(pid, True)
        return jsonify({"ok": True, "enabled": True})
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return make_response({"ok": False}, HTTPStatus.INTERNAL_SERVER_ERROR)


@assignment_setup_api.route('/create_checkpoint', methods=["POST"])
@jwt_required()
@inject
def create_checkpoint(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Create checkpoint.

    HTTP: POST /api/assignment_setup/create_checkpoint.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    pid = parse_int(data.get("project_id", 0), 0)
    name = str(data.get("name", "") or "").strip()

    # Return the response below when this validation or access check matches.
    if pid <= 0:
        return make_response({"message": "Invalid project_id"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    if not name:
        try:
            next_number = len(project_repo.list_checkpoints(pid)) + 1
            name = default_checkpoint_name(next_number)
        except Exception:
            name = default_checkpoint_name(1)

    try:
        new_id = project_repo.create_checkpoint(pid, name=name)
    # Convert this failure into the fallback result or error response below.
    except Exception as exc:
        print(f"[create_checkpoint] failed for project {pid}: {exc}", flush=True)
        return make_response(
            {"message": "Could not create checkpoint"}, HTTPStatus.INTERNAL_SERVER_ERROR
        )

    # Return the response below when this validation or access check matches.
    if not new_id:
        return make_response(
            {"message": "Could not create checkpoint"}, HTTPStatus.INTERNAL_SERVER_ERROR
        )

    try:
        rows = project_repo.list_checkpoints(pid)
        ordered_ids = [int(row.Id) for row in rows]
        if ordered_ids:
            project_repo.reorder_checkpoints(pid, ordered_ids)
    except Exception as exc:
        print(
            f"[create_checkpoint] checkpoint created, but numbering refresh failed: {exc}",
            flush=True,
        )

    return jsonify({"ok": True, "checkpoint_id": int(new_id)})


@assignment_setup_api.route('/reorder_checkpoints', methods=["POST"])
@jwt_required()
@inject
def reorder_checkpoints(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Reorder checkpoints.

    HTTP: POST /api/assignment_setup/reorder_checkpoints.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    pid = parse_int(data.get("project_id", 0), 0)
    ordered_ids_raw = data.get("ordered_ids", [])

    # Return the response below when this validation or access check matches.
    if pid <= 0 or not isinstance(ordered_ids_raw, list):
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    ordered_ids = [parse_int(item, 0) for item in ordered_ids_raw]
    ordered_ids = [item for item in ordered_ids if item > 0]

    try:
        rows = project_repo.reorder_checkpoints(pid, ordered_ids)
        return jsonify(
            {
                "ok": True,
                "problems": [
                    {
                        "id": int(r.Id),
                        "number": i + 1,
                        "name": (getattr(r, "Name", "") or default_checkpoint_name(i + 1)),
                        "enabled": bool(getattr(r, "Enabled", True)),
                    }
                    for i, r in enumerate(rows)
                ],
            }
        )
    # Convert this failure into the fallback result or error response below.
    except Exception as exc:
        print(exc, flush=True)
        return make_response(
            {"message": "Could not reorder checkpoints"}, HTTPStatus.INTERNAL_SERVER_ERROR
        )


@assignment_setup_api.route('/delete_checkpoint', methods=["POST"])
@jwt_required()
@inject
def delete_checkpoint(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Delete checkpoint.

    HTTP: POST /api/assignment_setup/delete_checkpoint.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_checkpoint_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    checkpoint_id = parse_int(data.get("checkpoint_id", 0), 0)

    # Return the response below when this validation or access check matches.
    if checkpoint_id <= 0:
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_checkpoint_id(checkpoint_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    try:
        pp = project_repo.get_checkpoint(checkpoint_id)
        # Return the response below when this validation or access check matches.
        if not pp:
            return make_response({"message": "Checkpoint not found"}, HTTPStatus.NOT_FOUND)

        project_id = int(getattr(pp, "ProjectId", 0) or 0)
        existing_rows = project_repo.list_checkpoints(project_id) if project_id > 0 else []
        # Return the response below when this validation or access check matches.
        if len(existing_rows) <= 1:
            return make_response(
                {"message": "A module must have at least one checkpoint"},
                HTTPStatus.BAD_REQUEST,
            )

        deleted = project_repo.delete_checkpoint(checkpoint_id)
        # Return the response below when this validation or access check matches.
        if not deleted:
            return make_response({"message": "Checkpoint not found"}, HTTPStatus.NOT_FOUND)

        remaining_rows = project_repo.list_checkpoints(project_id)
        remaining_ids = [int(row.Id) for row in remaining_rows]
        if remaining_ids:
            remaining_rows = project_repo.reorder_checkpoints(project_id, remaining_ids)
        return jsonify({"ok": True})
    # Convert this failure into the fallback result or error response below.
    except ValueError as exc:
        return make_response({"message": str(exc)}, HTTPStatus.BAD_REQUEST)
    # Convert this failure into the fallback result or error response below.
    except Exception as exc:
        print(exc, flush=True)
        return make_response(
            {"message": "Could not delete checkpoint"}, HTTPStatus.INTERNAL_SERVER_ERROR
        )


@assignment_setup_api.route('/update_checkpoint_name', methods=["POST"])
@jwt_required()
@inject
def update_checkpoint_name(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Update checkpoint name.

    HTTP: POST /api/assignment_setup/update_checkpoint_name.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_checkpoint_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    checkpoint_id = int(str(data.get("checkpoint_id", 0)) or 0)
    name = str(data.get("name", "")).strip()

    # Return the response below when this validation or access check matches.
    if checkpoint_id <= 0 or not name:
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_checkpoint_id(checkpoint_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    pp = project_repo.update_checkpoint_name(checkpoint_id, name)
    # Return the response below when this validation or access check matches.
    if not pp:
        return make_response({"message": "Checkpoint not found"}, HTTPStatus.NOT_FOUND)

    return jsonify({"message": "Checkpoint name updated"})


@assignment_setup_api.route('/rename_checkpoint', methods=["POST"])
@jwt_required()
@inject
def rename_checkpoint(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Rename checkpoint.

    HTTP: POST /api/assignment_setup/rename_checkpoint.

    Inputs: project_repo.
    Database changes are committed at the explicit transaction boundaries below."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_checkpoint_id
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    try:
        pid = int(str(data.get("project_id", 0)) or 0)
    except ValueError:
        pid = 0
    try:
        ppid = int(str(data.get("checkpoint_id", 0)) or 0)
    except ValueError:
        ppid = 0
    name = str(data.get("name", "") or "").strip()

    # Return the response below when this validation or access check matches.
    if pid <= 0 or ppid <= 0 or not name:
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    # Stop here when the caller does not have the required access.
    if not user_can_access_checkpoint_id(ppid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    pp = project_repo.get_checkpoint(ppid)
    # Return the response below when this validation or access check matches.
    if not pp or int(getattr(pp, "ProjectId", 0) or 0) != int(pid):
        return make_response({"message": "Checkpoint not found"}, HTTPStatus.NOT_FOUND)

    pp.Name = name
    try:
        from src.core.database import db

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return make_response(
            {"message": "Failed to rename checkpoint"}, HTTPStatus.INTERNAL_SERVER_ERROR
        )

    return jsonify({"ok": True, "name": name})
