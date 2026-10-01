"""Manage the files and test cases that make an assignment runnable and readable.

Resolve stable teacher/student directories and resource versions; serve and update
assignment sources, descriptions, test cases, and module presentations. Render DOCX
previews, convert office documents when the external tools are available, and
recompute expected outputs through the configured grader or subprocess fallback.
Filesystem work uses the existing assignment identities and permission checks.

Endpoints use /api/assignment_materials/<handler_name>."""

import re
from werkzeug.utils import secure_filename
from src.core.models import Classes
import os
from src.core.models import Modules
from src.core.constants import chicago_now
from src.core.database import db
from src.core.models import Projects
from src.core.models import Checkpoints
from src.repositories.assignment_repository import AssignmentRepository
from src.repositories.assignment_repository import normalize_grader_language
import shutil
from flask import request
from http import HTTPStatus
from flask import Response
from flask_jwt_extended import jwt_required
from flask import make_response
from src.core.blueprints import assignment_materials_api
from urllib.parse import quote
from flask import jsonify
from xml.etree import ElementTree
import zipfile
import posixpath
import base64
import html
import mimetypes
from io import BytesIO
import subprocess
import tempfile
import json
import importlib.util
import sys
from src.core.container import Container
from dependency_injector.wiring import Provide
from dependency_injector.wiring import inject

# Policy constants for projects.


ALLOWED_SOURCE_EXTS = {".py", ".c", ".java", ".rkt"}


ALLOWED_PRESENTATION_EXTS = {".pdf", ".ppt", ".pptx"}


PRESENTATION_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


TS_DIR_RE = re.compile(r"^\d{8}_\d{6}$")


DOCX_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


DOCX_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


DOCX_PACKAGE_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"


DOCX_DRAWING_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"


DOCX_WORDPROCESSING_DRAWING_NAMESPACE = (
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
)


DOCX_NAMESPACES = {
    "w": DOCX_NAMESPACE,
    "r": DOCX_RELATIONSHIP_NAMESPACE,
    "a": DOCX_DRAWING_NAMESPACE,
    "wp": DOCX_WORDPROCESSING_DRAWING_NAMESPACE,
}


# Stable teacher/student directories and assignment-version file paths.


def project_root() -> str:
    """Return the existing project-files mount used by the deployment."""
    return "/tabot-files/project-files"


def path_segment(value: str, fallback: str = "unnamed") -> str:
    """Convert a display value into a safe directory component with a fallback.

    Inputs: value, fallback."""
    safe = secure_filename(str(value or "").strip()).replace(" ", "_")
    return safe or fallback


def teacher_root_for_class(class_id: int) -> str:
    """Handle teacher root for class for this component.

    Inputs: class_id."""
    # Execute the database lookup with the filters specified below.
    class_item = Classes.query.filter(Classes.Id == int(class_id)).first()
    class_name = path_segment(
        getattr(class_item, "Name", "") if class_item else f"class_{class_id}", f"class_{class_id}"
    )
    school = getattr(class_item, "School", None) if class_item else None
    school_name = path_segment(getattr(school, "Name", "") if school else "school", "school")
    return os.path.join(project_root(), school_name, class_name, "teacher-files")


def student_root_for_class(class_id: int) -> str:
    """Handle student root for class for this component.

    Inputs: class_id."""
    # Execute the database lookup with the filters specified below.
    class_item = Classes.query.filter(Classes.Id == int(class_id)).first()
    class_name = path_segment(
        getattr(class_item, "Name", "") if class_item else f"class_{class_id}", f"class_{class_id}"
    )
    school = getattr(class_item, "School", None) if class_item else None
    school_name = path_segment(getattr(school, "Name", "") if school else "school", "school")
    return os.path.join(project_root(), school_name, class_name, "student-files")


def stable_module_identity(
    module: Modules | None, fallback_name: str, timestamp_hint: str | None = None
) -> tuple[str, str]:
    """Handle stable module identity for this component.

    Inputs: module, fallback_name, timestamp_hint.
    Database changes are committed at the explicit transaction boundaries below."""
    # Handle the case where module is None.
    if module is None:
        return timestamp_hint or chicago_now().strftime("%Y%m%d_%H%M%S"), path_segment(
            fallback_name, "module"
        )

    changed = False

    if not getattr(module, "FileTimestamp", None):
        module.FileTimestamp = timestamp_hint or chicago_now().strftime("%Y%m%d_%H%M%S")
        changed = True

    if not getattr(module, "FirstName", None):
        module.FirstName = getattr(module, "Name", None) or fallback_name or "module"
        changed = True

    if changed:
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    return str(module.FileTimestamp), path_segment(module.FirstName, "module")


def stable_project_first_name(project: Projects | None, fallback_name: str) -> str:
    """Handle stable project first name for this component.

    Inputs: project, fallback_name.
    Database changes are committed at the explicit transaction boundaries below."""
    # Handle the case where project is None.
    if project is None:
        return path_segment(fallback_name, "project")

    if not getattr(project, "FirstName", None):
        project.FirstName = getattr(project, "Name", None) or fallback_name or "project"
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    return path_segment(project.FirstName, "project")


def stable_checkpoint_first_name(checkpoint: Checkpoints | None, fallback_name: str) -> str:
    """Handle stable checkpoint first name for this component.

    Inputs: checkpoint, fallback_name.
    Database changes are committed at the explicit transaction boundaries below."""
    # Handle the case where checkpoint is None.
    if checkpoint is None:
        return path_segment(fallback_name, "checkpoint")

    if not getattr(checkpoint, "FirstName", None):
        checkpoint.FirstName = getattr(checkpoint, "Name", None) or fallback_name or "checkpoint"
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    return path_segment(checkpoint.FirstName, "checkpoint")


def module_folder_name(
    module: Modules | None, fallback_name: str, timestamp_hint: str | None = None
) -> str:
    """Handle module folder name for this component.

    Inputs: module, fallback_name, timestamp_hint."""
    ts, first_name = stable_module_identity(module, fallback_name, timestamp_hint)
    return f"{ts}_{first_name}"


def teacher_module_dir(module: Modules) -> str:
    """Handle teacher module dir for this component.

    Inputs: module."""
    return os.path.join(
        teacher_root_for_class(int(module.ClassId)),
        module_folder_name(module, getattr(module, "Name", "module")),
    )


def module_presentation_path(module: Modules | None) -> str | None:
    """Handle module presentation path for this component.

    Inputs: module."""
    # Return an empty or negative result when this guard matches.
    if module is None:
        return None

    timestamp = str(getattr(module, "FileTimestamp", "") or "").strip()
    first_name = str(getattr(module, "FirstName", "") or "").strip()
    # Return an empty or negative result when this guard matches.
    if not timestamp or not first_name:
        return None

    module_dir = os.path.join(
        teacher_root_for_class(int(module.ClassId)),
        f"{timestamp}_{path_segment(first_name, 'module')}",
    )
    try:
        candidates = [
            os.path.join(module_dir, name)
            for name in os.listdir(module_dir)
            if os.path.isfile(os.path.join(module_dir, name))
            and os.path.splitext(name)[1].lower() in ALLOWED_PRESENTATION_EXTS
        ]
    except OSError:
        candidates = []

    # Handle the case where not candidates.
    if not candidates:
        return AssignmentRepository().default_module_presentation(module)

    return max(candidates, key=lambda path: os.path.getmtime(path))


def teacher_main_project_dir(project: Projects, timestamp_hint: str | None = None) -> str:
    """Handle teacher main project dir for this component.

    Inputs: project, timestamp_hint."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import project_module

    module = project_module(project)
    module_folder = module_folder_name(module, getattr(project, "Name", "module"), timestamp_hint)
    project_folder = stable_project_first_name(project, getattr(project, "Name", "project"))
    return os.path.join(
        teacher_root_for_class(int(project.ClassId)), module_folder, "main", project_folder
    )


def teacher_checkpoint_project_dir(
    project: Projects, checkpoint: Checkpoints, timestamp_hint: str | None = None
) -> str:
    """Handle teacher checkpoint project dir for this component.

    Inputs: project, checkpoint, timestamp_hint."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import project_module

    module = project_module(project)
    module_folder = module_folder_name(module, getattr(project, "Name", "module"), timestamp_hint)
    checkpoint_folder = stable_checkpoint_first_name(
        checkpoint, getattr(checkpoint, "Name", "checkpoint")
    )
    return os.path.join(
        teacher_root_for_class(int(project.ClassId)), module_folder, "checkpoint", checkpoint_folder
    )


def teacher_main_project_dir_for_new(
    class_id: int, module_id: int | None, project_name: str, timestamp_hint: str
) -> str:
    """Handle teacher main project dir for new for this component.

    Inputs: class_id, module_id, project_name, timestamp_hint."""
    module = Modules.query.filter(Modules.Id == int(module_id)).first() if module_id else None
    module_folder = module_folder_name(module, project_name, timestamp_hint)
    return os.path.join(
        teacher_root_for_class(int(class_id)),
        module_folder,
        "main",
        path_segment(project_name, "project"),
    )


def is_ts_dir(name: str) -> bool:
    """Determine whether is ts dir.

    Inputs: name."""
    return bool(TS_DIR_RE.match(name or ""))


def version_dir(proj_dir_path: str, ts: str) -> str:
    """Handle version dir for this component.

    Inputs: proj_dir_path, ts."""
    return os.path.join(proj_dir_path, ts)


def pick_latest_version_dir(proj_dir_path: str) -> str | None:
    """Handle pick latest version dir for this component.

    Inputs: proj_dir_path."""
    try:
        kids = [
            d
            for d in os.listdir(proj_dir_path)
            if is_ts_dir(d) and os.path.isdir(os.path.join(proj_dir_path, d))
        ]
        # Handle the case where kids.
        if kids:
            return os.path.join(proj_dir_path, max(kids))
    # Ignore this failure and allow the surrounding operation to continue.
    except Exception:
        pass
    return None


# Check source files and assignment readiness before showing setup status.


def source_file_names(path_value: str) -> list[str]:
    """Handle source file names for this component.

    Inputs: path_value."""
    # Return an empty or negative result when this guard matches.
    if not path_value:
        return []

    try:
        if os.path.isdir(path_value):
            names = []
            # Process each filename from sorted(os.listdir(path_value)).
            for filename in sorted(os.listdir(path_value)):
                full_path = os.path.join(path_value, filename)
                if os.path.isfile(full_path):
                    _, ext = os.path.splitext(filename)
                    if ext.lower() in ALLOWED_SOURCE_EXTS:
                        names.append(filename)
            return names

        _, ext = os.path.splitext(path_value)
        return [os.path.basename(path_value)] if ext.lower() in ALLOWED_SOURCE_EXTS else []
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return []


def project_setup_status(
    project_repo: AssignmentRepository,
    project_id: int,
    checkpoint_id: int | None = None,
    testcase_count: int | None = None,
):
    """Handle project setup status for this component.

    Inputs: project_repo, project_id, checkpoint_id, testcase_count."""
    if testcase_count is None:
        try:
            testcase_count = project_repo.count_testcases(
                int(project_id),
                checkpoint_id=(int(checkpoint_id) if checkpoint_id else None),
            )
        except Exception:
            testcase_count = 0

    try:
        solution_path = project_repo.get_project_path(
            int(project_id),
            checkpoint_id=(int(checkpoint_id) if checkpoint_id else None),
        )
        has_solution_program = len(source_file_names(solution_path)) > 0
    except Exception:
        has_solution_program = False

    return {
        "HasSolutionProgram": bool(has_solution_program),
        "HasTestcases": int(testcase_count or 0) > 0,
        "TestcaseCount": int(testcase_count or 0),
    }


# Resolve and authorize the module requested by presentation endpoints.


def requested_presentation_module():
    """Handle requested presentation module for this component."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    module_id = parse_int(
        (
            request.form.get("module_id")
            if request.method == "POST"
            else request.args.get("module_id")
        ),
        0,
    )
    project_id = parse_int(
        (
            request.form.get("project_id")
            if request.method == "POST"
            else request.args.get("project_id")
        ),
        0,
    )

    module = Modules.query.filter(Modules.Id == module_id).first() if module_id > 0 else None
    if module is None and project_id > 0:
        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == project_id).first()
        resolved_module_id = parse_int(getattr(project, "ModuleId", 0) if project else 0, 0)
        module = (
            Modules.query.filter(Modules.Id == resolved_module_id).first()
            if resolved_module_id > 0
            else None
        )

    return module


# Projects HTTP endpoints for presentations.


@assignment_materials_api.route('/get_module_presentation', methods=["GET"])
@jwt_required()
def get_module_presentation():
    """Return module presentation.

    HTTP: GET /api/assignment_materials/get_module_presentation."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import current_user_can_access_visible_module_id
    from src.assignment_permissions import parse_bool

    module = requested_presentation_module()
    # Return the response below when this validation or access check matches.
    if module is None:
        return make_response({"message": "Module not found"}, HTTPStatus.NOT_FOUND)
    # Stop here when the caller does not have the required access.
    if not current_user_can_access_visible_module_id(int(module.Id)):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    presentation_path = module_presentation_path(module)
    # Return the response below when this validation or access check matches.
    if not presentation_path:
        return make_response(
            {"message": "No presentation has been saved for this module."},
            HTTPStatus.NOT_FOUND,
        )

    # Open the file for reading and close it automatically when this block finishes.
    with open(presentation_path, "rb") as presentation_file:
        data = presentation_file.read()

    filename = os.path.basename(presentation_path)
    ext = os.path.splitext(filename)[1].lower()
    preview = parse_bool(request.args.get("preview", False))
    mime = PRESENTATION_MIME_TYPES.get(ext, "application/octet-stream")
    response_name = filename

    if preview and ext in {".ppt", ".pptx"}:
        converted_pdf = presentation_pdf_preview(presentation_path)
        if converted_pdf is not None:
            data = converted_pdf
            mime = "application/pdf"
            response_name = f"{os.path.splitext(filename)[0]}.pdf"
        else:
            data = unavailable_presentation_preview_html(filename)
            mime = "text/html; charset=utf-8"
            response_name = f"{os.path.splitext(filename)[0]}.html"

    disposition = "inline" if preview else "attachment"
    headers = {
        "Content-Disposition": f"{disposition}; filename=\"{response_name}\"; filename*=UTF-8''{quote(response_name)}",
        "Content-Length": str(len(data)),
        "X-Filename": response_name,
        "X-Original-Filename": filename,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Access-Control-Expose-Headers": (
            "Content-Disposition, Content-Type, X-Filename, X-Original-Filename"
        ),
    }

    if preview and mime.startswith("text/html"):
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )

    return Response(data, content_type=mime, headers=headers)


@assignment_materials_api.route('/save_module_presentation', methods=["POST"])
@jwt_required()
def save_module_presentation():
    """Save module presentation.

    HTTP: POST /api/assignment_materials/save_module_presentation."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_module_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    module = requested_presentation_module()
    # Return the response below when this validation or access check matches.
    if module is None:
        return make_response({"message": "Module not found"}, HTTPStatus.NOT_FOUND)
    # Stop here when the caller does not have the required access.
    if not user_can_access_module_id(int(module.Id)):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Read this input from the incoming HTTP request.
    presentation = request.files.get("presentation")
    # Return the response below when this validation or access check matches.
    if presentation is None or not presentation.filename:
        return make_response(
            {"message": "Choose a presentation to save."},
            HTTPStatus.BAD_REQUEST,
        )

    filename = secure_filename(presentation.filename)
    ext = os.path.splitext(filename)[1].lower()
    # Return the response below when this validation or access check matches.
    if not filename or ext not in ALLOWED_PRESENTATION_EXTS:
        return make_response(
            {"message": "Presentations must be PDF, PPT, or PPTX files."},
            HTTPStatus.BAD_REQUEST,
        )

    module_dir = teacher_module_dir(module)
    # Ensure the destination directory exists before writing files there.
    os.makedirs(module_dir, exist_ok=True)
    destination = os.path.join(module_dir, filename)
    temporary_path = os.path.join(
        module_dir,
        f'.presentation-{chicago_now().strftime("%Y%m%d_%H%M%S_%f")}.upload',
    )

    try:
        presentation.save(temporary_path)
        # Move the file to its new path.
        os.replace(temporary_path, destination)

        # Process each name from os.listdir(module_dir).
        for name in os.listdir(module_dir):
            existing_path = os.path.join(module_dir, name)
            if (
                existing_path != destination
                and os.path.isfile(existing_path)
                and os.path.splitext(name)[1].lower() in ALLOWED_PRESENTATION_EXTS
            ):
                # Delete the file at the selected path.
                os.remove(existing_path)
    # Convert this failure into the fallback result or error response below.
    except OSError as exc:
        try:
            if os.path.exists(temporary_path):
                # Delete the file at the selected path.
                os.remove(temporary_path)
        # Ignore this failure and allow the surrounding operation to continue.
        except OSError:
            pass
        return make_response(
            {"message": f"Could not save presentation: {exc}"},
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    return jsonify(
        {
            "message": "Module presentation saved.",
            "file_name": filename,
        }
    )


# DOCX HTML rendering and office-document/PDF preview conversion.


def docx_tag(name: str) -> str:
    """Handle docx tag for this component.

    Inputs: name."""
    return f"{{{DOCX_NAMESPACE}}}{name}"


def docx_element_text(element) -> str:
    """Extract readable text, including tabs and explicit line breaks, from OOXML."""
    pieces = []

    # Process each node from element.iter().
    for node in element.iter():
        if node.tag == docx_tag("t"):
            pieces.append(node.text or "")
        elif node.tag == docx_tag("tab"):
            pieces.append("\t")
        elif node.tag in {docx_tag("br"), docx_tag("cr")}:
            pieces.append("\n")

    return "".join(pieces)


def docx_xml_value(element, default: str = "") -> str:
    """Handle docx xml value for this component.

    Inputs: element, default."""
    # Handle the case where element is None.
    if element is None:
        return default
    return str(element.get(docx_tag("val"), default) or default)


def docx_property_enabled(properties, name: str) -> bool:
    """Handle docx property enabled for this component.

    Inputs: properties, name."""
    # Return an empty or negative result when this guard matches.
    if properties is None:
        return False

    value_node = properties.find(f"w:{name}", DOCX_NAMESPACES)
    # Return an empty or negative result when this guard matches.
    if value_node is None:
        return False

    value = docx_xml_value(value_node, "true").strip().lower()
    return value not in {"0", "false", "none", "off"}


def docx_relationships(archive: zipfile.ZipFile) -> dict[str, dict[str, str | bool]]:
    """Handle docx relationships for this component.

    Inputs: archive."""
    try:
        relationships_xml = archive.read("word/_rels/document.xml.rels")
    # Convert this failure into the fallback result or error response below.
    except KeyError:
        return {}

    root = ElementTree.fromstring(relationships_xml)
    relationship_tag = f"{{{DOCX_PACKAGE_RELATIONSHIP_NAMESPACE}}}Relationship"
    relationships = {}

    # Process each relationship from root.findall(relationship_tag).
    for relationship in root.findall(relationship_tag):
        relationship_id = str(relationship.get("Id", ""))
        if not relationship_id:
            continue
        relationships[relationship_id] = {
            "target": str(relationship.get("Target", "")),
            "external": str(relationship.get("TargetMode", "")).lower() == "external",
        }

    return relationships


def docx_paragraph_styles(archive: zipfile.ZipFile) -> dict[str, dict[str, str | int]]:
    """Handle docx paragraph styles for this component.

    Inputs: archive."""
    try:
        styles_xml = archive.read("word/styles.xml")
    # Convert this failure into the fallback result or error response below.
    except KeyError:
        return {}

    root = ElementTree.fromstring(styles_xml)
    styles = {}

    # Process each style from root.findall('w:style', DOCX_NAMESPACES).
    for style in root.findall("w:style", DOCX_NAMESPACES):
        if str(style.get(docx_tag("type"), "")) != "paragraph":
            continue

        style_id = str(style.get(docx_tag("styleId"), ""))
        if not style_id:
            continue

        name = docx_xml_value(style.find("w:name", DOCX_NAMESPACES), style_id)
        heading_level = 0
        outline_level = style.find("./w:pPr/w:outlineLvl", DOCX_NAMESPACES)

        if outline_level is not None:
            try:
                heading_level = min(6, max(1, int(docx_xml_value(outline_level, "0")) + 1))
            except ValueError:
                heading_level = 0

        if heading_level == 0:
            heading_match = re.search(r"heading\s*([1-6])", f"{style_id} {name}", re.IGNORECASE)
            if heading_match:
                heading_level = int(heading_match.group(1))

        styles[style_id] = {"name": name, "heading_level": heading_level}

    return styles


def docx_safe_archive_target(target: str) -> str | None:
    """Handle docx safe archive target for this component.

    Inputs: target."""
    raw_target = str(target or "").replace("\\", "/")
    # Return an empty or negative result when this guard matches.
    if not raw_target:
        return None

    if raw_target.startswith("/"):
        normalized = posixpath.normpath(raw_target.lstrip("/"))
    else:
        normalized = posixpath.normpath(posixpath.join("word", raw_target))

    # Return an empty or negative result when this guard matches.
    if normalized.startswith("../") or normalized == "..":
        return None
    return normalized


def docx_image_html(element, archive: zipfile.ZipFile, relationships: dict) -> str:
    """Handle docx image html for this component.

    Inputs: element, archive, relationships."""
    images = []

    # Process each blip from element.findall('.//a:blip', DOCX_NAMESPACES).
    for blip in element.findall(".//a:blip", DOCX_NAMESPACES):
        relationship_id = str(blip.get(f"{{{DOCX_RELATIONSHIP_NAMESPACE}}}embed", ""))
        relationship = relationships.get(relationship_id) or {}
        if relationship.get("external"):
            continue

        archive_target = docx_safe_archive_target(str(relationship.get("target", "")))
        if not archive_target:
            continue

        try:
            image_bytes = archive.read(archive_target)
        except KeyError:
            continue

        mime_type = mimetypes.guess_type(archive_target)[0] or "application/octet-stream"
        if not mime_type.startswith("image/"):
            continue

        drawing_properties = element.find(".//wp:docPr", DOCX_NAMESPACES)
        alt_text = "Document image"
        if drawing_properties is not None:
            alt_text = str(
                drawing_properties.get("descr")
                or drawing_properties.get("title")
                or drawing_properties.get("name")
                or alt_text
            )

        encoded = base64.b64encode(image_bytes).decode("ascii")
        images.append(
            f'<img class="docx-image" src="data:{html.escape(mime_type)};base64,{encoded}" '
            f'alt="{html.escape(alt_text, quote=True)}">'
        )

    return "".join(images)


def docx_run_html(run, archive: zipfile.ZipFile, relationships: dict) -> str:
    """Handle docx run html for this component.

    Inputs: run, archive, relationships."""
    fragments = []

    # Process each child from run.
    for child in run:
        if child.tag == docx_tag("t"):
            fragments.append(html.escape(child.text or ""))
        elif child.tag == docx_tag("tab"):
            fragments.append('<span class="docx-tab" aria-hidden="true">&#9;</span>')
        elif child.tag in {docx_tag("br"), docx_tag("cr")}:
            break_type = str(child.get(docx_tag("type"), "")).lower()
            fragments.append(
                '<span class="docx-page-break" aria-label="Page break"></span>'
                if break_type == "page"
                else "<br>"
            )
        elif child.tag in {docx_tag("drawing"), docx_tag("pict")}:
            fragments.append(docx_image_html(child, archive, relationships))

    # Return an empty or negative result when this guard matches.
    if not fragments:
        return ""

    run_properties = run.find("w:rPr", DOCX_NAMESPACES)
    styles = []

    if docx_property_enabled(run_properties, "b"):
        styles.append("font-weight:700")
    if docx_property_enabled(run_properties, "i"):
        styles.append("font-style:italic")
    if docx_property_enabled(run_properties, "u"):
        styles.append("text-decoration:underline")
    if docx_property_enabled(run_properties, "strike"):
        styles.append("text-decoration:line-through")

    color_value = docx_xml_value(
        run_properties.find("w:color", DOCX_NAMESPACES) if run_properties is not None else None,
    )
    if re.fullmatch(r"[0-9a-fA-F]{6}", color_value):
        styles.append(f"color:#{color_value}")

    size_value = docx_xml_value(
        run_properties.find("w:sz", DOCX_NAMESPACES) if run_properties is not None else None,
    )
    try:
        size_points = min(96, max(6, int(size_value) / 2))
        styles.append(f"font-size:{size_points:g}pt")
    # Ignore this failure and allow the surrounding operation to continue.
    except (TypeError, ValueError):
        pass

    highlight_value = docx_xml_value(
        run_properties.find("w:highlight", DOCX_NAMESPACES) if run_properties is not None else None,
    ).lower()
    highlight_colors = {
        "yellow": "#fef08a",
        "green": "#bbf7d0",
        "cyan": "#a5f3fc",
        "magenta": "#f5d0fe",
        "blue": "#bfdbfe",
        "red": "#fecaca",
        "darkyellow": "#fde68a",
        "lightgray": "#e5e7eb",
    }
    if highlight_value in highlight_colors:
        styles.append(f"background:{highlight_colors[highlight_value]}")

    fonts = run_properties.find("w:rFonts", DOCX_NAMESPACES) if run_properties is not None else None
    font_name = ""
    if fonts is not None:
        font_name = str(fonts.get(docx_tag("ascii"), "") or fonts.get(docx_tag("hAnsi"), ""))
        font_name = re.sub(r"[^A-Za-z0-9 _-]", "", font_name).strip()
    if font_name:
        styles.append(f"font-family:'{font_name}',sans-serif")

    content = "".join(fragments)
    style_attribute = f' style="{html.escape(";".join(styles), quote=True)}"' if styles else ""
    content = f"<span{style_attribute}>{content}</span>"

    vertical_alignment = docx_xml_value(
        run_properties.find("w:vertAlign", DOCX_NAMESPACES) if run_properties is not None else None,
    ).lower()
    # Handle the case where vertical_alignment == 'superscript'.
    if vertical_alignment == "superscript":
        return f"<sup>{content}</sup>"
    # Handle the case where vertical_alignment == 'subscript'.
    if vertical_alignment == "subscript":
        return f"<sub>{content}</sub>"
    return content


def docx_inline_html(element, archive: zipfile.ZipFile, relationships: dict) -> str:
    """Handle docx inline html for this component.

    Inputs: element, archive, relationships."""
    fragments = []

    # Process each child from element.
    for child in element:
        if child.tag == docx_tag("r"):
            fragments.append(docx_run_html(child, archive, relationships))
        elif child.tag == docx_tag("hyperlink"):
            link_content = "".join(
                docx_run_html(run, archive, relationships)
                for run in child.findall("w:r", DOCX_NAMESPACES)
            )
            relationship_id = str(child.get(f"{{{DOCX_RELATIONSHIP_NAMESPACE}}}id", ""))
            relationship = relationships.get(relationship_id) or {}
            target = str(relationship.get("target", ""))

            if relationship.get("external") and re.match(
                r"^(?:https?://|mailto:)", target, re.IGNORECASE
            ):
                fragments.append(
                    f'<a href="{html.escape(target, quote=True)}" target="_blank" '
                    f'rel="noopener noreferrer">{link_content}</a>'
                )
            else:
                fragments.append(link_content)
        elif child.tag != docx_tag("pPr"):
            fragments.extend(
                docx_run_html(run, archive, relationships)
                for run in child.findall(".//w:r", DOCX_NAMESPACES)
            )

    return "".join(fragments)


def docx_twips_to_points(value: str) -> float | None:
    """Handle docx twips to points for this component.

    Inputs: value."""
    try:
        return min(360.0, max(-360.0, int(value) / 20.0))
    # Convert this failure into the fallback result or error response below.
    except (TypeError, ValueError):
        return None


def docx_paragraph_html(
    paragraph,
    archive: zipfile.ZipFile,
    relationships: dict,
    paragraph_styles: dict,
) -> str:
    """Handle docx paragraph html for this component.

    Inputs: paragraph, archive, relationships, paragraph_styles."""
    content = docx_inline_html(paragraph, archive, relationships)
    # Handle the case where not content and (not docx_element_text(paragraph).strip()).
    if not content and not docx_element_text(paragraph).strip():
        return '<div class="docx-spacer" aria-hidden="true"></div>'

    paragraph_properties = paragraph.find("w:pPr", DOCX_NAMESPACES)
    paragraph_style_node = (
        paragraph_properties.find("w:pStyle", DOCX_NAMESPACES)
        if paragraph_properties is not None
        else None
    )
    style_id = docx_xml_value(paragraph_style_node)
    style_details = paragraph_styles.get(style_id, {})
    style_name = str(style_details.get("name", style_id)).lower()
    heading_level = int(style_details.get("heading_level", 0) or 0)

    if heading_level == 0:
        heading_match = re.search(r"heading\s*([1-6])", style_id, re.IGNORECASE)
        if heading_match:
            heading_level = int(heading_match.group(1))

    paragraph_styles_css = []
    alignment = docx_xml_value(
        (
            paragraph_properties.find("w:jc", DOCX_NAMESPACES)
            if paragraph_properties is not None
            else None
        ),
    ).lower()
    alignment_map = {
        "left": "left",
        "center": "center",
        "right": "right",
        "both": "justify",
        "distribute": "justify",
    }
    if alignment in alignment_map:
        paragraph_styles_css.append(f"text-align:{alignment_map[alignment]}")

    indentation = (
        paragraph_properties.find("w:ind", DOCX_NAMESPACES)
        if paragraph_properties is not None
        else None
    )
    if indentation is not None:
        indentation_rules = {
            "left": "margin-left",
            "start": "margin-left",
            "right": "margin-right",
            "end": "margin-right",
            "firstLine": "text-indent",
            "hanging": "text-indent",
        }
        # Process each (attribute_name, css_name) from indentation_rules.items().
        for attribute_name, css_name in indentation_rules.items():
            points = docx_twips_to_points(str(indentation.get(docx_tag(attribute_name), "")))
            if points is None:
                continue
            if attribute_name == "hanging":
                points = -abs(points)
            paragraph_styles_css.append(f"{css_name}:{points:g}pt")

    spacing = (
        paragraph_properties.find("w:spacing", DOCX_NAMESPACES)
        if paragraph_properties is not None
        else None
    )
    if spacing is not None:
        # Process each (attribute_name, css_name) from (('before', 'margin-top'), ('after', 'margin-bottom')).
        for attribute_name, css_name in (("before", "margin-top"), ("after", "margin-bottom")):
            points = docx_twips_to_points(str(spacing.get(docx_tag(attribute_name), "")))
            if points is not None:
                paragraph_styles_css.append(f"{css_name}:{max(0, points):g}pt")

    style_attribute = (
        f' style="{html.escape(";".join(paragraph_styles_css), quote=True)}"'
        if paragraph_styles_css
        else ""
    )

    # Handle the case where heading_level.
    if heading_level:
        return f"<h{heading_level}{style_attribute}>{content}</h{heading_level}>"

    # Handle the case where 'title' == style_name or style_name.endswith(' title').
    if "title" == style_name or style_name.endswith(" title"):
        return f'<h1 class="docx-title"{style_attribute}>{content}</h1>'

    is_numbered = (
        paragraph_properties is not None
        and paragraph_properties.find("w:numPr", DOCX_NAMESPACES) is not None
    )
    css_class = ' class="docx-list-item"' if is_numbered else ""
    marker = '<span class="docx-list-marker" aria-hidden="true">•</span>' if is_numbered else ""
    return f"<p{css_class}{style_attribute}>{marker}{content}</p>"


def docx_table_html(
    table,
    archive: zipfile.ZipFile,
    relationships: dict,
    paragraph_styles: dict,
) -> str:
    """Handle docx table html for this component.

    Inputs: table, archive, relationships, paragraph_styles."""
    rows = []

    # Process each row from table.findall('w:tr', DOCX_NAMESPACES).
    for row in table.findall("w:tr", DOCX_NAMESPACES):
        cells = []
        # Process each cell from row.findall('w:tc', DOCX_NAMESPACES).
        for cell in row.findall("w:tc", DOCX_NAMESPACES):
            cell_blocks = [
                docx_paragraph_html(paragraph, archive, relationships, paragraph_styles)
                for paragraph in cell.findall("w:p", DOCX_NAMESPACES)
            ]
            cell_html = "".join(cell_blocks) or html.escape(docx_element_text(cell))
            cells.append(f"<td>{cell_html}</td>")
        if cells:
            rows.append(f"<tr>{''.join(cells)}</tr>")

    return f'<div class="docx-table-wrap"><table>{"".join(rows)}</table></div>' if rows else ""


def docx_preview_html(contents: bytes, filename: str) -> bytes:
    """Create a safe, rich HTML preview when server-side PDF conversion is unavailable."""
    # Open the ZIP archive for the operations below and close it on exit.
    with zipfile.ZipFile(BytesIO(contents)) as archive:
        document_xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(document_xml)
        body = root.find("w:body", DOCX_NAMESPACES)
        relationships = docx_relationships(archive)
        paragraph_styles = docx_paragraph_styles(archive)

        def render_blocks(parent) -> list[str]:
            """Handle render blocks for this component.

            Inputs: parent."""
            rendered = []
            # Process each child from parent.
            for child in parent:
                if child.tag == docx_tag("p"):
                    rendered.append(
                        docx_paragraph_html(
                            child,
                            archive,
                            relationships,
                            paragraph_styles,
                        )
                    )
                elif child.tag == docx_tag("tbl"):
                    rendered.append(
                        docx_table_html(
                            child,
                            archive,
                            relationships,
                            paragraph_styles,
                        )
                    )
                elif child.tag == docx_tag("sdt"):
                    content = child.find("w:sdtContent", DOCX_NAMESPACES)
                    if content is not None:
                        rendered.extend(render_blocks(content))
            return rendered

        blocks = render_blocks(body) if body is not None else []

    safe_filename = html.escape(filename or "Assignment instructions")
    body_html = "\n".join(blocks) or "<p>This document does not contain previewable content.</p>"
    preview = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{safe_filename}</title>
  <style>
    :root {{ color-scheme: light; font-family: Calibri, Arial, sans-serif; }}
    * {{ box-sizing: border-box; }}
    html {{ min-height: 100%; background: #dfe6ef; }}
    body {{ margin: 0; padding: clamp(18px, 4vw, 48px) clamp(10px, 3vw, 30px) 72px; color: #172033; background: #dfe6ef; line-height: 1.5; }}
    .docx-page {{ width: min(8.5in, 100%); min-height: 11in; margin: 0 auto; padding: clamp(38px, 8vw, 0.85in) clamp(28px, 8vw, 0.9in); overflow-wrap: anywhere; background: #fff; box-shadow: 0 8px 28px rgba(15, 23, 42, 0.22); }}
    h1, h2, h3, h4, h5, h6 {{ color: #132238; line-height: 1.22; margin: 1.25em 0 0.42em; page-break-after: avoid; }}
    h1:first-child, h2:first-child, .docx-title:first-child {{ margin-top: 0; }}
    .docx-title {{ margin: 0 0 1em; font-size: 2rem; text-align: center; }}
    p {{ margin: 0.62em 0; white-space: pre-wrap; }}
    a {{ color: #1d4ed8; text-decoration: underline; text-underline-offset: 2px; }}
    sup, sub {{ font-size: 0.75em; }}
    .docx-tab {{ display: inline-block; width: 2.5rem; white-space: pre; }}
    .docx-spacer {{ height: 0.75rem; }}
    .docx-list-item {{ position: relative; padding-left: 1.5rem; }}
    .docx-list-marker {{ position: absolute; left: 0.35rem; color: #2563eb; font-weight: 800; }}
    .docx-image {{ display: block; max-width: 100%; height: auto; margin: 0.75rem auto; object-fit: contain; }}
    .docx-page-break {{ display: block; height: 1px; margin: 2rem -0.35in; border-top: 2px dashed #cbd5e1; page-break-after: always; }}
    .docx-table-wrap {{ width: 100%; margin: 1rem 0; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: auto; }}
    td {{ min-width: 6rem; padding: 0.55rem 0.65rem; border: 1px solid #94a3b8; vertical-align: top; }}
    td p {{ margin: 0.2rem 0; }}
    tr:nth-child(odd) td {{ background: #f8fafc; }}
    @media (max-width: 680px) {{
      body {{ padding: 0; }}
      .docx-page {{ min-height: 100vh; padding: 28px 22px 56px; box-shadow: none; }}
    }}
    @media print {{
      html, body {{ background: #fff; padding: 0; }}
      .docx-page {{ width: auto; min-height: 0; padding: 0; box-shadow: none; }}
    }}
  </style>
</head>
<body>
  <article class="docx-page" aria-label="{safe_filename}">
{body_html}
  </article>
</body>
</html>"""
    return preview.encode("utf-8")


def office_document_pdf_preview(path: str) -> bytes | None:
    """Use LibreOffice when available so DOC/DOCX previews preserve page layout."""
    office_binary = shutil.which("libreoffice") or shutil.which("soffice")
    # Return an empty or negative result when this guard matches.
    if not office_binary or not path or not os.path.isfile(path):
        return None

    try:
        # Use temporary storage that is cleaned up automatically after this block.
        with tempfile.TemporaryDirectory(prefix="maat-assignment-preview-") as output_dir:
            office_profile_dir = os.path.join(output_dir, "office-profile")
            # Ensure the destination directory exists before writing files there.
            os.makedirs(office_profile_dir, exist_ok=True)
            # Launch the external command with the execution options below.
            completed = subprocess.run(
                [
                    office_binary,
                    f"-env:UserInstallation=file://{quote(office_profile_dir)}",
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nofirststartwizard",
                    "--convert-to",
                    "pdf:writer_pdf_Export",
                    "--outdir",
                    output_dir,
                    path,
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=45,
            )
            # Return an empty or negative result when this guard matches.
            if completed.returncode != 0:
                return None

            converted_path = os.path.join(
                output_dir,
                f"{os.path.splitext(os.path.basename(path))[0]}.pdf",
            )
            # Return an empty or negative result when this guard matches.
            if not os.path.isfile(converted_path):
                return None

            # Open the file for reading and close it automatically when this block finishes.
            with open(converted_path, "rb") as converted_file:
                return converted_file.read()
    # Convert this failure into the fallback result or error response below.
    except (OSError, subprocess.SubprocessError):
        return None


def unavailable_office_preview_html(filename: str) -> bytes:
    """Handle unavailable office preview html for this component.

    Inputs: filename."""
    safe_filename = html.escape(filename or "assignment document")
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:Inter,system-ui,sans-serif;padding:40px;color:#334155;line-height:1.55">
  <h1 style="color:#172033">Preview unavailable</h1>
  <p><strong>{safe_filename}</strong> could not be previewed on this server.</p>
  <p>Ask your instructor for a browser-compatible copy if you need to view this document here.</p>
</body>
</html>""".encode(
        "utf-8"
    )


def presentation_pdf_preview(path: str) -> bytes | None:
    """Convert PPT/PPTX files to PDF for inline student preview when LibreOffice is available."""
    office_binary = shutil.which("libreoffice") or shutil.which("soffice")
    # Return an empty or negative result when this guard matches.
    if not office_binary or not path or not os.path.isfile(path):
        return None

    try:
        # Use temporary storage that is cleaned up automatically after this block.
        with tempfile.TemporaryDirectory(prefix="maat-presentation-preview-") as output_dir:
            office_profile_dir = os.path.join(output_dir, "office-profile")
            # Ensure the destination directory exists before writing files there.
            os.makedirs(office_profile_dir, exist_ok=True)
            # Launch the external command with the execution options below.
            completed = subprocess.run(
                [
                    office_binary,
                    f"-env:UserInstallation=file://{quote(office_profile_dir)}",
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nofirststartwizard",
                    "--convert-to",
                    "pdf:impress_pdf_Export",
                    "--outdir",
                    output_dir,
                    path,
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=45,
            )
            # Return an empty or negative result when this guard matches.
            if completed.returncode != 0:
                return None

            converted_path = os.path.join(
                output_dir,
                f"{os.path.splitext(os.path.basename(path))[0]}.pdf",
            )
            # Return an empty or negative result when this guard matches.
            if not os.path.isfile(converted_path):
                return None

            # Open the file for reading and close it automatically when this block finishes.
            with open(converted_path, "rb") as converted_file:
                return converted_file.read()
    # Convert this failure into the fallback result or error response below.
    except (OSError, subprocess.SubprocessError):
        return None


def unavailable_presentation_preview_html(filename: str) -> bytes:
    """Handle unavailable presentation preview html for this component.

    Inputs: filename."""
    safe_filename = html.escape(filename or "module presentation")
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:Inter,system-ui,sans-serif;padding:40px;color:#334155;line-height:1.55">
  <h1 style="color:#172033">Presentation preview unavailable</h1>
  <p><strong>{safe_filename}</strong> could not be converted for browser viewing on this server.</p>
  <p>The presentation is optional for this module.</p>
</body>
</html>""".encode(
        "utf-8"
    )


# Grader loading, solution execution, and expected-output recomputation.


def has_allowed_ext(path: str) -> bool:
    """Determine whether has allowed ext.

    Inputs: path."""
    return os.path.splitext(path)[1].lower() in ALLOWED_SOURCE_EXTS


def run_solution_for_input(
    solution_root: str,
    language: str,
    input_text: str,
    project_id: int,
    class_id: int,
    additional_file_path: str = "",
) -> str:
    """
    Execute code strictly via /tabot-files/grading-scripts/grade.py (ADMIN path).
    Returns stdout with normalized newlines; raises on execution failure.
    """
    # Return an empty or negative result when this guard matches.
    if not solution_root or not os.path.exists(solution_root):
        raise ValueError("Solution files are missing")
    script = "/tabot-files/grading-scripts/grade.py"

    # Expand DB-stored additional file names to absolute paths under solution_root.
    add_arg = additional_file_path or ""
    try:
        base_dir = solution_root if os.path.isdir(solution_root) else os.path.dirname(solution_root)
        raw = add_arg.strip() if isinstance(add_arg, str) else ""
        if raw.startswith("[") or raw.startswith("{"):
            lst = json.loads(raw)
        else:
            lst = [raw] if raw else []
        if isinstance(lst, dict):
            lst = lst.get("files", [])
        abs_list = []
        # Process each p from lst or [].
        for p in lst or []:
            if not p:
                continue
            if os.path.isabs(p):
                abs_list.append(p)
            else:
                abs_list.append(os.path.join(base_dir, os.path.basename(p)))
        add_arg = json.dumps(abs_list)
    except Exception:
        add_arg = additional_file_path or ""

    args = [
        sys.executable,
        script,
        "ADMIN",  # student_name triggers admin path
        normalize_grader_language(language, solution_root),  # language as grade.py expects
        input_text or "",  # goes to admin_run(user_input)
        solution_root,  # file or directory
        add_arg,
        str(project_id or 0),
        str(class_id or 0),
    ]
    try:
        # Launch the external command with the execution options below.
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.path.dirname(solution_root) if os.path.isfile(solution_root) else solution_root,
            timeout=120,
        )
    # Convert this failure into the fallback result or error response below.
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Could not run the solution") from exc
    if proc.returncode != 0:
        raise ValueError("Could not run the solution")
    return (proc.stdout or "").replace("\r\n", "\n").replace("\r", "\n")


def load_tabot_module():
    """
    Try to import tabot as a normal module first.
    If that fails, load it from /ta-bot/grading-scripts and make sure
    its directory is on sys.path so sibling imports (output, tests)
    resolve correctly.
    """
    try:
        import tabot as _t

        return _t
    # Ignore this failure and allow the surrounding operation to continue.
    except Exception:
        pass

    grading_dir = "/tabot-files/grading-scripts"
    grading_path = os.path.join(grading_dir, "grade.py")

    spec = importlib.util.spec_from_file_location("tabot-files", grading_path)
    # Reject this case with the exception below.
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load spec for {grading_path}")

    # Ensure sibling imports like `from output import *` work
    sys.path.insert(0, grading_dir)
    try:
        mod = importlib.util.module_from_spec(spec)
        sys.modules["tabot"] = mod  # let subimports see the module name
        # Optional but helps some relative-import edge cases:
        mod.__package__ = None
        spec.loader.exec_module(mod)
        return mod
    finally:
        # Avoid permanently polluting sys.path
        try:
            sys.path.remove(grading_dir)
        # Ignore this failure and allow the surrounding operation to continue.
        except ValueError:
            pass




def recompute_expected_outputs(
    project_repo,
    project_id,
    *,
    solution_override_path: str = None,
    language_override: str = None,
    checkpoint_id: int | None = None,
):
    """
    For each testcase, run the (updated) solution and persist the new output.
    """

    # Always fetch the project once (needed for class id, fallback language, etc.)
    try:
        proj_obj = project_repo.get_selected_project(int(project_id))
    except Exception:
        proj_obj = None

    if solution_override_path and os.path.exists(solution_override_path):
        solution_root = solution_override_path
        lang = (
            language_override or (getattr(proj_obj, "Language", "") if proj_obj else "")
        ).strip()
    else:
        # Handle the case where not proj_obj or not getattr(proj_obj, 'solutionpath', None).
        if not proj_obj or not getattr(proj_obj, "solutionpath", None):
            return
        solution_root = getattr(proj_obj, "solutionpath", "")
        lang = getattr(proj_obj, "Language", "")

    cases = project_repo.get_testcases(int(project_id), checkpoint_id=checkpoint_id)

    # Determine class id (needed by repo call)
    class_id = getattr(proj_obj, "ClassId", 0) if proj_obj else 0
    if not class_id:
        try:
            cname = project_repo.get_className_by_projectId(str(project_id))
            class_id = project_repo.get_class_id_by_name(cname)
        except Exception:
            class_id = 0

    if checkpoint_id:
        pp = project_repo.get_checkpoint(int(checkpoint_id))
        add_path = getattr(pp, "AdditionalFilePath", "") if pp else ""
    else:
        add_path = getattr(proj_obj, "AdditionalFilePath", "") if proj_obj else ""
    for tc_id, vals in cases.items():
        try:
            name = vals[1] if len(vals) > 1 else ""
            inp = (vals[2] if len(vals) > 2 else "").replace("\r\n", "\n").replace("\r", "\n")
            hidden = bool(vals[4]) if len(vals) > 4 else False
            sort_order = int(vals[5]) if len(vals) > 5 else None
        except Exception:
            name, inp, hidden, sort_order = "", "", False, None

        try:
            new_out = run_solution_for_input(solution_root, lang, inp, project_id, class_id, add_path)
            project_repo.add_or_update_testcase(
                int(project_id),
                int(tc_id),
                name or "",
                inp or "",
                new_out,
                int(class_id),
                hidden,
                sort_order,
                checkpoint_id=checkpoint_id,
                recompute_output=False,
            )
        except Exception:
            # continue on individual failures
            continue


# Assignment source/description files and testcase management endpoints.


# Projects HTTP endpoints for files.


@assignment_materials_api.route('/list_solution_files', methods=["GET"])
@jwt_required()
@inject
def list_solution_files(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """List solution files.

    HTTP: GET /api/assignment_materials/list_solution_files.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import opt_int
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    pid = parse_int(request.args.get("id", ""), 0)
    ppid = opt_int(request.args.get("checkpoint_id", ""))

    # Return the response below when this validation or access check matches.
    if pid <= 0:
        return make_response([], HTTPStatus.OK)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    p = project_repo.get_project_path(pid, checkpoint_id=ppid)
    # Return the response below when this validation or access check matches.
    if not p:
        return make_response([], HTTPStatus.OK)

    try:
        if os.path.isdir(p):
            names = []
            # Process each fn from sorted(os.listdir(p)).
            for fn in sorted(os.listdir(p)):
                full = os.path.join(p, fn)
                if os.path.isfile(full):
                    _, ext = os.path.splitext(fn)
                    if ext.lower() in ALLOWED_SOURCE_EXTS:
                        names.append(fn)
            return make_response(names, HTTPStatus.OK)
        return make_response([os.path.basename(p)], HTTPStatus.OK)
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return make_response([], HTTPStatus.OK)


@assignment_materials_api.route('/list_source_files', methods=["GET"])
@jwt_required()
@inject
def list_source_files(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Return list of previewable source files for a project (relative paths if a directory)."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import opt_int
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    pid = parse_int(request.args.get("project_id", ""), 0)
    # Return the response below when this validation or access check matches.
    if pid <= 0:
        return make_response({"message": "Missing project_id"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    ppid = opt_int(request.args.get("checkpoint_id", ""))
    root = project_repo.get_project_path(int(pid), checkpoint_id=ppid)

    # Return the response below when this validation or access check matches.
    if not root or not os.path.exists(root):
        return jsonify({"files": []})

    files = []
    if os.path.isdir(root):
        # Process each (base, _, fnames) from os.walk(root).
        for base, _, fnames in os.walk(root):
            # Process each fname from fnames.
            for fname in fnames:
                full = os.path.join(base, fname)
                if has_allowed_ext(full):
                    rel = os.path.relpath(full, root).replace("\\", "/")
                    files.append({"relpath": rel, "bytes": os.path.getsize(full)})
    else:
        if has_allowed_ext(root):
            files.append({"relpath": os.path.basename(root), "bytes": os.path.getsize(root)})

    return jsonify({"files": files})


@assignment_materials_api.route('/get_source_file', methods=["GET"])
@jwt_required()
@inject
def get_source_file(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Return the text content of a source file for preview."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    pid = parse_int(request.args.get("project_id", ""), 0)
    # Read this input from the incoming HTTP request.
    relpath = request.args.get("relpath", "")
    # Return the response below when this validation or access check matches.
    if pid <= 0:
        return make_response({"message": "Missing project_id"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    ppid_raw = (request.args.get("checkpoint_id", "") or "").strip()
    ppid = int(ppid_raw) if ppid_raw.isdigit() else None
    root = project_repo.get_project_path(int(pid), checkpoint_id=ppid)

    # Return the response below when this validation or access check matches.
    if not root or not os.path.exists(root):
        return make_response({"message": "Project path not found"}, HTTPStatus.NOT_FOUND)

    # Resolve full path safely using os.path only
    if os.path.isdir(root):
        candidate = os.path.normpath(os.path.join(root, relpath))
        root_abs = os.path.abspath(root)
        cand_abs = os.path.abspath(candidate)
        # Return the response below when this validation or access check matches.
        if not (cand_abs == root_abs or cand_abs.startswith(root_abs + os.sep)):
            return make_response({"message": "Invalid path"}, HTTPStatus.BAD_REQUEST)
        full = cand_abs
    else:
        # Single-file project: only that file is allowed
        if relpath and relpath != os.path.basename(root):
            return make_response(
                {"message": "Invalid path for single-file project"}, HTTPStatus.BAD_REQUEST
            )
        full = root

    # Return the response below when this validation or access check matches.
    if not os.path.exists(full):
        return make_response({"message": "File not found"}, HTTPStatus.NOT_FOUND)
    # Return the response below when this validation or access check matches.
    if not has_allowed_ext(full):
        return make_response({"message": "Unsupported file type"}, HTTPStatus.BAD_REQUEST)

    # Limit preview size to 2 MB
    if os.path.getsize(full) > 2 * 1024 * 1024:
        return make_response({"message": "File too large to preview"}, HTTPStatus.BAD_REQUEST)

    # Open the file for reading and close it automatically when this block finishes.
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    resp = make_response(text, HTTPStatus.OK)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@assignment_materials_api.route('/getAssignmentDescription', methods=["GET"])
@jwt_required()
@inject
def getAssignmentDescription(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Handle get assignment description for this component.

    HTTP: GET /api/assignment_materials/getAssignmentDescription.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import current_user_can_download_project_files
    from src.assignment_permissions import parse_bool
    from src.assignment_setup import parse_int

    # Read this input from the incoming HTTP request.
    project_id = request.args.get("project_id")
    # Stop here when the caller does not have the required access.
    if not current_user_can_download_project_files(parse_int(project_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    ppid_raw = (request.args.get("checkpoint_id", "") or "").strip()
    ppid = int(ppid_raw) if ppid_raw.isdigit() else None
    assignmentdesc_contents = project_repo.get_project_desc_file(
        int(project_id), checkpoint_id=ppid
    )
    assignmentdesc_path = project_repo.get_project_desc_path(int(project_id), checkpoint_id=ppid)

    fname = (
        os.path.basename(assignmentdesc_path) if assignmentdesc_path else "assignment_description"
    )
    ext = os.path.splitext(fname)[1].lower()
    data = assignmentdesc_contents
    preview = parse_bool(request.args.get("preview", False))

    if preview and ext in {".doc", ".docx"}:
        converted_pdf = office_document_pdf_preview(assignmentdesc_path)
        if converted_pdf is not None:
            data = converted_pdf
            mime = "application/pdf"
            response_name = f"{os.path.splitext(fname)[0]}.pdf"
        elif ext == ".docx":
            try:
                data = docx_preview_html(data, fname)
            except (KeyError, OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
                data = unavailable_office_preview_html(fname)
            mime = "text/html; charset=utf-8"
            response_name = f"{os.path.splitext(fname)[0]}.html"
        else:
            data = unavailable_office_preview_html(fname)
            mime = "text/html; charset=utf-8"
            response_name = f"{os.path.splitext(fname)[0]}.html"
    else:
        response_name = fname
        if ext == ".pdf":
            mime = "application/pdf"
        elif ext == ".docx":
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif ext == ".doc":
            mime = "application/msword"
        elif ext in {".txt", ".md"}:
            mime = "text/plain; charset=utf-8"
        else:
            mime = "application/octet-stream"

    disposition = "inline" if preview else "attachment"

    headers = {
        "Content-Disposition": f"{disposition}; filename=\"{response_name}\"; filename*=UTF-8''{quote(response_name)}",
        "Content-Length": str(len(data)),
        "X-Filename": response_name,
        "X-Original-Filename": fname,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Access-Control-Expose-Headers": "Content-Disposition, Content-Type, X-Filename, X-Original-Filename",
    }

    if preview and mime.startswith("text/html"):
        headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )

    # Preserve the original filename while allowing the preview representation to differ.
    return Response(
        data,
        content_type=mime,
        headers=headers,
    )


@assignment_materials_api.route('/edit_checkpoint_project_files', methods=["POST"])
@jwt_required()
@inject
def edit_checkpoint_project_files(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """
    Upload checkpoint solution files + assignment description (and optional additional files)
    into a dedicated checkpoint folder, and store paths on CheckpointProjects (not Projects).
    """
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import user_can_access_checkpoint_id
    from src.assignment_permissions import user_can_access_project_id


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

    pid_str = (request.form.get("project_id", "") or "").strip()
    ppid_str = (request.form.get("checkpoint_id", "") or "").strip()
    # Return the response below when this validation or access check matches.
    if not pid_str.isdigit() or not ppid_str.isdigit():
        return make_response(
            {"message": "Invalid project_id or checkpoint_id"}, HTTPStatus.BAD_REQUEST
        )
    pid = int(pid_str)
    ppid = int(ppid_str)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    # Stop here when the caller does not have the required access.
    if not user_can_access_checkpoint_id(ppid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    proj = project_repo.get_selected_project(pid)
    # Return the response below when this validation or access check matches.
    if not proj:
        return make_response({"message": "Project not found"}, HTTPStatus.NOT_FOUND)

    # Require both solution and description for checkpoint files
    solution_uploads = request.files.getlist("solutionFiles")
    solution_uploads = [f for f in solution_uploads if f and f.filename]
    # Return the response below when this validation or access check matches.
    if not solution_uploads:
        return make_response({"message": "No selected solution files"}, HTTPStatus.BAD_REQUEST)
    # Return the response below when this validation or access check matches.
    if "assignmentdesc" not in request.files or not request.files["assignmentdesc"].filename:
        return make_response({"message": "No assignment description file"}, HTTPStatus.BAD_REQUEST)

    pp = project_repo.get_checkpoint(ppid)
    # Return the response below when this validation or access check matches.
    if not pp:
        return make_response({"message": "Checkpoint not found"}, HTTPStatus.NOT_FOUND)

    ts = ts_str()
    checkpoint_dir = teacher_checkpoint_project_dir(proj, pp, timestamp_hint=ts)
    base_dir = version_dir(checkpoint_dir, ts)
    # Ensure the destination directory exists before writing files there.
    os.makedirs(base_dir, exist_ok=True)

    # Save solution file(s)
    uploaded_exts = set()
    # Process each up from solution_uploads.
    for up in solution_uploads:
        orig = safe_name(up.filename)
        ext = os.path.splitext(orig)[1].lower()
        # Return the response below when this validation or access check matches.
        if ext not in ALLOWED_SOURCE_EXTS:
            return make_response(
                {"message": f"Unsupported file type: {ext}"}, HTTPStatus.BAD_REQUEST
            )
        uploaded_exts.add(ext)
        up.save(os.path.join(base_dir, orig))

    requested_language = (request.form.get("language", "") or "").strip().lower()
    language_aliases = {
        "python": "python",
        "python3": "python",
        "py": "python",
        "java": "java",
        "c": "c",
    }

    inferred_language = ""
    if ".java" in uploaded_exts:
        inferred_language = "java"
    elif ".py" in uploaded_exts:
        inferred_language = "python"
    elif ".c" in uploaded_exts:
        inferred_language = "c"

    checkpoint_language = (
        language_aliases.get(requested_language)
        or inferred_language
        or getattr(proj, "Language", "")
        or getattr(pp, "Language", "")
    )

    # Save description
    ad = request.files["assignmentdesc"]
    ad_name = safe_name(ad.filename or "assignment.pdf")
    desc_path = os.path.join(base_dir, ad_name)
    ad.save(desc_path)

    # Save additional checkpoint files (optional)
    add_names = []
    # Process each add_up from request.files.getlist('additionalFiles').
    for add_up in request.files.getlist("additionalFiles"):
        if add_up and add_up.filename:
            orig_name = safe_name(add_up.filename)
            add_up.save(os.path.join(base_dir, orig_name))
            add_names.append(orig_name)

    # Persist checkpoint-only paths
    pp.solutionpath = base_dir
    pp.AsnDescriptionPath = desc_path
    pp.AdditionalFilePath = json.dumps(add_names)
    pp.Language = checkpoint_language
    pp.Enabled = True
    try:
        from src.core.database import db

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return make_response(
            {"message": "Failed to save checkpoint paths"}, HTTPStatus.INTERNAL_SERVER_ERROR
        )

    # Recompute outputs for this PRACTICE PROBLEM's testcases using this checkpoint solution
    try:
        recompute_expected_outputs(
            project_repo,
            int(pid),
            solution_override_path=base_dir,
            language_override=checkpoint_language,
            checkpoint_id=int(ppid),
        )
    # Ignore this failure and allow the surrounding operation to continue.
    except Exception:
        pass

    return jsonify({"ok": True})


# Projects HTTP endpoints for testcases.


@assignment_materials_api.route('/get_testcases', methods=["GET"])
@jwt_required()
@inject
def get_testcases(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Return testcases.

    HTTP: GET /api/assignment_materials/get_testcases.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import opt_int
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    project_id = parse_int(request.args.get("id", ""), 0)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    ppid = opt_int(request.args.get("checkpoint_id", ""))
    testcases = project_repo.get_testcases(int(project_id), checkpoint_id=ppid)

    return make_response(json.dumps(list(testcases.values())), HTTPStatus.OK)


@assignment_materials_api.route('/count_testcases', methods=["GET"])
@jwt_required()
@inject
def count_testcases(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Count testcases.

    HTTP: GET /api/assignment_materials/count_testcases.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import opt_int
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    project_id = parse_int(request.args.get("id", ""), 0)
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    ppid = opt_int(request.args.get("checkpoint_id", ""))
    count = project_repo.count_testcases(int(project_id), checkpoint_id=ppid)
    return jsonify({"count": int(count)})


@assignment_materials_api.route('/json_add_testcases', methods=["POST"])
@jwt_required()
@inject
def json_add_testcases(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Handle json add testcases for this component.

    HTTP: POST /api/assignment_materials/json_add_testcases.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import opt_int
    from src.assignment_permissions import parse_bool
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    file = request.files["file"]
    project_id = request.form["project_id"]
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(parse_int(project_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    ppid = opt_int(request.form.get("checkpoint_id", ""))

    # Require a solution root for whichever scope we're writing testcases into (main or checkpoint)
    sol = project_repo.get_project_path(int(project_id), checkpoint_id=ppid)
    if not sol:
        msg = "Checkpoint has no solution files" if ppid else "Assignment has no solution files"
        return make_response({"message": msg}, HTTPStatus.BAD_REQUEST)

    try:
        proj = project_repo.get_selected_project(int(project_id))
        class_id = int(getattr(proj, "ClassId", 0) or 0)
    except Exception:
        class_id = 0

    try:
        # Read the stored JSON document into Python data structures.
        json_obj = json.load(file)
    # Convert this failure into the fallback result or error response below.
    except json.JSONDecodeError:
        message = {"message": "Incorrect JSON format"}
        return make_response(message, HTTPStatus.INTERNAL_SERVER_ERROR)
    else:
        # Return the response below when this validation or access check matches.
        if not isinstance(json_obj, list):
            return make_response(
                {"message": "Testcase JSON must be an array"},
                HTTPStatus.BAD_REQUEST,
            )

        ordered_testcases = sorted(
            enumerate(json_obj),
            key=lambda item: (
                parse_int(item[1].get("order", item[0] + 1), item[0] + 1)
                if isinstance(item[1], dict)
                else item[0] + 1
            ),
        )
        validated_testcases = []
        # Process each (_, testcase) from ordered_testcases.
        for _, testcase in ordered_testcases:
            # Return the response below when this validation or access check matches.
            if not isinstance(testcase, dict):
                return make_response(
                    {"message": "Each testcase must be an object"},
                    HTTPStatus.BAD_REQUEST,
                )

            name = str(testcase.get("name", "") or "").strip()
            input_data = str(testcase.get("input", "") or "").replace("\r\n", "\n").replace("\r", "\n")
            # Return the response below when this validation or access check matches.
            if not name:
                return make_response(
                    {"message": "Each testcase requires a name"},
                    HTTPStatus.BAD_REQUEST,
                )
            validated_testcases.append(
                {
                    "name": name,
                    "input": input_data,
                    "output": str(testcase.get("output", "") or ""),
                    "hidden": parse_bool(testcase.get("hidden", False)),
                }
            )

        existing_count = project_repo.count_testcases(
            int(project_id),
            checkpoint_id=ppid,
        )
        # Process each (offset, testcase) from enumerate(validated_testcases, start=1).
        for offset, testcase in enumerate(validated_testcases, start=1):
            project_repo.add_or_update_testcase(
                int(project_id),
                -1,
                testcase["name"],
                testcase["input"],
                testcase["output"],
                class_id,
                testcase["hidden"],
                existing_count + offset,
                checkpoint_id=ppid,
            )

    return make_response("Testcase Added", HTTPStatus.OK)


@assignment_materials_api.route('/add_or_update_testcase', methods=["POST"])
@jwt_required()
@inject
def add_or_update_testcase(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Handle add or update testcase for this component.

    HTTP: POST /api/assignment_materials/add_or_update_testcase.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_permissions import parse_bool
    from src.assignment_permissions import user_can_access_class_id
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    # Grab all fields safely (defaults prevent NameError)
    id_val = request.form.get("id", "").strip()
    # Read this input from the incoming HTTP request.
    name = request.form.get("name", "").strip()
    # Read this input from the incoming HTTP request.
    input_data = request.form.get("input", "")
    # Read this input from the incoming HTTP request.
    output = request.form.get("output", "")
    # Read this input from the incoming HTTP request.
    project_id = request.form.get("project_id", "").strip()
    # Read this input from the incoming HTTP request.
    class_id = request.form.get("class_id", "").strip()
    # Read this input from the incoming HTTP request.
    sort_order_raw = request.form.get("order", "").strip()

    # Read this input from the incoming HTTP request.
    ppid_raw = request.form.get("checkpoint_id", "").strip()

    # Return the response below when this validation or access check matches.
    if id_val == "" or name == "" or project_id == "" or class_id == "":
        return make_response("Error in form", HTTPStatus.BAD_REQUEST)

    # Coerce types with validation
    try:
        project_id = int(project_id)
        id_val = int(id_val)
        class_id_int = int(class_id)
    # Convert this failure into the fallback result or error response below.
    except ValueError:
        return make_response("Invalid numeric id", HTTPStatus.BAD_REQUEST)

    hidden = parse_bool(request.form.get("hidden", ""))
    sort_order = (
        int(sort_order_raw) if sort_order_raw.isdigit() and int(sort_order_raw) > 0 else None
    )
    checkpoint_id = int(ppid_raw) if (ppid_raw or "").isdigit() else None
    # Stop here when the caller does not have the required access.
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(class_id_int):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Do not allow testcase edits/creates unless solution files exist (main or checkpoint)
    sol = project_repo.get_project_path(int(project_id), checkpoint_id=checkpoint_id)
    # Return the response below when this validation or access check matches.
    if not sol:
        return make_response(
            (
                "Checkpoint has no solution files"
                if checkpoint_id
                else "Assignment has no solution files"
            ),
            HTTPStatus.BAD_REQUEST,
        )

    try:
        project_repo.add_or_update_testcase(
            project_id,
            id_val,
            name,
            input_data,
            output,
            class_id_int,
            hidden,
            sort_order,
            checkpoint_id=checkpoint_id,
        )
    except ValueError as exc:
        return make_response({"message": str(exc)}, HTTPStatus.BAD_REQUEST)

    return make_response("Testcase Added", HTTPStatus.OK)


@assignment_materials_api.route('/reorder_testcases', methods=["POST"])
@jwt_required()
@inject
def reorder_testcases(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Reorder testcases.

    HTTP: POST /api/assignment_materials/reorder_testcases.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_permissions import access_denied_response
    from src.assignment_permissions import is_staff_user
    from src.assignment_setup import opt_int
    from src.assignment_setup import parse_int
    from src.assignment_permissions import user_can_access_project_id

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    project_id = parse_int(request.form.get("project_id", ""), 0)
    # Stop here when the caller does not have the required access.
    if project_id <= 0 or not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    checkpoint_id = opt_int(request.form.get("checkpoint_id", ""))
    try:
        testcase_ids = json.loads(request.form.get("testcase_ids", "[]"))
        # Reject this case with the exception below.
        if not isinstance(testcase_ids, list):
            raise ValueError("testcase_ids must be an array")
        ordered_ids = [int(testcase_id) for testcase_id in testcase_ids]
        project_repo.reorder_testcases(
            project_id,
            ordered_ids,
            checkpoint_id=checkpoint_id,
        )
    # Convert this failure into the fallback result or error response below.
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return make_response({"message": str(exc)}, HTTPStatus.BAD_REQUEST)

    return make_response("Testcase order updated", HTTPStatus.OK)


@assignment_materials_api.route('/remove_testcase', methods=["POST"])
@jwt_required()
@inject
def remove_testcase(project_repo: AssignmentRepository = Provide[Container.project_repo]):
    """Authorize the testcase's actual assignment before deleting it."""
    from src.assignment_permissions import access_denied_response, is_staff_user
    from src.assignment_permissions import user_can_access_project_id
    from src.assignment_setup import parse_int

    if not is_staff_user():
        return access_denied_response()
    testcase_id = parse_int(request.form.get("id"), 0)
    if testcase_id <= 0:
        return make_response({"message": "Invalid testcase id"}, HTTPStatus.BAD_REQUEST)
    testcase = project_repo.get_testcase(testcase_id)
    if testcase is None:
        return make_response({"message": "Testcase not found"}, HTTPStatus.NOT_FOUND)
    if not user_can_access_project_id(int(testcase.ProjectId)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    project_repo.remove_testcase(testcase_id)
    return make_response("Testcase Removed", HTTPStatus.OK)


def seed_version_dir(dest_dir: str, *, seed_from_dir: str | None):
    """Copy the current resource directory when creating a new version."""
    # Ensure the destination directory exists before writing files there.
    os.makedirs(dest_dir, exist_ok=True)
    if seed_from_dir and os.path.isdir(seed_from_dir):
        # Copy the source content into the destination specified below.
        shutil.copytree(seed_from_dir, dest_dir, dirs_exist_ok=True)
