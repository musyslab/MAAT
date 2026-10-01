"""Run bounded Python previews for the in-browser assignment editor.

Resolve the selected assignment and verify class access before accepting source
and standard input. Enforce source, input, output, and execution-time limits;
return captured execution results to the editor. Share language and upload-scope
rules with the submission upload feature.

Endpoints use /api/python_ide/<handler_name>."""

from http import HTTPStatus
from src.repositories.assignment_repository import AssignmentRepository
from src.upload import current_user_is_enrolled_in_class
from src.upload import user_can_access_class_id
from src.upload import PYTHON_IDE_MAX_OUTPUT_BYTES
from src.upload import normalize_grader_language
from dependency_injector.wiring import Provide
from dependency_injector.wiring import inject
from flask import make_response
from flask import request
from flask.json import jsonify
from flask_jwt_extended import jwt_required
from src.core.blueprints import python_ide_api
from src.core.container import Container
from src.upload import parse_int
from src.upload import PYTHON_IDE_MAX_SOURCE_BYTES
from src.upload import PYTHON_IDE_MAX_STDIN_BYTES
from src.upload import PYTHON_IDE_TIMEOUT_SECONDS
from src.upload import safe_upload_filename
import json
import os
import subprocess
import sys
import tempfile


# Resolve editor assignments and execute bounded Python preview runs.


# Resolve editor assignment context and cap captured interpreter output.


def resolve_python_ide_assignment(
    project_repo: AssignmentRepository,
    class_id: int,
    project_id: int,
    checkpoint_id: int = 0,
):
    """Resolve python ide assignment.

    Inputs: project_repo, class_id, project_id, checkpoint_id."""
    # Handle the case where class_id <= 0 or project_id <= 0.
    if class_id <= 0 or project_id <= 0:
        return None, (
            {"message": "class_id and project_id are required"},
            HTTPStatus.BAD_REQUEST,
        )

    is_staff = user_can_access_class_id(class_id)
    # Handle the case where not is_staff and (not current_user_is_enrolled_in_class(class_id)).
    if not is_staff and not current_user_is_enrolled_in_class(class_id):
        return None, ({"message": "Access Denied"}, HTTPStatus.FORBIDDEN)

    project = project_repo.get_selected_project(project_id)
    # Handle the case where project is None or int(getattr(project, 'ClassId', 0) or 0) != class_id.
    if project is None or int(getattr(project, "ClassId", 0) or 0) != class_id:
        return None, (
            {"message": "Project does not belong to this class"},
            HTTPStatus.BAD_REQUEST,
        )

    checkpoint = None
    if checkpoint_id > 0:
        checkpoint = project_repo.get_checkpoint(checkpoint_id)

        # Handle the case where checkpoint is None.
        if checkpoint is None:
            return None, ({"message": "Checkpoint not found"}, HTTPStatus.NOT_FOUND)

        # Handle the case where int(getattr(checkpoint, 'ProjectId', 0) or 0) != project_id.
        if int(getattr(checkpoint, "ProjectId", 0) or 0) != project_id:
            return None, (
                {"message": "Checkpoint does not belong to this project"},
                HTTPStatus.BAD_REQUEST,
            )

        # Handle the case where not is_staff and (not bool(getattr(checkpoint, 'Enabled', True))).
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
        getattr(owner, "Language", None) or getattr(project, "Language", None) or ""
    )

    return {
        "project": project,
        "checkpoint": checkpoint,
        "language": normalize_grader_language(effective_language, solution_path),
    }, None


def truncate_ide_output(value: str) -> tuple[str, bool]:
    """Handle truncate ide output for this component.

    Inputs: value."""
    encoded = str(value or "").encode("utf-8")
    # Handle the case where len(encoded) <= PYTHON_IDE_MAX_OUTPUT_BYTES.
    if len(encoded) <= PYTHON_IDE_MAX_OUTPUT_BYTES:
        return str(value or ""), False

    clipped = encoded[:PYTHON_IDE_MAX_OUTPUT_BYTES].decode(
        "utf-8",
        errors="ignore",
    )
    return clipped, True


# Uploads HTTP endpoints for python ide.


@python_ide_api.route('/python_ide_context', methods=["GET"])
@jwt_required()
@inject
def python_ide_context(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """Handle python ide context for this component.

    HTTP: GET /api/python_ide/python_ide_context.

    Inputs: project_repo."""
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


@python_ide_api.route('/run_python_from_ide', methods=["POST"])
@jwt_required()
@inject
def run_python_from_ide(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """Authorize an editor run and return bounded interpreter output.

    HTTP: POST /api/python_ide/run_python_from_ide.

    Inputs: project_repo.
    Invokes the configured external runner; preserve its timeout and output handling."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return make_response({"message": "Expected a JSON object"}, HTTPStatus.BAD_REQUEST)
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

    # Return the response below when this validation or access check matches.
    if context.get("language") != "py":
        return make_response(
            {"message": "The Python IDE is only available for Python assignments."},
            HTTPStatus.BAD_REQUEST,
        )

    source = data.get("source", "")
    stdin_text = data.get("stdin", "")
    requested_filename = str(data.get("filename", "main.py") or "main.py")

    # Return the response below when this validation or access check matches.
    if not isinstance(source, str) or not source.strip():
        return make_response(
            {"message": "Python source code is required."},
            HTTPStatus.BAD_REQUEST,
        )

    # Return the response below when this validation or access check matches.
    if not isinstance(stdin_text, str):
        return make_response(
            {"message": "Program input must be text."},
            HTTPStatus.BAD_REQUEST,
        )

    # Return the response below when this validation or access check matches.
    if "\x00" in stdin_text:
        return make_response(
            {"message": "Program input contains an unsupported null character."},
            HTTPStatus.BAD_REQUEST,
        )

    # Return the response below when this validation or access check matches.
    if len(source.encode("utf-8")) > PYTHON_IDE_MAX_SOURCE_BYTES:
        return make_response(
            {"message": "The Python program is too large to run in the IDE."},
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    # Return the response below when this validation or access check matches.
    if len(stdin_text.encode("utf-8")) > PYTHON_IDE_MAX_STDIN_BYTES:
        return make_response(
            {"message": "The program input is too large."},
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    safe_filename = safe_upload_filename(requested_filename)
    # Return the response below when this validation or access check matches.
    if len(requested_filename.encode("utf-8")) > 128 or not safe_filename.lower().endswith(".py"):
        return make_response(
            {"message": ("The Python program needs a short file name ending in .py.")},
            HTTPStatus.BAD_REQUEST,
        )

    grading_script = "/tabot-files/grading-scripts/grade.py"
    # Return the response below when this validation or access check matches.
    if not os.path.isfile(grading_script):
        return make_response(
            {"message": "The Judge0 grading service is not configured."},
            HTTPStatus.SERVICE_UNAVAILABLE,
        )

    try:
        # Use temporary storage that is cleaned up automatically after this block.
        with tempfile.TemporaryDirectory(prefix="maat-python-ide-") as temp_dir:
            source_path = os.path.join(temp_dir, safe_filename)

            # Open the file for writing and close it automatically when this block finishes.
            with open(source_path, "w", encoding="utf-8", newline="\n") as source_file:
                source_file.write(source)

            # Launch the external command with the execution options below.
            process = subprocess.run(
                [
                    sys.executable,
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
    # Convert this failure into the fallback result or error response below.
    except subprocess.TimeoutExpired:
        return make_response(
            {"message": "Judge0 did not finish the program in time."},
            HTTPStatus.GATEWAY_TIMEOUT,
        )
    # Convert this failure into the fallback result or error response below.
    except OSError:
        return make_response(
            {"message": "The Judge0 grading service could not be started."},
            HTTPStatus.SERVICE_UNAVAILABLE,
        )

    # Return the response below when this validation or access check matches.
    if process.returncode != 0:
        return make_response(
            {"message": (process.stderr.strip() or "Judge0 could not run the Python program.")},
            HTTPStatus.BAD_GATEWAY,
        )

    try:
        result = json.loads(process.stdout or "{}")
    # Convert this failure into the fallback result or error response below.
    except json.JSONDecodeError:
        return make_response(
            {"message": "Judge0 returned an unreadable response."},
            HTTPStatus.BAD_GATEWAY,
        )

    if not isinstance(result, dict):
        return make_response(
            {"message": "Judge0 returned an invalid response."}, HTTPStatus.BAD_GATEWAY
        )

    response_payload = {}
    was_truncated = False

    # Process each field from ('stdout', 'stdout_transcript', 'stderr', 'compile_output').
    for field in ("stdout", "stdout_transcript", "stderr", "compile_output"):
        response_payload[field], field_truncated = truncate_ide_output(result.get(field, ""))
        was_truncated = was_truncated or field_truncated

    response_payload["truncated"] = was_truncated
    response_payload["waiting_for_input"] = bool(result.get("waiting_for_input", False))
    response_payload["execution_provider"] = "judge0"

    return jsonify(response_payload)
