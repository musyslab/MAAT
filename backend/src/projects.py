import importlib.util
import base64
import html
import json
import mimetypes
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime
from http import HTTPStatus
from io import BytesIO
from urllib.parse import quote
from xml.etree import ElementTree

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, jsonify, make_response, request
from flask_jwt_extended import current_user, jwt_required
from sqlalchemy import and_, func
from werkzeug.utils import secure_filename

from container import Container
from src.constants import ADMIN_ROLE, STUDENT_ROLE, TEACHER_ROLE
from src.repositories.class_repository import ClassRepository
from src.repositories.database import db
from src.repositories.models import (
    CheckpointGrades,
    ClassAssignments,
    Classes,
    Labs,
    LectureSections,
    MainAssignmentGrades,
    Modules,
    Checkpoints,
    Projects,
    StudentCheckpointSkips,
    StudentCooldownSkips,
    StudentHiddenModules,
    StudentStarAwards,
    Submissions,
    Users,
)
from src.repositories.project_repository import ProjectRepository
from src.repositories.submission_repository import SubmissionRepository
from src.repositories.user_repository import UserRepository

projects_api = Blueprint('projects_api', __name__)

ALLOWED_SOURCE_EXTS = {'.py', '.c', '.java', '.rkt'}
ALLOWED_PRESENTATION_EXTS = {'.pdf', '.ppt', '.pptx'}
PRESENTATION_MIME_TYPES = {
    '.pdf': 'application/pdf',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}
TS_DIR_RE = re.compile(r"^\d{8}_\d{6}$")
CHECKPOINT_COMPLETION_STARS = 1
MAIN_PROJECT_COMPLETION_STARS = 3
EARLY_START_MULTIPLIER = 2
CHECKPOINT_SKIP_COST_STARS = 6
CHECKPOINT_SUBMISSION_COOLDOWN_SKIP_COST_STARS = 1
MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS = 2
INPUT_EVENT_PREFIX = "[[[MAAT_INPUT_B64:"

DOCX_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOCX_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DOCX_PACKAGE_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCX_DRAWING_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"
DOCX_WORDPROCESSING_DRAWING_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
DOCX_NAMESPACES = {
    "w": DOCX_NAMESPACE,
    "r": DOCX_RELATIONSHIP_NAMESPACE,
    "a": DOCX_DRAWING_NAMESPACE,
    "wp": DOCX_WORDPROCESSING_DRAWING_NAMESPACE,
}


def docx_tag(name: str) -> str:
    return f"{{{DOCX_NAMESPACE}}}{name}"


def docx_element_text(element) -> str:
    """Extract readable text, including tabs and explicit line breaks, from OOXML."""
    pieces = []

    for node in element.iter():
        if node.tag == docx_tag("t"):
            pieces.append(node.text or "")
        elif node.tag == docx_tag("tab"):
            pieces.append("\t")
        elif node.tag in {docx_tag("br"), docx_tag("cr")}:
            pieces.append("\n")

    return "".join(pieces)


def docx_xml_value(element, default: str = "") -> str:
    if element is None:
        return default
    return str(element.get(docx_tag("val"), default) or default)


def docx_property_enabled(properties, name: str) -> bool:
    if properties is None:
        return False

    value_node = properties.find(f"w:{name}", DOCX_NAMESPACES)
    if value_node is None:
        return False

    value = docx_xml_value(value_node, "true").strip().lower()
    return value not in {"0", "false", "none", "off"}


def docx_relationships(archive: zipfile.ZipFile) -> dict[str, dict[str, str | bool]]:
    try:
        relationships_xml = archive.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}

    root = ElementTree.fromstring(relationships_xml)
    relationship_tag = f"{{{DOCX_PACKAGE_RELATIONSHIP_NAMESPACE}}}Relationship"
    relationships = {}

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
    try:
        styles_xml = archive.read("word/styles.xml")
    except KeyError:
        return {}

    root = ElementTree.fromstring(styles_xml)
    styles = {}

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
    raw_target = str(target or "").replace("\\", "/")
    if not raw_target:
        return None

    if raw_target.startswith("/"):
        normalized = posixpath.normpath(raw_target.lstrip("/"))
    else:
        normalized = posixpath.normpath(posixpath.join("word", raw_target))

    if normalized.startswith("../") or normalized == "..":
        return None
    return normalized


def docx_image_html(element, archive: zipfile.ZipFile, relationships: dict) -> str:
    images = []

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
    fragments = []

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
    if vertical_alignment == "superscript":
        return f"<sup>{content}</sup>"
    if vertical_alignment == "subscript":
        return f"<sub>{content}</sub>"
    return content


def docx_inline_html(element, archive: zipfile.ZipFile, relationships: dict) -> str:
    fragments = []

    for child in element:
        if child.tag == docx_tag("r"):
            fragments.append(docx_run_html(child, archive, relationships))
        elif child.tag == docx_tag("hyperlink"):
            link_content = "".join(
                docx_run_html(run, archive, relationships)
                for run in child.findall("w:r", DOCX_NAMESPACES)
            )
            relationship_id = str(
                child.get(f"{{{DOCX_RELATIONSHIP_NAMESPACE}}}id", "")
            )
            relationship = relationships.get(relationship_id) or {}
            target = str(relationship.get("target", ""))

            if (
                relationship.get("external")
                and re.match(r"^(?:https?://|mailto:)", target, re.IGNORECASE)
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
    try:
        return min(360.0, max(-360.0, int(value) / 20.0))
    except (TypeError, ValueError):
        return None


def docx_paragraph_html(
    paragraph,
    archive: zipfile.ZipFile,
    relationships: dict,
    paragraph_styles: dict,
) -> str:
    content = docx_inline_html(paragraph, archive, relationships)
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
        paragraph_properties.find("w:jc", DOCX_NAMESPACES)
        if paragraph_properties is not None
        else None,
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
        for attribute_name, css_name in (("before", "margin-top"), ("after", "margin-bottom")):
            points = docx_twips_to_points(str(spacing.get(docx_tag(attribute_name), "")))
            if points is not None:
                paragraph_styles_css.append(f"{css_name}:{max(0, points):g}pt")

    style_attribute = (
        f' style="{html.escape(";".join(paragraph_styles_css), quote=True)}"'
        if paragraph_styles_css
        else ""
    )

    if heading_level:
        return f"<h{heading_level}{style_attribute}>{content}</h{heading_level}>"

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
    rows = []

    for row in table.findall("w:tr", DOCX_NAMESPACES):
        cells = []
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
    with zipfile.ZipFile(BytesIO(contents)) as archive:
        document_xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(document_xml)
        body = root.find("w:body", DOCX_NAMESPACES)
        relationships = docx_relationships(archive)
        paragraph_styles = docx_paragraph_styles(archive)

        def render_blocks(parent) -> list[str]:
            rendered = []
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
    if not office_binary or not path or not os.path.isfile(path):
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="maat-assignment-preview-") as output_dir:
            office_profile_dir = os.path.join(output_dir, "office-profile")
            os.makedirs(office_profile_dir, exist_ok=True)
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
            if completed.returncode != 0:
                return None

            converted_path = os.path.join(
                output_dir,
                f"{os.path.splitext(os.path.basename(path))[0]}.pdf",
            )
            if not os.path.isfile(converted_path):
                return None

            with open(converted_path, "rb") as converted_file:
                return converted_file.read()
    except (OSError, subprocess.SubprocessError):
        return None


def unavailable_office_preview_html(filename: str) -> bytes:
    safe_filename = html.escape(filename or "assignment document")
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:Inter,system-ui,sans-serif;padding:40px;color:#334155;line-height:1.55">
  <h1 style="color:#172033">Preview unavailable</h1>
  <p><strong>{safe_filename}</strong> uses the legacy Word format and could not be converted on this server.</p>
  <p>Ask your instructor for a browser-compatible copy if you need to view this document here.</p>
</body>
</html>""".encode("utf-8")


def presentation_pdf_preview(path: str) -> bytes | None:
    """Convert PPT/PPTX files to PDF for inline student preview when LibreOffice is available."""
    office_binary = shutil.which("libreoffice") or shutil.which("soffice")
    if not office_binary or not path or not os.path.isfile(path):
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="maat-presentation-preview-") as output_dir:
            office_profile_dir = os.path.join(output_dir, "office-profile")
            os.makedirs(office_profile_dir, exist_ok=True)
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
            if completed.returncode != 0:
                return None

            converted_path = os.path.join(
                output_dir,
                f"{os.path.splitext(os.path.basename(path))[0]}.pdf",
            )
            if not os.path.isfile(converted_path):
                return None

            with open(converted_path, "rb") as converted_file:
                return converted_file.read()
    except (OSError, subprocess.SubprocessError):
        return None


def unavailable_presentation_preview_html(filename: str) -> bytes:
    safe_filename = html.escape(filename or "module presentation")
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:Inter,system-ui,sans-serif;padding:40px;color:#334155;line-height:1.55">
  <h1 style="color:#172033">Presentation preview unavailable</h1>
  <p><strong>{safe_filename}</strong> could not be converted for browser viewing on this server.</p>
  <p>The presentation is optional for this module.</p>
</body>
</html>""".encode("utf-8")



def normalize_grader_language(language: str, solution_root: str = "") -> str:
    """
    Convert the project language stored/displayed by the app into the token
    expected by /tabot-files/grading-scripts/grade.py. The UI stores Python as
    "python", but the grader's language switch uses "py".
    """
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
            candidates = [os.path.splitext(name)[1].lower() for name in os.listdir(solution_root)]
        elif solution_root:
            candidates = [os.path.splitext(solution_root)[1].lower()]

        if ".py" in candidates:
            return "py"
        if ".java" in candidates:
            return "java"
        if ".c" in candidates:
            return "c"
        if ".rkt" in candidates or ".scm" in candidates:
            return "racket"
    except Exception:
        pass

    return raw or "py"


def solution_source_uses_input(solution_root: str, language: str) -> bool:
    """Best-effort check used to migrate legacy testcase output transcripts."""
    if not solution_root or not os.path.exists(solution_root):
        return False

    normalized_language = normalize_grader_language(language, solution_root)
    if normalized_language not in {"py", "java"}:
        return False

    expected_extension = ".py" if normalized_language == "py" else ".java"
    if os.path.isdir(solution_root):
        source_paths = [
            os.path.join(base, filename)
            for base, _, filenames in os.walk(solution_root)
            for filename in filenames
            if os.path.splitext(filename)[1].lower() == expected_extension
        ]
    else:
        source_paths = [solution_root]

    for source_path in source_paths:
        try:
            if os.path.getsize(source_path) > 2 * 1024 * 1024:
                continue
            with open(source_path, "r", encoding="utf-8", errors="replace") as source_file:
                source = source_file.read()
        except OSError:
            continue

        if normalized_language == "py" and re.search(r"\binput\s*\(", source):
            return True
        if normalized_language == "java" and (
            re.search(r"\bScanner\b[\s\S]*?\.\s*next(?:Line|Boolean|Byte|Short|Int|Long|Float|Double|BigInteger|BigDecimal)?\s*\(", source)
            or re.search(r"\bBufferedReader\b[\s\S]*?\.\s*readLine\s*\(", source)
            or re.search(r"\bSystem\s*\.\s*console\s*\(\s*\)\s*\.\s*readLine\s*\(", source)
        ):
            return True

    return False

def parse_int(v, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


def get_star_balance(user_id: int, class_id: int) -> int:
    """Return awards minus checkpoint and cooldown skip purchases."""
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

    return max(
        0,
        parse_int(awarded, 0)
        - parse_int(checkpoint_spent, 0)
        - parse_int(cooldown_spent, 0),
    )


def incentive_summary(user_id: int, class_id: int) -> dict:
    return {
        "stars": get_star_balance(user_id, class_id),
        "star_balance": get_star_balance(user_id, class_id),
        "checkpoint_completion_stars": CHECKPOINT_COMPLETION_STARS,
        "main_project_completion_stars": MAIN_PROJECT_COMPLETION_STARS,
        "early_start_multiplier": EARLY_START_MULTIPLIER,
        "checkpoint_skip_cost": CHECKPOINT_SKIP_COST_STARS,
        "checkpoint_cooldown_skip_cost": CHECKPOINT_SUBMISSION_COOLDOWN_SKIP_COST_STARS,
        "main_project_cooldown_skip_cost": MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS,
        "cooldown_skip_cost": MAIN_PROJECT_SUBMISSION_COOLDOWN_SKIP_COST_STARS,
    }


def skipped_checkpoint_ids_for_project(user_id: int, project_id: int) -> set[int]:
    rows = StudentCheckpointSkips.query.filter(
        StudentCheckpointSkips.UserId == int(user_id),
        StudentCheckpointSkips.ProjectId == int(project_id),
    ).all()
    return {parse_int(getattr(row, "CheckpointId", 0), 0) for row in rows}


def checkpoint_awards_for_project(user_id: int, project_id: int) -> dict[int, dict]:
    rows = StudentStarAwards.query.filter(
        StudentStarAwards.UserId == int(user_id),
        StudentStarAwards.ProjectId == int(project_id),
        StudentStarAwards.AwardType == "checkpoint_completion",
    ).all()

    return {
        parse_int(getattr(row, "CheckpointId", 0), 0): {
            "stars": parse_int(getattr(row, "Stars", 0), 0),
            "base_stars": parse_int(getattr(row, "BaseStars", 0), 0),
            "multiplier": parse_int(getattr(row, "Multiplier", 1), 1),
            "started_early": bool(getattr(row, "StartedEarly", False)),
        }
        for row in rows
    }


def main_award_for_project(user_id: int, project_id: int) -> dict | None:
    row = StudentStarAwards.query.filter(
        StudentStarAwards.UserId == int(user_id),
        StudentStarAwards.ProjectId == int(project_id),
        StudentStarAwards.CheckpointId == 0,
        StudentStarAwards.AwardType == "main_completion",
    ).first()

    if row is None:
        return None

    return {
        "stars": parse_int(getattr(row, "Stars", 0), 0),
        "base_stars": parse_int(getattr(row, "BaseStars", 0), 0),
        "multiplier": parse_int(getattr(row, "Multiplier", 1), 1),
        "started_early": bool(getattr(row, "StartedEarly", False)),
    }

DEFAULT_CHECKPOINT_NAME_RE = re.compile(r"^(checkpoint)\s+\d+$", re.IGNORECASE)

def default_checkpoint_name(number: int) -> str:
    return f"Checkpoint {int(number)}"

def should_renumber_default_checkpoint_name(name: str) -> bool:
    return bool(DEFAULT_CHECKPOINT_NAME_RE.match(str(name or "").strip()))

def normalize_default_checkpoint_names(project_repo: ProjectRepository, rows):
    normalized_rows = []

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
    project_repo: ProjectRepository,
    project_id: int,
    *,
    context: str = "project",
) -> int | None:
    project_id = parse_int(project_id, 0)
    if project_id <= 0:
        return None

    try:
        existing_checkpoints = project_repo.list_checkpoints(project_id)
        if existing_checkpoints:
            return int(existing_checkpoints[0].Id)

        checkpoint_id = project_repo.create_checkpoint(
            project_id,
            name=default_checkpoint_name(1),
        )
        return int(checkpoint_id) if checkpoint_id else None
    except Exception as exc:
        print(
            f"[{context}] default checkpoint creation failed for project {project_id}: {exc}",
            flush=True,
        )
        return None


def ensure_default_checkpoint_for_module(
    project_repo: ProjectRepository,
    module_id: int,
    *,
    context: str = "module",
) -> int | None:
    module_id = parse_int(module_id, 0)
    if module_id <= 0:
        return None

    try:
        project = project_repo.get_main_project_for_module(module_id)
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


def current_user_assignment_for_class(class_id: int):
    class_id = parse_int(class_id, 0)
    user_id = current_user_id()

    if class_id <= 0 or user_id <= 0:
        return None

    try:
        return ClassAssignments.query.filter(
            ClassAssignments.UserId == user_id,
            ClassAssignments.ClassId == class_id,
        ).first()
    except Exception:
        return None


def current_user_assignment_role_for_class(class_id: int) -> int | None:
    assignment = current_user_assignment_for_class(class_id)

    if assignment is None:
        return None

    return parse_int(getattr(assignment, "Role", None), STUDENT_ROLE)


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


def access_denied_response(status=HTTPStatus.UNAUTHORIZED):
    return make_response({'message': 'Access Denied'}, status)

def source_file_names(path_value: str) -> list[str]:
    if not path_value:
        return []

    try:
        if os.path.isdir(path_value):
            names = []
            for filename in sorted(os.listdir(path_value)):
                full_path = os.path.join(path_value, filename)
                if os.path.isfile(full_path):
                    _, ext = os.path.splitext(filename)
                    if ext.lower() in ALLOWED_SOURCE_EXTS:
                        names.append(filename)
            return names

        _, ext = os.path.splitext(path_value)
        return [os.path.basename(path_value)] if ext.lower() in ALLOWED_SOURCE_EXTS else []
    except Exception:
        return []


def project_setup_status(
    project_repo: ProjectRepository,
    project_id: int,
    checkpoint_id: int | None = None,
    testcase_count: int | None = None,
):
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




def student_checkpoint_rows(project_repo: ProjectRepository, project_id: int) -> list[dict]:
    """
    Student-safe checkpoint list rows.
    Returns only enabled checkpoints and includes setup/progress fields used by student pages.
    """
    project_id = parse_int(project_id, 0)
    if project_id <= 0:
        return []

    try:
        rows = normalize_default_checkpoint_names(
            project_repo,
            project_repo.list_checkpoints(project_id),
        )
    except Exception as exc:
        print(f"[student_checkpoint_rows] failed to list checkpoints for project {project_id}: {exc}", flush=True)
        return []

    user_id = int(getattr(current_user, "Id", 0) or 0)
    skipped_checkpoint_ids = skipped_checkpoint_ids_for_project(user_id, project_id)
    checkpoint_awards = checkpoint_awards_for_project(user_id, project_id)
    passed_checkpoint_ids: set[int] = set()
    try:
        if hasattr(Submissions, "IsCheckpoint") and hasattr(Submissions, "CheckpointId"):
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

        out.append({
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
        })

    return out


def checkpoint_submission_count_map(project_id: int) -> dict[int, int]:
    try:
        if hasattr(Submissions, "CheckpointId"):
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
    except Exception:
        pass
    return {}


def checkpoint_unique_user_counts(project_ids: list[int]) -> dict[int, int]:
    ids = [int(pid) for pid in (project_ids or []) if int(pid or 0) > 0]
    if not ids:
        return {}

    try:
        rows = (
            db.session.query(
                Submissions.Project,
                func.count(func.distinct(Submissions.User)),
            )
            .filter(Submissions.Project.in_(ids), Submissions.IsCheckpoint == True)
            .group_by(Submissions.Project)
            .all()
        )
        return {int(project_id): int(count or 0) for project_id, count in rows if project_id is not None}
    except Exception:
        return {}


def main_completed_project_ids(project_ids: list[int]) -> set[int]:
    ids = [int(pid) for pid in (project_ids or []) if int(pid or 0) > 0]
    if not ids:
        return set()

    try:
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
    except Exception:
        return set()

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


def user_can_access_project_id(project_id: int) -> bool:
    project_id = parse_int(project_id, 0)
    if project_id <= 0:
        return False

    project = Projects.query.filter(Projects.Id == project_id).first()
    if project is None:
        return False

    return user_can_access_class_id(int(getattr(project, "ClassId", 0) or 0))

def current_user_is_enrolled_in_class(class_id: int) -> bool:
    class_id = parse_int(class_id, 0)
    if class_id <= 0:
        return False

    try:
        return (
            ClassAssignments.query.filter(
                ClassAssignments.ClassId == class_id,
                ClassAssignments.UserId == int(current_user.Id),
            ).first()
            is not None
        )
    except Exception:
        return False


def current_user_is_enrolled_in_project_class(project_id: int) -> bool:
    project_id = parse_int(project_id, 0)
    if project_id <= 0:
        return False

    project = Projects.query.filter(Projects.Id == project_id).first()
    if project is None:
        return False

    class_id = parse_int(getattr(project, "ClassId", 0) or 0, 0)
    return current_user_is_enrolled_in_class(class_id)


def student_module_is_hidden_for_current_user(module_id: int) -> bool:
    module_id = parse_int(module_id, 0)
    if module_id <= 0:
        return False

    try:
        module = Modules.query.filter(Modules.Id == module_id).first()
        if module is not None and user_can_access_class_id(int(getattr(module, "ClassId", 0) or 0)):
            return False
    except Exception:
        pass

    try:
        return (
            StudentHiddenModules.query.filter(
                StudentHiddenModules.UserId == int(current_user.Id),
                StudentHiddenModules.ModuleId == module_id,
            ).first()
            is not None
        )
    except Exception:
        return False


def current_user_can_access_visible_module_id(module_id: int) -> bool:
    module_id = parse_int(module_id, 0)
    if module_id <= 0:
        return False

    if user_can_access_module_id(module_id):
        return True

    module = Modules.query.filter(Modules.Id == module_id).first()
    if module is None:
        return False

    class_id = parse_int(getattr(module, "ClassId", 0) or 0, 0)
    return (
        current_user_is_enrolled_in_class(class_id)
        and not student_module_is_hidden_for_current_user(module_id)
    )


def project_is_hidden_for_current_student(project) -> bool:
    if not project:
        return False

    project_id = parse_int(getattr(project, "Id", 0) or 0, 0)
    if project_id > 0 and user_can_access_project_id(project_id):
        return False

    module_id = parse_int(getattr(project, "ModuleId", 0) or 0, 0)
    return module_id > 0 and student_module_is_hidden_for_current_user(module_id)


def current_user_can_access_visible_project_id(project_id: int) -> bool:
    project_id = parse_int(project_id, 0)
    if project_id <= 0:
        return False

    if user_can_access_project_id(project_id):
        return True

    project = Projects.query.filter(Projects.Id == project_id).first()
    if project is None:
        return False

    class_id = parse_int(getattr(project, "ClassId", 0) or 0, 0)
    if not current_user_is_enrolled_in_class(class_id):
        return False

    return not project_is_hidden_for_current_student(project)


def current_user_can_download_project_files(project_id: int) -> bool:
    if user_can_access_project_id(project_id):
        return True

    return current_user_can_access_visible_project_id(project_id)



def user_can_access_module_id(module_id: int) -> bool:
    module_id = parse_int(module_id, 0)
    if module_id <= 0:
        return False

    module = Modules.query.filter(Modules.Id == module_id).first()
    if module is None:
        return False

    return user_can_access_class_id(int(getattr(module, "ClassId", 0) or 0))


def user_can_access_checkpoint_id(checkpoint_id: int) -> bool:
    checkpoint_id = parse_int(checkpoint_id, 0)
    if checkpoint_id <= 0:
        return False

    checkpoint = Checkpoints.query.filter(Checkpoints.Id == checkpoint_id).first()
    if checkpoint is None:
        return False

    return user_can_access_project_id(int(getattr(checkpoint, "ProjectId", 0) or 0))


def user_can_access_student_id(student_id: int) -> bool:
    student_id = parse_int(student_id, 0)
    if student_id <= 0:
        return False

    if is_admin_user():
        return True

    assignments = ClassAssignments.query.filter(ClassAssignments.UserId == student_id).all()
    return any(user_can_access_class_id(int(assignment.ClassId)) for assignment in assignments)


def filter_projects_for_current_user(projects):
    return [
        project
        for project in projects
        if user_can_access_class_id(int(getattr(project, "ClassId", 0) or 0))
    ]

def opt_int(raw) -> int | None:
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
        if not s:
            return []
        vals = json.loads(s) if s.startswith("[") else [s]
        return [os.path.basename(v) for v in (vals or []) if v]
    except Exception:
        return []


def project_module(project):
    if not project:
        return None
    module = getattr(project, "Module", None)
    if module:
        return module
    module_id = getattr(project, "ModuleId", None)
    if module_id:
        return Modules.query.filter(Modules.Id == int(module_id)).first()
    return None

def project_start(project):
    module = project_module(project)
    return getattr(module, "Start", None) if module else None

def project_end(project):
    module = project_module(project)
    return getattr(module, "End", None) if module else None

def count_checkpoint_unique_users(project_id: int) -> int:
    try:
        if hasattr(Submissions, "IsCheckpoint"):
            return int(
                db.session.query(func.count(func.distinct(Submissions.User)))
                .filter(Submissions.Project == int(project_id), Submissions.IsCheckpoint == True)
                .scalar()
                or 0
            )
    except Exception:
        pass
    return 0

def project_root() -> str:
    return "/tabot-files/project-files"


def path_segment(value: str, fallback: str = "unnamed") -> str:
    safe = secure_filename(str(value or "").strip()).replace(" ", "_")
    return safe or fallback


def teacher_root_for_class(class_id: int) -> str:
    class_item = Classes.query.filter(Classes.Id == int(class_id)).first()
    class_name = path_segment(getattr(class_item, "Name", "") if class_item else f"class_{class_id}", f"class_{class_id}")
    school = getattr(class_item, "School", None) if class_item else None
    school_name = path_segment(getattr(school, "Name", "") if school else "school", "school")
    return os.path.join(project_root(), school_name, class_name, "teacher-files")


def student_root_for_class(class_id: int) -> str:
    class_item = Classes.query.filter(Classes.Id == int(class_id)).first()
    class_name = path_segment(getattr(class_item, "Name", "") if class_item else f"class_{class_id}", f"class_{class_id}")
    school = getattr(class_item, "School", None) if class_item else None
    school_name = path_segment(getattr(school, "Name", "") if school else "school", "school")
    return os.path.join(project_root(), school_name, class_name, "student-files")


def stable_module_identity(module: Modules | None, fallback_name: str, timestamp_hint: str | None = None) -> tuple[str, str]:
    if module is None:
        return timestamp_hint or datetime.now().strftime("%Y%m%d_%H%M%S"), path_segment(fallback_name, "module")

    changed = False

    if not getattr(module, "FileTimestamp", None):
        module.FileTimestamp = timestamp_hint or datetime.now().strftime("%Y%m%d_%H%M%S")
        changed = True

    if not getattr(module, "FirstName", None):
        module.FirstName = getattr(module, "Name", None) or fallback_name or "module"
        changed = True

    if changed:
        db.session.commit()

    return str(module.FileTimestamp), path_segment(module.FirstName, "module")


def stable_project_first_name(project: Projects | None, fallback_name: str) -> str:
    if project is None:
        return path_segment(fallback_name, "project")

    if not getattr(project, "FirstName", None):
        project.FirstName = getattr(project, "Name", None) or fallback_name or "project"
        db.session.commit()

    return path_segment(project.FirstName, "project")


def stable_checkpoint_first_name(checkpoint: Checkpoints | None, fallback_name: str) -> str:
    if checkpoint is None:
        return path_segment(fallback_name, "checkpoint")

    if not getattr(checkpoint, "FirstName", None):
        checkpoint.FirstName = getattr(checkpoint, "Name", None) or fallback_name or "checkpoint"
        db.session.commit()

    return path_segment(checkpoint.FirstName, "checkpoint")


def module_folder_name(module: Modules | None, fallback_name: str, timestamp_hint: str | None = None) -> str:
    ts, first_name = stable_module_identity(module, fallback_name, timestamp_hint)
    return f"{ts}_{first_name}"


def teacher_module_dir(module: Modules) -> str:
    return os.path.join(
        teacher_root_for_class(int(module.ClassId)),
        module_folder_name(module, getattr(module, "Name", "module")),
    )


def module_presentation_path(module: Modules | None) -> str | None:
    if module is None:
        return None

    timestamp = str(getattr(module, "FileTimestamp", "") or "").strip()
    first_name = str(getattr(module, "FirstName", "") or "").strip()
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

    if not candidates:
        return ProjectRepository().default_module_presentation(module)

    return max(candidates, key=lambda path: os.path.getmtime(path))


def teacher_main_project_dir(project: Projects, timestamp_hint: str | None = None) -> str:
    module = project_module(project)
    module_folder = module_folder_name(module, getattr(project, "Name", "module"), timestamp_hint)
    project_folder = stable_project_first_name(project, getattr(project, "Name", "project"))
    return os.path.join(teacher_root_for_class(int(project.ClassId)), module_folder, "main", project_folder)


def teacher_checkpoint_project_dir(project: Projects, checkpoint: Checkpoints, timestamp_hint: str | None = None) -> str:
    module = project_module(project)
    module_folder = module_folder_name(module, getattr(project, "Name", "module"), timestamp_hint)
    checkpoint_folder = stable_checkpoint_first_name(checkpoint, getattr(checkpoint, "Name", "checkpoint"))
    return os.path.join(teacher_root_for_class(int(project.ClassId)), module_folder, "checkpoint", checkpoint_folder)


def teacher_main_project_dir_for_new(class_id: int, module_id: int | None, project_name: str, timestamp_hint: str) -> str:
    module = Modules.query.filter(Modules.Id == int(module_id)).first() if module_id else None
    module_folder = module_folder_name(module, project_name, timestamp_hint)
    return os.path.join(
        teacher_root_for_class(int(class_id)),
        module_folder,
        "main",
        path_segment(project_name, "project"),
    )


def is_ts_dir(name: str) -> bool:
    return bool(TS_DIR_RE.match(name or ""))


def version_dir(proj_dir_path: str, ts: str) -> str:
    return os.path.join(proj_dir_path, ts)

def pick_latest_version_dir(proj_dir_path: str) -> str | None:
    try:
        kids = [d for d in os.listdir(proj_dir_path) if is_ts_dir(d) and os.path.isdir(os.path.join(proj_dir_path, d))]
        if kids:
            return os.path.join(proj_dir_path, max(kids))
    except Exception:
        pass
    return None

def seed_version_dir(dest_dir: str, *, seed_from_dir: str | None, seed_solution_path: str | None, seed_desc_path: str | None, seed_add_paths: list[str]):
    os.makedirs(dest_dir, exist_ok=True)
    # Prefer copying an existing version directory (new layout)
    if seed_from_dir and os.path.isdir(seed_from_dir):
        shutil.copytree(seed_from_dir, dest_dir, dirs_exist_ok=True)
        return
    # Legacy seeding: copy solution file/dir sources
    if seed_solution_path and os.path.exists(seed_solution_path):
        if os.path.isdir(seed_solution_path):
            for fn in os.listdir(seed_solution_path):
                src = os.path.join(seed_solution_path, fn)
                if os.path.isfile(src) and os.path.splitext(fn)[1].lower() in (ALLOWED_SOURCE_EXTS | {".h", ".hpp", ".cpp"}):
                    shutil.copy2(src, os.path.join(dest_dir, fn))
        else:
            shutil.copy2(seed_solution_path, os.path.join(dest_dir, os.path.basename(seed_solution_path)))
    # Copy assignment description
    if seed_desc_path and os.path.isfile(seed_desc_path):
        shutil.copy2(seed_desc_path, os.path.join(dest_dir, os.path.basename(seed_desc_path)))
    # Copy additional files
    for p in (seed_add_paths or []):
        if p and os.path.isfile(p):
            shutil.copy2(p, os.path.join(dest_dir, os.path.basename(p)))

def module_payload(
    module,
    project_repo: ProjectRepository,
    submission_repo: SubmissionRepository,
    total_submission_counts: dict[int, int] | None = None,
    checkpoint_total_counts: dict[int, int] | None = None,
    main_completed_project_ids: set[int] | None = None,
):
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
    project_repo: ProjectRepository,
    total_submission_counts: dict[int, int] | None = None,
    checkpoint_total_counts: dict[int, int] | None = None,
):
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

def analytics_iso(value) -> str:
    if value is None:
        return ""

    try:
        return value.isoformat()
    except Exception:
        return str(value or "")


def analytics_student_row_payload(user, lecture_name: str, lab_name: str, class_id: int, *, submission=None, attempts=0, grade=0, skipped=False):
    student_id = str(getattr(user, "StudentNumber", "") or "")
    last_name = str(getattr(user, "Lastname", "") or "")
    first_name = str(getattr(user, "Firstname", "") or "")
    lecture = str(lecture_name or "")
    lab = str(lab_name or "")
    is_locked = bool(getattr(user, "IsLocked", False))

    if submission is None:
        return [
            last_name,
            first_name,
            lecture,
            lab,
            "N/A",
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
    if current is None:
        return True

    candidate_time = getattr(candidate, "Time", None)
    current_time = getattr(current, "Time", None)

    if candidate_time is not None and current_time is not None and candidate_time != current_time:
        return candidate_time > current_time
    if candidate_time is not None and current_time is None:
        return True
    if candidate_time is None and current_time is not None:
        return False

    return int(getattr(candidate, "Id", 0) or 0) > int(getattr(current, "Id", 0) or 0)


def analytics_dashboard_students(class_id: int):
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
    for user, lecture_name, lab_name, assignment_role in rows:
        role = parse_int(assignment_role, STUDENT_ROLE)
        if role != STUDENT_ROLE:
            continue

        student_rows.append({
            "user": user,
            "lecture": str(lecture_name or ""),
            "lab": str(lab_name or ""),
        })

    return student_rows


def analytics_checkpoint_payloads(
    project_ids: list[int],
    project_repo: ProjectRepository,
) -> dict[str, list[dict]]:
    if not project_ids:
        return {}

    payloads: dict[str, list[dict]] = {str(project_id): [] for project_id in project_ids}

    for project_id in project_ids:
        checkpoints = project_repo.list_checkpoints(project_id)

        for index, checkpoint in enumerate(checkpoints):
            checkpoint_id = int(getattr(checkpoint, "Id", 0) or 0)
            if checkpoint_id <= 0:
                continue

            checkpoint_number = index + 1
            checkpoint_name = str(
                getattr(checkpoint, "Name", "") or default_checkpoint_name(checkpoint_number)
            )

            payloads.setdefault(str(project_id), []).append({
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
            })

    return payloads


def analytics_dashboard_progress(class_id: int, project_ids: list[int], checkpoints_by_project_id: dict[str, list[dict]]):
    students = analytics_dashboard_students(class_id)
    student_ids = [int(getattr(row["user"], "Id", 0) or 0) for row in students]

    item_ids = [f"main-{project_id}" for project_id in project_ids]
    checkpoint_ids_by_project: dict[int, list[int]] = defaultdict(list)
    for project_id_key, checkpoints in checkpoints_by_project_id.items():
        project_id = parse_int(project_id_key, 0)
        for checkpoint in checkpoints:
            checkpoint_id = parse_int(checkpoint.get("id") or checkpoint.get("Id"), 0)
            if project_id > 0 and checkpoint_id > 0:
                checkpoint_ids_by_project[project_id].append(checkpoint_id)
                item_ids.append(f"checkpoint-{project_id}-{checkpoint_id}")

    empty_progress = {item_id: {} for item_id in item_ids}
    if not project_ids or not student_ids:
        return empty_progress

    submissions = (
        Submissions.query
        .filter(Submissions.Project.in_(project_ids), Submissions.User.in_(student_ids))
        .order_by(Submissions.Project.asc(), Submissions.User.asc(), Submissions.Time.desc(), Submissions.Id.desc())
        .all()
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

    for student_row in students:
        user = student_row["user"]
        user_id = int(getattr(user, "Id", 0) or 0)
        lecture = student_row["lecture"]
        lab = student_row["lab"]

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

            for checkpoint_id in checkpoint_ids_by_project.get(project_id, []):
                checkpoint_item_id = f"checkpoint-{project_id}-{checkpoint_id}"
                checkpoint_key = (project_id, checkpoint_id, user_id)
                checkpoint_submission = latest_checkpoint.get(checkpoint_key)
                checkpoint_grade = 0
                if checkpoint_submission is not None:
                    checkpoint_grade = checkpoint_grades.get(int(getattr(checkpoint_submission, "Id", 0) or 0), 0)

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


@projects_api.route('/analytics_dashboard', methods=['GET'])
@jwt_required()
@inject
def analytics_dashboard(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    class_id = parse_int(request.args.get('class_id') or request.args.get('id'), 0)
    if class_id <= 0:
        return make_response({'message': 'Missing class_id'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_class_id(class_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    modules = list(project_repo.get_modules_by_class_id(class_id) or [])
    projects = list(project_repo.get_projects_by_class_id(class_id) or [])
    project_ids = [
        int(project.Id)
        for project in projects
        if int(getattr(project, "Id", 0) or 0) > 0
    ]

    checkpoints_by_project_id = analytics_checkpoint_payloads(
        project_ids,
        project_repo,
    )

    module_by_id = {
        int(module.Id): module
        for module in modules
        if int(getattr(module, "Id", 0) or 0) > 0
    }

    project_by_module_id = {
        int(getattr(project, "ModuleId", 0) or 0): project
        for project in projects
        if int(getattr(project, "ModuleId", 0) or 0) > 0
    }

    return jsonify({
        "modules": [
            {
                "Id": int(module.Id),
                "ClassId": int(module.ClassId),
                "Name": str(module.Name or ""),
                "Start": module.Start.strftime("%x %X") if module.Start else "",
                "End": module.End.strftime("%x %X") if module.End else "",
                "MainProjectId": int(project_by_module_id[int(module.Id)].Id)
                    if int(module.Id) in project_by_module_id else None,
                "MainProjectName": str(project_by_module_id[int(module.Id)].Name or "")
                    if int(module.Id) in project_by_module_id else "",
            }
            for module in modules
        ],
        "projects": [
            {
                "Id": int(project.Id),
                "Name": str(project.Name or ""),
                "Start": module_by_id[int(getattr(project, "ModuleId", 0) or 0)].Start.strftime("%x %X")
                    if int(getattr(project, "ModuleId", 0) or 0) in module_by_id
                    and module_by_id[int(getattr(project, "ModuleId", 0) or 0)].Start
                    else "",
                "End": module_by_id[int(getattr(project, "ModuleId", 0) or 0)].End.strftime("%x %X")
                    if int(getattr(project, "ModuleId", 0) or 0) in module_by_id
                    and module_by_id[int(getattr(project, "ModuleId", 0) or 0)].End
                    else "",
                "ModuleId": getattr(project, "ModuleId", None),
            }
            for project in projects
        ],
        "checkpointsByProjectId": checkpoints_by_project_id,
        "submissionsByItemId": analytics_dashboard_progress(
            class_id,
            project_ids,
            checkpoints_by_project_id,
        ),
        "hiddenModulesByStudentId": project_repo.get_hidden_module_ids_by_student_for_class(class_id),
    })


@projects_api.route('/student_module_visibility', methods=['POST'])
@jwt_required()
@inject
def set_student_module_visibility(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get('class_id'), 0)
    student_id = parse_int(data.get('student_id'), 0)
    module_id = parse_int(data.get('module_id'), 0)
    hidden = parse_bool(data.get('hidden'))

    if class_id <= 0 or student_id <= 0 or module_id <= 0:
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_class_id(class_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    module = Modules.query.filter(Modules.Id == module_id).first()
    if not module or int(getattr(module, 'ClassId', 0) or 0) != class_id:
        return make_response({'message': 'Module not found'}, HTTPStatus.NOT_FOUND)

    assigned = ClassAssignments.query.filter(
        ClassAssignments.ClassId == class_id,
        ClassAssignments.UserId == student_id,
    ).first()
    if assigned is None:
        return make_response({'message': 'Student is not enrolled in this class'}, HTTPStatus.BAD_REQUEST)

    project_repo.set_student_module_hidden(student_id, module_id, hidden)

    return jsonify({
        'studentId': student_id,
        'moduleId': module_id,
        'hidden': bool(hidden),
    })


@projects_api.route('/module_visibility_for_all', methods=['POST'])
@jwt_required()
@inject
def set_module_visibility_for_all(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get('class_id'), 0)
    module_id = parse_int(data.get('module_id'), 0)
    hidden = parse_bool(data.get('hidden'))

    if class_id <= 0 or module_id <= 0:
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_class_id(class_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    module = Modules.query.filter(Modules.Id == module_id).first()
    if not module or int(getattr(module, 'ClassId', 0) or 0) != class_id:
        return make_response({'message': 'Module not found'}, HTTPStatus.NOT_FOUND)

    student_ids = project_repo.set_module_hidden_for_class(module_id, hidden)

    return jsonify({
        'moduleId': module_id,
        'hidden': bool(hidden),
        'studentIds': student_ids,
    })


@projects_api.route('/all_projects', methods=['GET'])
@jwt_required()
@inject
def all_projects(project_repo: ProjectRepository = Provide[Container.project_repo], submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    if not is_staff_user():
        return access_denied_response()
    data = filter_projects_for_current_user(project_repo.get_all_projects())
    new_projects = []
    thisdic = submission_repo.get_total_submission_for_all_projects()
    for proj in data:

        checkpoint_total = count_checkpoint_unique_users(int(proj.Id))

        new_projects.append(json.dumps({
            "Id": proj.Id,
            "Name": proj.Name,
            "Start": project_start(proj).strftime("%x %X") if project_start(proj) else "",
            "End": project_end(proj).strftime("%x %X") if project_end(proj) else "",
            "TotalSubmissions": int(thisdic.get(proj.Id, 0) or 0),
            "CheckpointTotalSubmissions": int(checkpoint_total),
            "CheckpointsEnabled": True,
            "ModuleId": getattr(proj, "ModuleId", None),
        }))
    return jsonify(new_projects)

@projects_api.route('/set_checkpoints_enabled', methods=['POST'])
@jwt_required()
@inject
def set_checkpoints_enabled(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    pid = parse_int(data.get("project_id", 0), 0)
    enabled = True

    if pid <= 0:
        return make_response({'message': 'Invalid project_id'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    try:
        project_repo.set_checkpoints_enabled(pid, True)
        return jsonify({'ok': True, 'enabled': True})
    except Exception:
        return make_response({'ok': False}, HTTPStatus.INTERNAL_SERVER_ERROR)

@projects_api.route('/list_checkpoints', methods=['GET'])
@jwt_required()
@inject
def list_checkpoints(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()
    pid = parse_int(request.args.get("project_id", ""), 0)
    if pid <= 0:
        return jsonify({'problems': []})
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    rows = project_repo.list_checkpoints(int(pid))
    return jsonify({
        'problems': [
            {
                'id': int(r.Id),
                'number': i + 1,
                'name': (getattr(r, "Name", "") or f"Checkpoint {i + 1}"),
                'enabled': bool(getattr(r, "Enabled", True)),
            }
            for i, r in enumerate(rows)
        ]
    })


@projects_api.route('/list_checkpoints_student', methods=['GET'])
@jwt_required()
@inject
def list_checkpoints_student(project_repo: ProjectRepository = Provide[Container.project_repo]):
    """
    Student-safe checkpoint list.
    Returns only enabled checkpoints. Checkpoints are always enabled at the project level.
    """
    project_id = parse_int(request.args.get("project_id", ""), 0)
    if project_id <= 0:
        return jsonify({'problems': []})
    if not current_user_can_access_visible_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    return jsonify({'problems': student_checkpoint_rows(project_repo, project_id)})

@projects_api.route('/create_checkpoint', methods=['POST'])
@jwt_required()
@inject
def create_checkpoint(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    pid = parse_int(data.get("project_id", 0), 0)
    name = str(data.get('name', '') or '').strip()

    if pid <= 0:
        return make_response({'message': 'Invalid project_id'}, HTTPStatus.BAD_REQUEST)
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
    except Exception as exc:
        print(f"[create_checkpoint] failed for project {pid}: {exc}", flush=True)
        return make_response({'message': 'Could not create checkpoint'}, HTTPStatus.INTERNAL_SERVER_ERROR)

    if not new_id:
        return make_response({'message': 'Could not create checkpoint'}, HTTPStatus.INTERNAL_SERVER_ERROR)

    try:
        rows = project_repo.list_checkpoints(pid)
        ordered_ids = [int(row.Id) for row in rows]
        if ordered_ids:
            project_repo.reorder_checkpoints(pid, ordered_ids)
    except Exception as exc:
        print(f"[create_checkpoint] checkpoint created, but numbering refresh failed: {exc}", flush=True)

    return jsonify({'ok': True, 'checkpoint_id': int(new_id)})

@projects_api.route('/reorder_checkpoints', methods=['POST'])
@jwt_required()
@inject
def reorder_checkpoints(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    pid = parse_int(data.get("project_id", 0), 0)
    ordered_ids_raw = data.get("ordered_ids", [])

    if pid <= 0 or not isinstance(ordered_ids_raw, list):
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    ordered_ids = [parse_int(item, 0) for item in ordered_ids_raw]
    ordered_ids = [item for item in ordered_ids if item > 0]

    try:
        rows = project_repo.reorder_checkpoints(pid, ordered_ids)
        return jsonify({
            'ok': True,
            'problems': [
                {
                    'id': int(r.Id),
                    'number': i + 1,
                    'name': (getattr(r, "Name", "") or default_checkpoint_name(i + 1)),
                    'enabled': bool(getattr(r, "Enabled", True)),
                }
                for i, r in enumerate(rows)
            ],
        })
    except Exception as exc:
        print(exc, flush=True)
        return make_response({'message': 'Could not reorder checkpoints'}, HTTPStatus.INTERNAL_SERVER_ERROR)

@projects_api.route('/delete_checkpoint', methods=['POST'])
@jwt_required()
@inject
def delete_checkpoint(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    checkpoint_id = parse_int(data.get("checkpoint_id", 0), 0)

    if checkpoint_id <= 0:
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_checkpoint_id(checkpoint_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    try:
        pp = project_repo.get_checkpoint(checkpoint_id)
        if not pp:
            return make_response({'message': 'Checkpoint not found'}, HTTPStatus.NOT_FOUND)

        project_id = int(getattr(pp, "ProjectId", 0) or 0)
        existing_rows = project_repo.list_checkpoints(project_id) if project_id > 0 else []
        if len(existing_rows) <= 1:
            return make_response(
                {'message': 'A module must have at least one checkpoint'},
                HTTPStatus.BAD_REQUEST,
            )

        deleted = project_repo.delete_checkpoint(checkpoint_id)
        if not deleted:
            return make_response({'message': 'Checkpoint not found'}, HTTPStatus.NOT_FOUND)

        remaining_rows = project_repo.list_checkpoints(project_id)
        remaining_ids = [int(row.Id) for row in remaining_rows]
        if remaining_ids:
            remaining_rows = project_repo.reorder_checkpoints(project_id, remaining_ids)
        return jsonify({'ok': True})
    except ValueError as exc:
        return make_response({'message': str(exc)}, HTTPStatus.BAD_REQUEST)
    except Exception as exc:
        print(exc, flush=True)
        return make_response({'message': 'Could not delete checkpoint'}, HTTPStatus.INTERNAL_SERVER_ERROR)

@projects_api.route('/list_solution_files', methods=['GET'])
@jwt_required()
@inject
def list_solution_files(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    pid = parse_int(request.args.get("id", ""), 0)
    ppid = opt_int(request.args.get("checkpoint_id", ""))

    if pid <= 0:
        return make_response([], HTTPStatus.OK)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    p = project_repo.get_project_path(pid, checkpoint_id=ppid)
    if not p:
        return make_response([], HTTPStatus.OK)

    try:
        if os.path.isdir(p):
            names = []
            for fn in sorted(os.listdir(p)):
                full = os.path.join(p, fn)
                if os.path.isfile(full):
                    _, ext = os.path.splitext(fn)
                    if ext.lower() in ALLOWED_SOURCE_EXTS:
                        names.append(fn)
            return make_response(names, HTTPStatus.OK)
        return make_response([os.path.basename(p)], HTTPStatus.OK)
    except Exception:
        return make_response([], HTTPStatus.OK)

@projects_api.route('/check_time_conflict', methods=['POST'])
@jwt_required()
@inject
def check_time_conflict(project_repo: ProjectRepository = Provide[Container.project_repo]):
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
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    pid = int(str(data.get('project_id', 0)) or 0)
    class_id = str(data.get('class_id', '')).strip()
    start_s = str(data.get('start_date', '')).strip()
    end_s = str(data.get('end_date', '')).strip()

    if not class_id or not start_s or not end_s:
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    if pid > 0 and not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    try:
        start_dt = datetime.fromisoformat(start_s)
        end_dt = datetime.fromisoformat(end_s)
    except ValueError:
        return make_response({'message': 'Invalid datetime format'}, HTTPStatus.BAD_REQUEST)

    conflicts = []
    try:
        current_module_id = None
        if pid > 0:
            current_project = project_repo.get_selected_project(pid)
            current_module_id = getattr(current_project, "ModuleId", None) if current_project else None

        modules = project_repo.get_modules_by_class_id(class_id)
        for module in modules:
            if current_module_id and int(getattr(module, "Id", 0) or 0) == int(current_module_id):
                continue
            p_start = getattr(module, 'Start', None)
            p_end = getattr(module, 'End', None)
            if not p_start or not p_end:
                continue
            # strict overlap: allows back-to-back intervals without conflict
            if (start_dt < p_end) and (p_start < end_dt):
                conflicts.append({
                    'id': getattr(module, 'Id', None),
                    'name': getattr(module, 'Name', ''),
                    'start': p_start.isoformat(),
                    'end': p_end.isoformat(),
                })
    except Exception:
        # Fail-safe: treat as no conflicts if repo call fails
        conflicts = []

    return jsonify({'conflict': bool(conflicts), 'conflicts': conflicts})

@projects_api.route('/run-plagiarism', methods=['POST'])
@jwt_required()
@inject
def run_plagiarism(user_repo: UserRepository = Provide[Container.user_repo], submission_repo: SubmissionRepository = Provide[Container.submission_repo], project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()
    
    input_json = request.get_json()
    projectid = input_json['project_id']
    if not user_can_access_project_id(projectid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Fetch language from projects DB and pass it through
    proj = project_repo.get_selected_project(projectid)
    language = getattr(proj, "Language", "") if proj else ""

    from src.services.dataService import run_local_plagiarism
    result = run_local_plagiarism(projectid, submission_repo, user_repo, project_repo, language=language)

    return make_response(result, HTTPStatus.OK)
    
@projects_api.route('/projects-by-user', methods=['GET'])
@jwt_required()
@inject
def get_projects_by_user(project_repo: ProjectRepository = Provide[Container.project_repo], submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    projects= project_repo.get_all_projects()
    student_submissions={}
    for project in projects:
        if project_is_hidden_for_current_student(project):
            continue

        subs = submission_repo.get_most_recent_submission_by_project(project.Id, [current_user.Id])
        class_name = project_repo.get_className_by_projectId(project.Id)
        if current_user.Id in subs: 
            sub = subs[current_user.Id]
            student_submissions[project.Name]=[sub.Id, 0, sub.Time.strftime("%x %X"), class_name, str(project.ClassId)]
    return jsonify(student_submissions)

@projects_api.route('/past-submissions', methods=['GET'])
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
    uid = int(getattr(current_user, "Id", 0) or 0)
    if uid <= 0:
        return jsonify([])

    # Projects where this student has ANY submissions (main or checkpoint)
    proj_ids = [
        int(r[0])
        for r in (
            db.session.query(Submissions.Project)
            .filter(Submissions.User == uid)
            .distinct()
            .all()
        )
        if r and r[0] is not None
    ]
    if not proj_ids:
        return jsonify([])

    projects = (
        Projects.query
        .outerjoin(Modules, Projects.ModuleId == Modules.Id)
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
        if not proj_ids:
            return jsonify([])

    class_ids = {int(getattr(p, "ClassId", 0) or 0) for p in (projects or [])}
    class_ids.discard(0)
    class_name_by_id = {}
    if class_ids:
        for c in Classes.query.filter(Classes.Id.in_(list(class_ids))).all():
            class_name_by_id[int(getattr(c, "Id", 0) or 0)] = str(getattr(c, "Name", "") or "")

    def iso(val):
        if val is None:
            return ""
        try:
            return val.isoformat()
        except Exception:
            return str(val)

    # Most recent MAIN submission per project
    main_by_project = {}
    main_rows = (
        Submissions.query
        .filter(
            Submissions.User == uid,
            Submissions.Project.in_(proj_ids),
            Submissions.IsCheckpoint == False,
        )
        .order_by(Submissions.Project.asc(), Submissions.Time.desc())
        .all()
    )
    for s in (main_rows or []):
        pid = int(getattr(s, "Project", 0) or 0)
        if pid and pid not in main_by_project:
            main_by_project[pid] = s

    # Most recent PRACTICE submission per (project, checkpoint_id)
    latest_checkpoint = {}
    pp_ids = set()
    checkpoint_rows = (
        Submissions.query
        .filter(
            Submissions.User == uid,
            Submissions.Project.in_(proj_ids),
            Submissions.IsCheckpoint == True,
        )
        .filter(Submissions.CheckpointId.isnot(None))
        .order_by(Submissions.Project.asc(), Submissions.CheckpointId.asc(), Submissions.Time.desc())
        .all()
    )
    for s in (checkpoint_rows or []):
        pid = int(getattr(s, "Project", 0) or 0)
        ppid = getattr(s, "CheckpointId", None)
        if not pid or ppid is None:
            continue
        ppid_int = int(ppid)
        key = (pid, ppid_int)
        if key not in latest_checkpoint:
            latest_checkpoint[key] = s
            pp_ids.add(ppid_int)

    pp_map = {}
    if pp_ids:
        for pp in Checkpoints.query.filter(Checkpoints.Id.in_(list(pp_ids))).all():
            pp_map[int(getattr(pp, "Id", 0) or 0)] = pp

    checkpoint_number_by_project_and_id = {}
    for pid in proj_ids:
        try:
            rows = (
                Checkpoints.query
                .filter(
                    Checkpoints.ProjectId == int(pid),
                    Checkpoints.Enabled == True,
                )
                .order_by(Checkpoints.CheckpointNumber.asc(), Checkpoints.Id.asc())
                .all()
            )
            checkpoint_number_by_project_and_id[int(pid)] = {
                int(getattr(row, "Id", 0) or 0): index
                for index, row in enumerate(rows or [], start=1)
            }
        except Exception:
            checkpoint_number_by_project_and_id[int(pid)] = {}

    checkpoints_by_project = defaultdict(list)
    for (pid, ppid), s in latest_checkpoint.items():
        pp = pp_map.get(ppid)
        number = int(checkpoint_number_by_project_and_id.get(pid, {}).get(ppid, 0) or 0)
        name = str(getattr(pp, "Name", "") or "") if pp else ""
        if not name:
            name = f"Checkpoint {number}" if number else "Checkpoint"
        checkpoints_by_project[pid].append({
            "checkpointId": int(ppid),
            "number": int(number),
            "name": name,
            "submissionId": int(getattr(s, "Id", 0) or 0),
            "time": iso(getattr(s, "Time", "")),
            "passed": bool(getattr(s, "IsPassing", False)),
        })
    for pid in checkpoints_by_project:
        checkpoints_by_project[pid].sort(
            key=lambda x: (int(x.get("number", 0) or 0), int(x.get("checkpointId", 0) or 0))
        )

    out = []
    for p in (projects or []):
        pid = int(getattr(p, "Id", 0) or 0)
        cid = int(getattr(p, "ClassId", 0) or 0)
        main_s = main_by_project.get(pid)
        out.append({
            "projectId": pid,
            "projectName": str(getattr(p, "Name", "") or ""),
            "classId": str(cid),
            "className": class_name_by_id.get(cid, ""),
            "start": iso(project_start(p)),
            "end": iso(project_end(p)),
            "main": None if not main_s else {
                "submissionId": int(getattr(main_s, "Id", 0) or 0),
                "time": iso(getattr(main_s, "Time", "")),
                "passed": bool(getattr(main_s, "IsPassing", False)),
            },
            "checkpoints": checkpoints_by_project.get(pid, []),
        })

    return jsonify(out)

@projects_api.route('/create_project', methods=['POST'])
@jwt_required()
@inject
def create_project(project_repo: ProjectRepository = Provide[Container.project_repo]):

    def ts_str() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def safe_name(s: str) -> str:
        # normalize and remove unsafe chars; also collapse spaces
        return secure_filename(s or "").replace(" ", "_")

    if not is_staff_user():
        return access_denied_response()

    # Validate solution files (multi-file)
    solution_uploads = request.files.getlist('solutionFiles')
    solution_uploads = [f for f in solution_uploads if f and f.filename]
    if not solution_uploads:
        return make_response({'message': 'No selected solution files'}, HTTPStatus.BAD_REQUEST)
    if 'assignmentdesc' not in request.files or not request.files['assignmentdesc'].filename:
        return make_response({'message': 'No assignment description file'}, HTTPStatus.BAD_REQUEST)

    # Read form
    name = request.form.get('name', '')
    language = request.form.get('language', '')
    class_id = request.form.get('class_id', '')
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    checkpoint_enabled = True
    module_id = request.form.get('module_id', '').strip()

    if name == '' or language == '':
        return make_response("Error in form", HTTPStatus.BAD_REQUEST)

    ts = ts_str()
    module_id_int = int(module_id) if module_id.isdigit() else None
    proj_dir_path = teacher_main_project_dir_for_new(
        int(class_id),
        module_id_int,
        name,
        ts,
    )
    os.makedirs(proj_dir_path, exist_ok=True)

    # Save solution + description + additional into a timestamped version directory.
    # The module folder uses the first module creation timestamp, not the current project name.
    path = version_dir(proj_dir_path, ts)
    os.makedirs(path, exist_ok=True)
    for up in solution_uploads:
        orig = safe_name(up.filename)
        ext = os.path.splitext(orig)[1].lower()
        if ext not in ALLOWED_SOURCE_EXTS:
            return make_response({'message': f'Unsupported file type: {ext}'}, HTTPStatus.BAD_REQUEST)
        dst = os.path.join(path, orig)
        up.save(dst)

    ad = request.files['assignmentdesc']
    ad_name = safe_name(ad.filename or "assignment.pdf")
    assignmentdesc_path = os.path.join(path, ad_name)
    ad.save(assignmentdesc_path)

    # Multiple additional files: save into version folder, but store only basenames in DB (short)
    add_names = []
    for add_up in request.files.getlist('additionalFiles'):
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
        module_id_int
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

@projects_api.route('/edit_project', methods=['POST'])
@jwt_required()
@inject
def edit_project(project_repo: ProjectRepository = Provide[Container.project_repo]):

    def ts_str() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def safe_name(s: str) -> str:
        return secure_filename(s or "").replace(" ", "_")

    if not is_staff_user():
        return access_denied_response()

    pid_str = request.form.get("id", "").strip()
    if not pid_str.isdigit():
        return make_response({'message': 'Invalid or missing project id'}, HTTPStatus.BAD_REQUEST)
    pid = int(pid_str)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    name = request.form.get('name', '')
    language = request.form.get('language', '')
    checkpoint_enabled = True
    module_id = request.form.get('module_id', '').strip()

    if name == '' or language == '':
        return make_response("Error in form", HTTPStatus.BAD_REQUEST)

    ts = ts_str()
    existing_path = project_repo.get_project_path(pid)
    existing_proj = project_repo.get_selected_project(pid)
    if not existing_proj:
        return make_response({'message': 'Project not found'}, HTTPStatus.NOT_FOUND)

    # Preserve the original filesystem project name even if the display name changes.
    proj_dir = teacher_main_project_dir(existing_proj, timestamp_hint=ts)
    os.makedirs(proj_dir, exist_ok=True)

    # Default to existing paths if no new files are uploaded
    path = existing_path
    assignmentdesc_path = project_repo.get_project_desc_path(pid)
    add_path = getattr(existing_proj, "AdditionalFilePath", "") if existing_proj else ""

    # Determine whether we need to mint a new version directory
    solution_uploads = request.files.getlist('solutionFiles')
    solution_uploads = [f for f in solution_uploads if f and f.filename]
    solution_changed = False
    ad = request.files.get('assignmentdesc')
    desc_changed = bool(ad and ad.filename)
    remove_add = request.form.get('removeAdditionalFiles', '').strip()
    clear_add = (request.form.get('clearAdditionalFiles', '').strip().lower() == 'true')
    new_add_uploads = [f for f in request.files.getlist('additionalFiles') if f and f.filename]
    try:
        to_remove = json.loads(remove_add) if remove_add else []
    except Exception:
        to_remove = []
    additional_ops = bool(clear_add or to_remove or new_add_uploads)
    needs_new_version = bool(solution_uploads or desc_changed or additional_ops)

    # Seed a new version folder so history is preserved and teacher layout is consistent
    current_version_dir = existing_path if (existing_path and os.path.isdir(existing_path) and is_ts_dir(os.path.basename(existing_path))) else None
    if needs_new_version:
        new_version = version_dir(proj_dir, ts)
        # Seed from current version directory when available; otherwise seed from legacy paths
        try:
            seed_add_paths = []
            existing_add = getattr(existing_proj, "AdditionalFilePath", "") if existing_proj else ""
            existing_list = json.loads(existing_add) if (existing_add or "").startswith('[') else ([existing_add] if existing_add else [])
            # existing_list may be basenames or absolute paths. Resolve for seeding when not using seed_from_dir.
            legacy_base = None
            if existing_path:
                legacy_base = existing_path if os.path.isdir(existing_path) else os.path.dirname(existing_path)
            legacy_base = legacy_base or proj_dir
            for p in (existing_list or []):
                if not p:
                    continue
                if os.path.isabs(p):
                    seed_add_paths.append(p)
                else:
                    seed_add_paths.append(os.path.join(legacy_base, os.path.basename(p)))
        except Exception:
            seed_add_paths = []
        seed_version_dir(
            new_version,
            seed_from_dir=current_version_dir,
            seed_solution_path=existing_path,
            seed_desc_path=assignmentdesc_path,
            seed_add_paths=seed_add_paths,
        )
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
            for fn in os.listdir(path):
                full = os.path.join(path, fn)
                if os.path.isfile(full) and os.path.splitext(fn)[1].lower() in ALLOWED_SOURCE_EXTS:
                    os.remove(full)
        except Exception:
            pass
        for up in solution_uploads:
            orig = safe_name(up.filename)
            ext = os.path.splitext(orig)[1].lower()
            if ext not in ALLOWED_SOURCE_EXTS:
                return make_response({'message': f'Unsupported file type: {ext}'}, HTTPStatus.BAD_REQUEST)
            dst = os.path.join(path, orig)
            up.save(dst)
        solution_changed = True

    # If a new assignment description was uploaded, save into the version folder
    ad = request.files.get('assignmentdesc')
    if ad and ad.filename:
        ad_name = safe_name(ad.filename or "assignment.pdf")
        assignmentdesc_path = os.path.join(path, ad_name)
        ad.save(assignmentdesc_path)

    # Multiple additional files: store only basenames in DB; operate on files inside `path`.
    existing_add = getattr(existing_proj, "AdditionalFilePath", "") if existing_proj else ""
    try:
        add_names = json.loads(existing_add) if (existing_add or "").startswith('[') else ([existing_add] if existing_add else [])
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
        for n in add_names:
            if n in to_remove:
                try:
                    os.remove(os.path.join(path, n))
                except Exception:
                    pass
                additional_file_changed = True
            else:
                keep.append(n)
        add_names = keep
    # Clear all
    if clear_add and add_names:
        for n in add_names:
            try:
                os.remove(os.path.join(path, n))
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
        name, language, pid,
        path, assignmentdesc_path, json.dumps(add_names), checkpoint_enabled
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

def has_allowed_ext(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in ALLOWED_SOURCE_EXTS

def run_solution_for_input(solution_root: str, language: str, input_text: str, project_id: int, class_id: int, additional_file_path: str = "") -> str:
    """
    Execute code strictly via /tabot-files/grading-scripts/grade.py (ADMIN path).
    Returns stdout (or stderr) with normalized newlines, or "" on failure.
    """
    if not solution_root or not os.path.exists(solution_root):
        return ""
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
        abs_list = []
        for p in (lst or []):
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
        "python", script,
        "ADMIN",              # student_name triggers admin path
        normalize_grader_language(language, solution_root), # language as grade.py expects
        input_text or "",     # goes to admin_run(user_input)
        solution_root,        # file or directory
        add_arg,
        str(project_id or 0),
        str(class_id or 0),
    ]
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=os.path.dirname(solution_root) if os.path.isfile(solution_root) else solution_root)
    except Exception:
        return ""
    out = (proc.stdout or "").replace("\r\n", "\n").replace("\r", "\n")
    err = (proc.stderr or "").strip()
    return (out or err)

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
    except Exception:
        pass

    grading_dir = "/tabot-files/grading-scripts"
    grading_path = os.path.join(grading_dir, "grade.py")

    spec = importlib.util.spec_from_file_location("tabot-files", grading_path)
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
        except ValueError:
            pass

try:
    TABOT = load_tabot_module()
except Exception as e:
    TABOT = None
    print(f"[projects] Warning: tabot import failed (will use subprocess path): {e}", flush=True)

def recompute_expected_outputs(project_repo, project_id, *, solution_override_path: str = None, language_override: str = None, checkpoint_id: int | None = None):

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
        lang = (language_override or (getattr(proj_obj, "Language", "") if proj_obj else "")).strip()
    else:
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

    for tc_id, vals in cases.items():
        if checkpoint_id:
            pp = project_repo.get_checkpoint(int(checkpoint_id))
            add_path = getattr(pp, "AdditionalFilePath", "") if pp else ""
        else:
            add_path = getattr(proj_obj, "AdditionalFilePath", "") if proj_obj else ""
        try:
            name = vals[1] if len(vals) > 1 else ""
            inp = vals[2] if len(vals) > 2 else ""
            hidden = bool(vals[4]) if len(vals) > 4 else False
            sort_order = int(vals[5]) if len(vals) > 5 else None
        except Exception:
            name, inp, hidden, sort_order = "", "", False, None

        new_out = run_solution_for_input(solution_root, lang, inp, project_id, class_id, add_path)
        try:
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
            )
        except Exception:
            # continue on individual failures
            continue

@projects_api.route('/list_source_files', methods=['GET'])
@jwt_required()
@inject
def list_source_files(project_repo: ProjectRepository = Provide[Container.project_repo]):
    """Return list of previewable source files for a project (relative paths if a directory)."""
    if not is_staff_user():
        return access_denied_response()

    pid = parse_int(request.args.get("project_id", ""), 0)
    if pid <= 0:
        return make_response({'message': 'Missing project_id'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    ppid = opt_int(request.args.get("checkpoint_id", ""))
    root = project_repo.get_project_path(int(pid), checkpoint_id=ppid)

    if not root or not os.path.exists(root):
        return jsonify({'files': []})

    files = []
    if os.path.isdir(root):
        for base, _, fnames in os.walk(root):
            for fname in fnames:
                full = os.path.join(base, fname)
                if has_allowed_ext(full):
                    rel = os.path.relpath(full, root).replace("\\", "/")
                    files.append({'relpath': rel, 'bytes': os.path.getsize(full)})
    else:
        if has_allowed_ext(root):
            files.append({'relpath': os.path.basename(root), 'bytes': os.path.getsize(root)})

    return jsonify({'files': files})


@projects_api.route('/get_source_file', methods=['GET'])
@jwt_required()
@inject
def get_source_file(project_repo: ProjectRepository = Provide[Container.project_repo]):
    """Return the text content of a source file for preview."""
    if not is_staff_user():
        return access_denied_response()

    pid = parse_int(request.args.get("project_id", ""), 0)
    relpath = request.args.get('relpath', '')
    if pid <= 0:
        return make_response({'message': 'Missing project_id'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    ppid_raw = (request.args.get('checkpoint_id', '') or '').strip()
    ppid = int(ppid_raw) if ppid_raw.isdigit() else None
    root = project_repo.get_project_path(int(pid), checkpoint_id=ppid)

    if not root or not os.path.exists(root):
        return make_response({'message': 'Project path not found'}, HTTPStatus.NOT_FOUND)

    # Resolve full path safely using os.path only
    if os.path.isdir(root):
        candidate = os.path.normpath(os.path.join(root, relpath))
        root_abs = os.path.abspath(root)
        cand_abs = os.path.abspath(candidate)
        if not (cand_abs == root_abs or cand_abs.startswith(root_abs + os.sep)):
            return make_response({'message': 'Invalid path'}, HTTPStatus.BAD_REQUEST)
        full = cand_abs
    else:
        # Single-file project: only that file is allowed
        if relpath and relpath != os.path.basename(root):
            return make_response({'message': 'Invalid path for single-file project'}, HTTPStatus.BAD_REQUEST)
        full = root

    if not os.path.exists(full):
        return make_response({'message': 'File not found'}, HTTPStatus.NOT_FOUND)
    if not has_allowed_ext(full):
        return make_response({'message': 'Unsupported file type'}, HTTPStatus.BAD_REQUEST)

    # Limit preview size to 2 MB
    if os.path.getsize(full) > 2 * 1024 * 1024:
        return make_response({'message': 'File too large to preview'}, HTTPStatus.BAD_REQUEST)

    with open(full, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()

    resp = make_response(text, HTTPStatus.OK)
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Cache-Control'] = 'no-store'
    return resp

@projects_api.route('/get_project_id', methods=['GET'])
@jwt_required()
@inject
def get_project(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    pid_raw = (request.args.get('id') or '').strip()
    if not pid_raw.isdigit():
        return make_response(json.dumps({}), HTTPStatus.OK)
    pid = int(pid_raw)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    ppid = opt_int(request.args.get("checkpoint_id", ""))
    project_info = project_repo.get_project(pid, checkpoint_id=ppid)

    return make_response(json.dumps(project_info), HTTPStatus.OK)
    
@projects_api.route('/get_testcases', methods=['GET'])
@jwt_required()
@inject
def get_testcases(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    project_id = parse_int(request.args.get("id", ""), 0)
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    ppid = opt_int(request.args.get("checkpoint_id", ""))
    testcases = project_repo.get_testcases(int(project_id), checkpoint_id=ppid)

    # Older expected outputs predate terminal-style input events. Refresh them
    # once when Project Manage first encounters an input-reading Python/Java
    # solution so existing projects receive the same display as new projects.
    has_input_event = any(
        isinstance(values, (list, tuple))
        and len(values) > 3
        and INPUT_EVENT_PREFIX in str(values[3] or "")
        for values in testcases.values()
    )
    if testcases and not has_input_event:
        try:
            solution_root = project_repo.get_project_path(int(project_id), checkpoint_id=ppid)
            project = project_repo.get_selected_project(int(project_id))
            if ppid:
                checkpoint = project_repo.get_checkpoint(int(ppid))
                language = (
                    getattr(checkpoint, "Language", "")
                    or getattr(project, "Language", "")
                    or ""
                )
            else:
                language = getattr(project, "Language", "") or ""

            if solution_source_uses_input(solution_root, language):
                recompute_expected_outputs(
                    project_repo,
                    int(project_id),
                    solution_override_path=solution_root,
                    language_override=language,
                    checkpoint_id=ppid,
                )
                testcases = project_repo.get_testcases(int(project_id), checkpoint_id=ppid)
        except Exception as exc:
            print(f"[get_testcases] legacy input transcript refresh failed: {exc}", flush=True)
 
    return make_response(json.dumps(list(testcases.values())), HTTPStatus.OK)


@projects_api.route('/count_testcases', methods=['GET'])
@jwt_required()
@inject
def count_testcases(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    project_id = parse_int(request.args.get("id", ""), 0)
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    ppid = opt_int(request.args.get("checkpoint_id", ""))
    count = project_repo.count_testcases(int(project_id), checkpoint_id=ppid)
    return jsonify({"count": int(count)})


@projects_api.route('/json_add_testcases', methods=['POST'])
@jwt_required()
@inject   
def json_add_testcases(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    file = request.files['file']
    project_id = request.form["project_id"]
    if not user_can_access_project_id(parse_int(project_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    ppid = opt_int(request.form.get("checkpoint_id", ""))

    # Require a solution root for whichever scope we're writing testcases into (main or checkpoint)
    sol = project_repo.get_project_path(int(project_id), checkpoint_id=ppid)
    if not sol:
        msg = 'Checkpoint has no solution files' if ppid else 'Assignment has no solution files'
        return make_response({'message': msg}, HTTPStatus.BAD_REQUEST)

    try:
        proj = project_repo.get_selected_project(int(project_id))
        class_id = int(getattr(proj, "ClassId", 0) or 0)
    except Exception:
        class_id = 0

    try:
        json_obj = json.load(file)
    except json.JSONDecodeError:
         message = {
            'message': 'Incorrect JSON format'
        }
         return make_response(message, HTTPStatus.INTERNAL_SERVER_ERROR)
    else:
        if not isinstance(json_obj, list):
            return make_response(
                {'message': 'Testcase JSON must be an array'},
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
        for _, testcase in ordered_testcases:
            if not isinstance(testcase, dict):
                return make_response(
                    {'message': 'Each testcase must be an object'},
                    HTTPStatus.BAD_REQUEST,
                )

            name = str(testcase.get("name", "") or "").strip()
            input_data = str(testcase.get("input", "") or "")
            if not name or input_data == "":
                return make_response(
                    {'message': 'Each testcase requires a name and input'},
                    HTTPStatus.BAD_REQUEST,
                )
            validated_testcases.append({
                "name": name,
                "input": input_data,
                "output": str(testcase.get("output", "") or ""),
                "hidden": parse_bool(testcase.get("hidden", False)),
            })

        project = project_repo.get_selected_project(int(project_id))
        language = str(getattr(project, "Language", "") or "")
        if ppid:
            checkpoint = project_repo.get_checkpoint(int(ppid))
            additional_path = getattr(checkpoint, "AdditionalFilePath", "") if checkpoint else ""
        else:
            additional_path = getattr(project, "AdditionalFilePath", "") if project else ""

        for testcase in validated_testcases:
            testcase["output"] = run_solution_for_input(
                sol,
                language,
                testcase["input"],
                int(project_id),
                int(class_id),
                additional_path,
            )

        existing_count = project_repo.count_testcases(
            int(project_id),
            checkpoint_id=ppid,
        )
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

@projects_api.route('/add_or_update_testcase', methods=['POST'])
@jwt_required()
@inject   
def add_or_update_testcase(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    # Grab all fields safely (defaults prevent NameError)
    id_val = request.form.get('id', '').strip()
    name = request.form.get('name', '').strip()
    input_data = request.form.get('input', '')
    output = request.form.get('output', '')
    project_id = request.form.get('project_id', '').strip()
    class_id = request.form.get('class_id', '').strip()
    sort_order_raw = request.form.get('order', '').strip()

    ppid_raw = request.form.get('checkpoint_id', '').strip()
    
    if id_val == '' or name == '' or input_data == '' or project_id == '' or class_id == '':
        return make_response("Error in form", HTTPStatus.BAD_REQUEST)    

    # Coerce types with validation
    try:
        project_id = int(project_id)
        id_val = int(id_val)
        class_id_int = int(class_id)
    except ValueError:
        return make_response("Invalid numeric id", HTTPStatus.BAD_REQUEST)

    hidden = parse_bool(request.form.get("hidden", ""))
    sort_order = int(sort_order_raw) if sort_order_raw.isdigit() and int(sort_order_raw) > 0 else None
    checkpoint_id = int(ppid_raw) if (ppid_raw or "").isdigit() else None
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    if not user_can_access_class_id(class_id_int):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Do not allow testcase edits/creates unless solution files exist (main or checkpoint)
    sol = project_repo.get_project_path(int(project_id), checkpoint_id=checkpoint_id)
    if not sol:
        return make_response(
            "Checkpoint has no solution files" if checkpoint_id else "Assignment has no solution files",
            HTTPStatus.BAD_REQUEST
        )

    # Auto-recompute expected output when editing a testcase.
    # If the project's language is Python, run the saved solution with the new input
    # and overwrite the provided `output` with the program's stdout.
    try:
        project = project_repo.get_selected_project(int(project_id))
        language = (getattr(project, "Language", "") or "")
        # If this testcase is PRACTICE, use checkpoint solution/desc/additional
        if checkpoint_id:
            solution_root = project_repo.get_project_path(int(project_id), checkpoint_id=checkpoint_id)
            pp = project_repo.get_checkpoint(int(checkpoint_id))
            add_path = getattr(pp, "AdditionalFilePath", "") if pp else ""
        else:
            solution_root = (getattr(project, "solutionpath", "") or "")
            add_path = getattr(project, "AdditionalFilePath", "") if project else ""
        output = run_solution_for_input(solution_root, language, input_data, int(project_id), int(class_id_int), add_path)

    except Exception:
        # Fall back to the submitted output if recomputation fails
        pass

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

    return make_response("Testcase Added", HTTPStatus.OK)


@projects_api.route('/reorder_testcases', methods=['POST'])
@jwt_required()
@inject
def reorder_testcases(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    project_id = parse_int(request.form.get("project_id", ""), 0)
    if project_id <= 0 or not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    checkpoint_id = opt_int(request.form.get("checkpoint_id", ""))
    try:
        testcase_ids = json.loads(request.form.get("testcase_ids", "[]"))
        if not isinstance(testcase_ids, list):
            raise ValueError("testcase_ids must be an array")
        ordered_ids = [int(testcase_id) for testcase_id in testcase_ids]
        project_repo.reorder_testcases(
            project_id,
            ordered_ids,
            checkpoint_id=checkpoint_id,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return make_response({"message": str(exc)}, HTTPStatus.BAD_REQUEST)

    return make_response("Testcase order updated", HTTPStatus.OK)

@projects_api.route('/remove_testcase', methods=['POST'])
@jwt_required()
@inject
def remove_testcase(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    if 'id' in request.form:
        id_val = request.form['id']
    else:
        return make_response({'message': 'Missing testcase id'}, HTTPStatus.BAD_REQUEST)
    testcase = project_repo.get_testcase(parse_int(id_val, 0)) if hasattr(project_repo, 'get_testcase') else None
    if testcase is not None and not user_can_access_project_id(int(getattr(testcase, 'ProjectId', 0) or 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    project_repo.remove_testcase(id_val)
    return make_response("Testcase Removed", HTTPStatus.OK)

@projects_api.route('/default_content', methods=['GET', 'POST'])
@jwt_required()
@inject
def default_content(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()
    data = request.get_json(silent=True) if request.method == 'POST' else None
    if request.method == 'POST' and not isinstance(data, dict):
        return make_response({'message': 'Expected a JSON object'}, HTTPStatus.BAD_REQUEST)
    class_id = parse_int(data.get('class_id') if data else request.args.get('class_id'), 0)
    if not user_can_access_class_id(class_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    try:
        if request.method == 'POST':
            return jsonify(project_repo.import_default_content(class_id, data.get('selected')))
        return jsonify(project_repo.default_content_options(class_id))
    except PermissionError as exc:
        return make_response({'message': str(exc)}, HTTPStatus.FORBIDDEN)
    except (ValueError, OSError) as exc:
        return make_response({'message': str(exc)}, HTTPStatus.BAD_REQUEST)
    except Exception:
        from flask import current_app
        current_app.logger.exception('Default content operation failed for class %s', class_id)
        return make_response({'message': 'Import failed. Refresh the catalog before retrying.'}, HTTPStatus.INTERNAL_SERVER_ERROR)


@projects_api.route('/get_modules_by_class_id', methods=['GET'])
@jwt_required()
@inject
def get_modules_by_class_id(project_repo: ProjectRepository = Provide[Container.project_repo], submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    if not is_staff_user():
        return access_denied_response()

    class_id = request.args.get('id')
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    modules = project_repo.get_modules_by_class_id(class_id)
    module_projects = [project_repo.get_main_project_for_module(int(module.Id)) for module in modules]
    project_ids = [int(project.Id) for project in module_projects if project is not None]
    total_submission_counts = submission_repo.get_total_submission_for_all_projects()
    checkpoint_total_counts = checkpoint_unique_user_counts(project_ids)
    main_completed_ids = main_completed_project_ids(project_ids)

    return jsonify([
        module_payload(
            module,
            project_repo,
            submission_repo,
            total_submission_counts=total_submission_counts,
            checkpoint_total_counts=checkpoint_total_counts,
            main_completed_project_ids=main_completed_ids,
        )
        for module in modules
    ])

@projects_api.route('/create_module', methods=['POST'])
@jwt_required()
@inject
def create_module(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    name = str(data.get('name', '')).strip()
    class_id = str(data.get('class_id', '')).strip()
    start_date = str(data.get('start_date', '')).strip()
    end_date = str(data.get('end_date', '')).strip()

    if not name or not class_id or not start_date or not end_date:
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    try:
        module_id = project_repo.create_module(
            int(class_id),
            name,
            datetime.fromisoformat(start_date),
            datetime.fromisoformat(end_date),
        )
    except Exception as exc:
        return make_response({'message': f'Could not create module: {exc}'}, HTTPStatus.BAD_REQUEST)

    default_checkpoint_id = ensure_default_checkpoint_for_module(
        project_repo,
        int(module_id),
        context="create_module",
    )

    return jsonify({
        'module_id': int(module_id),
        'checkpoint_id': int(default_checkpoint_id) if default_checkpoint_id else None,
    })

@projects_api.route('/get_modules_by_class_id_student', methods=['GET'])
@jwt_required()
@inject
def get_modules_by_class_id_student(project_repo: ProjectRepository = Provide[Container.project_repo], submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    class_id = parse_int(request.args.get('id'), 0)

    if class_id <= 0:
        return jsonify([])

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

    module_projects = [project_repo.get_main_project_for_module(int(module.Id)) for module in modules]
    project_ids = [int(project.Id) for project in module_projects if project is not None]
    total_submission_counts = submission_repo.get_total_submission_for_all_projects()
    checkpoint_total_counts = checkpoint_unique_user_counts(project_ids)
    main_completed_ids = main_completed_project_ids(project_ids)

    return jsonify([
        module_payload(
            module,
            project_repo,
            submission_repo,
            total_submission_counts=total_submission_counts,
            checkpoint_total_counts=checkpoint_total_counts,
            main_completed_project_ids=main_completed_ids,
        )
        for module in modules
    ])

@projects_api.route('/get_module_overview_student', methods=['GET'])
@jwt_required()
@inject
def get_module_overview_student(project_repo: ProjectRepository = Provide[Container.project_repo], submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    module_id = parse_int(request.args.get('module_id', 0), 0)
    if module_id <= 0:
        return make_response({'message': 'Module not found'}, HTTPStatus.NOT_FOUND)

    module = project_repo.get_module(module_id)
    if not module:
        return make_response({'message': 'Module not found'}, HTTPStatus.NOT_FOUND)
    if not current_user_can_access_visible_module_id(module_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    project = project_repo.get_main_project_for_module(int(module.Id))
    checkpoint_rows = student_checkpoint_rows(project_repo, int(project.Id)) if project else []

    return jsonify({
        "module": module_payload(module, project_repo, submission_repo),
        "checkpoints": checkpoint_rows,
        "practiceProblems": checkpoint_rows,
        "incentives": incentive_summary(int(current_user.Id), int(module.ClassId)),
    })


@projects_api.route('/skip_checkpoint', methods=['POST'])
@jwt_required()
def skip_checkpoint():
    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id", 0), 0)
    project_id = parse_int(data.get("project_id", 0), 0)
    checkpoint_id = parse_int(data.get("checkpoint_id", 0), 0)
    user_id = int(getattr(current_user, "Id", 0) or 0)

    if class_id <= 0 or project_id <= 0 or checkpoint_id <= 0:
        return make_response({"message": "class_id, project_id, and checkpoint_id are required."}, HTTPStatus.BAD_REQUEST)

    project = Projects.query.filter(Projects.Id == project_id).first()
    if project is None or parse_int(getattr(project, "ClassId", 0), 0) != class_id:
        return make_response({"message": "Project not found."}, HTTPStatus.NOT_FOUND)

    if not current_user_can_access_visible_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    checkpoint = Checkpoints.query.filter(
        Checkpoints.Id == checkpoint_id,
        Checkpoints.ProjectId == project_id,
        Checkpoints.Enabled == True,
    ).first()
    if checkpoint is None:
        return make_response({"message": "Checkpoint not found."}, HTTPStatus.NOT_FOUND)

    checkpoint_rows = student_checkpoint_rows(ProjectRepository(), project_id)
    target_index = next((i for i, row in enumerate(checkpoint_rows) if parse_int(row.get("id"), 0) == checkpoint_id), -1)

    if target_index < 0:
        return make_response({"message": "Checkpoint is not available."}, HTTPStatus.NOT_FOUND)

    target_row = checkpoint_rows[target_index]
    if bool(target_row.get("solved")):
        return jsonify({
            "message": "Checkpoint is already completed.",
            "checkpoints": checkpoint_rows,
            "incentives": incentive_summary(user_id, class_id),
        })

    earlier_incomplete = [
        row for row in checkpoint_rows[:target_index]
        if not bool(row.get("solved"))
    ]
    if earlier_incomplete:
        return make_response({"message": "Complete or skip earlier checkpoints first."}, HTTPStatus.BAD_REQUEST)


    # Lock the user row so simultaneous purchases cannot overspend a derived balance.
    locked_user = Users.query.filter(Users.Id == user_id).with_for_update().first()
    if locked_user is None:
        db.session.rollback()
        return make_response({"message": "User not found."}, HTTPStatus.NOT_FOUND)

    existing_skip = StudentCheckpointSkips.query.filter(
        StudentCheckpointSkips.UserId == user_id,
        StudentCheckpointSkips.ClassId == class_id,
        StudentCheckpointSkips.ProjectId == project_id,
        StudentCheckpointSkips.CheckpointId == checkpoint_id,
    ).first()
    if existing_skip is not None:
        db.session.rollback()
        return jsonify({
            "message": "Checkpoint already skipped.",
            "checkpoints": student_checkpoint_rows(ProjectRepository(), project_id),
            "incentives": incentive_summary(user_id, class_id),
        })

    balance = get_star_balance(user_id, class_id)
    if balance < CHECKPOINT_SKIP_COST_STARS:
        db.session.rollback()
        return make_response({
            "message": f"You need {CHECKPOINT_SKIP_COST_STARS} stars to skip a checkpoint.",
            "stars": balance,
            "required_stars": CHECKPOINT_SKIP_COST_STARS,
        }, HTTPStatus.BAD_REQUEST)

    db.session.add(StudentCheckpointSkips(
        UserId=user_id,
        ClassId=class_id,
        ProjectId=project_id,
        CheckpointId=checkpoint_id,
        SpentStars=CHECKPOINT_SKIP_COST_STARS,
        CreatedAt=datetime.now(),
    ))
    db.session.commit()

    updated_rows = student_checkpoint_rows(ProjectRepository(), project_id)

    return jsonify({
        "message": "Checkpoint skipped.",
        "checkpoints": updated_rows,
        "practiceProblems": updated_rows,
        "incentives": incentive_summary(user_id, class_id),
    })

@projects_api.route('/update_module', methods=['POST'])
@jwt_required()
@inject
def update_module(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    module_id = int(str(data.get('module_id', 0)) or 0)
    name = str(data.get('name', '')).strip()
    start_date = str(data.get('start_date', '')).strip()
    end_date = str(data.get('end_date', '')).strip()

    if module_id <= 0 or not name or not start_date or not end_date:
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_module_id(module_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    module = project_repo.update_module(
        module_id,
        name,
        datetime.fromisoformat(start_date),
        datetime.fromisoformat(end_date),
    )

    if not module:
        return make_response({'message': 'Module not found'}, HTTPStatus.NOT_FOUND)

    return jsonify({'message': 'Module updated'})


def requested_presentation_module():
    module_id = parse_int(
        request.form.get('module_id')
        if request.method == 'POST'
        else request.args.get('module_id'),
        0,
    )
    project_id = parse_int(
        request.form.get('project_id')
        if request.method == 'POST'
        else request.args.get('project_id'),
        0,
    )

    module = Modules.query.filter(Modules.Id == module_id).first() if module_id > 0 else None
    if module is None and project_id > 0:
        project = Projects.query.filter(Projects.Id == project_id).first()
        resolved_module_id = parse_int(getattr(project, 'ModuleId', 0) if project else 0, 0)
        module = (
            Modules.query.filter(Modules.Id == resolved_module_id).first()
            if resolved_module_id > 0
            else None
        )

    return module


@projects_api.route('/module_presentation', methods=['GET'])
@jwt_required()
def get_module_presentation():
    module = requested_presentation_module()
    if module is None:
        return make_response({'message': 'Module not found'}, HTTPStatus.NOT_FOUND)
    if not current_user_can_access_visible_module_id(int(module.Id)):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    presentation_path = module_presentation_path(module)
    if not presentation_path:
        return make_response(
            {'message': 'No presentation has been saved for this module.'},
            HTTPStatus.NOT_FOUND,
        )

    with open(presentation_path, 'rb') as presentation_file:
        data = presentation_file.read()

    filename = os.path.basename(presentation_path)
    ext = os.path.splitext(filename)[1].lower()
    preview = parse_bool(request.args.get('preview', False))
    mime = PRESENTATION_MIME_TYPES.get(ext, 'application/octet-stream')
    response_name = filename

    if preview and ext in {'.ppt', '.pptx'}:
        converted_pdf = presentation_pdf_preview(presentation_path)
        if converted_pdf is not None:
            data = converted_pdf
            mime = 'application/pdf'
            response_name = f"{os.path.splitext(filename)[0]}.pdf"
        else:
            data = unavailable_presentation_preview_html(filename)
            mime = 'text/html; charset=utf-8'
            response_name = f"{os.path.splitext(filename)[0]}.html"

    disposition = 'inline' if preview else 'attachment'
    headers = {
        'Content-Disposition': f"{disposition}; filename=\"{response_name}\"; filename*=UTF-8''{quote(response_name)}",
        'Content-Length': str(len(data)),
        'X-Filename': response_name,
        'X-Original-Filename': filename,
        'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'private, no-store',
        'Access-Control-Expose-Headers': (
            'Content-Disposition, Content-Type, X-Filename, X-Original-Filename'
        ),
    }

    if preview and mime.startswith('text/html'):
        headers['Content-Security-Policy'] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )

    return Response(data, content_type=mime, headers=headers)


@projects_api.route('/module_presentation', methods=['POST'])
@jwt_required()
def save_module_presentation():
    if not is_staff_user():
        return access_denied_response()

    module = requested_presentation_module()
    if module is None:
        return make_response({'message': 'Module not found'}, HTTPStatus.NOT_FOUND)
    if not user_can_access_module_id(int(module.Id)):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    presentation = request.files.get('presentation')
    if presentation is None or not presentation.filename:
        return make_response(
            {'message': 'Choose a presentation to save.'},
            HTTPStatus.BAD_REQUEST,
        )

    filename = secure_filename(presentation.filename)
    ext = os.path.splitext(filename)[1].lower()
    if not filename or ext not in ALLOWED_PRESENTATION_EXTS:
        return make_response(
            {'message': 'Presentations must be PDF, PPT, or PPTX files.'},
            HTTPStatus.BAD_REQUEST,
        )

    module_dir = teacher_module_dir(module)
    os.makedirs(module_dir, exist_ok=True)
    destination = os.path.join(module_dir, filename)
    temporary_path = os.path.join(
        module_dir,
        f'.presentation-{datetime.now().strftime("%Y%m%d_%H%M%S_%f")}.upload',
    )

    try:
        presentation.save(temporary_path)
        os.replace(temporary_path, destination)

        for name in os.listdir(module_dir):
            existing_path = os.path.join(module_dir, name)
            if (
                existing_path != destination
                and os.path.isfile(existing_path)
                and os.path.splitext(name)[1].lower() in ALLOWED_PRESENTATION_EXTS
            ):
                os.remove(existing_path)
    except OSError as exc:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass
        return make_response(
            {'message': f'Could not save presentation: {exc}'},
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    return jsonify({
        'message': 'Module presentation saved.',
        'file_name': filename,
    })


@projects_api.route('/get_module_overview', methods=['GET'])
@jwt_required()
@inject
def get_module_overview(project_repo: ProjectRepository = Provide[Container.project_repo], submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    if not is_staff_user():
        return access_denied_response()

    module_id = int(str(request.args.get('module_id', 0)) or 0)
    project_id = int(str(request.args.get('project_id', 0)) or 0)
    if module_id > 0 and not user_can_access_module_id(module_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    if project_id > 0 and not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    module = project_repo.get_module(module_id) if module_id > 0 else None
    if not module and project_id > 0:
        module = project_repo.get_module_by_project_id(project_id)

    if not module:
        return make_response({'message': 'Module not found'}, HTTPStatus.NOT_FOUND)

    project = project_repo.get_main_project_for_module(module.Id)
    if not project:
        return make_response({'message': 'Main project not found'}, HTTPStatus.NOT_FOUND)

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

        for idx, pp in enumerate(problems):
            pp_id = int(pp.Id)
            setup_status = project_setup_status(
                project_repo,
                project_id,
                checkpoint_id=pp_id,
                testcase_count=int(testcase_counts.get(pp_id, 0) or 0),
            )
            checkpoint_rows.append({
                "id": pp_id,
                "number": idx + 1,
                "name": str(getattr(pp, "Name", "") or f"Checkpoint {idx + 1}"),
                "enabled": bool(getattr(pp, "Enabled", True)),
                "submissions": int(submission_counts.get(pp_id, 0) or 0),
                "hasSolutionProgram": bool(setup_status["HasSolutionProgram"]),
                "hasTestcases": bool(setup_status["HasTestcases"]),
                "testcaseCount": int(setup_status["TestcaseCount"]),
            })
    except Exception:
        checkpoint_rows = []

    return jsonify({
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
    })

@projects_api.route('/update_project_name', methods=['POST'])
@jwt_required()
@inject
def update_project_name(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    project_id = int(str(data.get('project_id', 0)) or 0)
    name = str(data.get('name', '')).strip()

    if project_id <= 0 or not name:
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    project = project_repo.update_project_name(project_id, name)
    if not project:
        return make_response({'message': 'Project not found'}, HTTPStatus.NOT_FOUND)

    return jsonify({'message': 'Project name updated'})

@projects_api.route('/update_checkpoint_name', methods=['POST'])
@jwt_required()
@inject
def update_checkpoint_name(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    checkpoint_id = int(str(data.get('checkpoint_id', 0)) or 0)
    name = str(data.get('name', '')).strip()

    if checkpoint_id <= 0 or not name:
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_checkpoint_id(checkpoint_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    pp = project_repo.update_checkpoint_name(checkpoint_id, name)
    if not pp:
        return make_response({'message': 'Checkpoint not found'}, HTTPStatus.NOT_FOUND)

    return jsonify({'message': 'Checkpoint name updated'})


@projects_api.route('/get_projects_by_class_id', methods=['GET'])
@jwt_required()
@inject
def get_projects_by_class_id(project_repo: ProjectRepository = Provide[Container.project_repo], submission_repo: SubmissionRepository = Provide[Container.submission_repo]):
    if not is_staff_user():
        return access_denied_response()
    class_id = request.args.get('id')
    if not user_can_access_class_id(parse_int(class_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    data = project_repo.get_projects_by_class_id(class_id)
    
    new_projects = []
    thisdic = submission_repo.get_total_submission_for_all_projects()
    for proj in data:

        checkpoint_total = count_checkpoint_unique_users(int(proj.Id))

        new_projects.append(json.dumps({
            "Id": proj.Id,
            "Name": proj.Name,
            "Start": project_start(proj).strftime("%x %X") if project_start(proj) else "",
            "End": project_end(proj).strftime("%x %X") if project_end(proj) else "",
            "TotalSubmissions": int(thisdic.get(proj.Id, 0) or 0),
            "CheckpointTotalSubmissions": int(checkpoint_total),
            "CheckpointsEnabled": True,
            "ModuleId": getattr(proj, "ModuleId", None),
        }))
    return jsonify(new_projects)

@projects_api.route('/checkpoint_submission_counts', methods=['GET'])
@jwt_required()
def checkpoint_submission_counts():
    """
    Returns checkpoint submission counts per checkpoint_id (and total) for a project.
    Response:
      { "total": <int>, "by_problem": { "<ppid>": <count>, ... } }
    """
    if not is_staff_user():
        return access_denied_response()

    pid = parse_int(request.args.get("project_id", ""), 0)
    if pid <= 0:
        return jsonify({'total': 0, 'by_problem': {}})
    pid = int(pid)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    if not hasattr(Submissions, "IsCheckpoint"):
        return jsonify({'total': 0, 'by_problem': {}})

    total = 0
    by_problem = {}
    try:
        total = count_checkpoint_unique_users(int(pid))

        # If your Submissions model tracks which checkpoint was submitted:
        if hasattr(Submissions, "CheckpointId"):
            rows = (
                db.session.query(
                    Submissions.CheckpointId,
                    func.count(func.distinct(Submissions.User))
                )

                .filter(Submissions.Project == pid, Submissions.IsCheckpoint == True)
                .group_by(Submissions.CheckpointId)
                .all()
            )
            for ppid, cnt in rows:
                if ppid is None:
                    continue
                by_problem[str(int(ppid))] = int(cnt or 0)
    except Exception:
        total = 0
        by_problem = {}

    return jsonify({'total': int(total), 'by_problem': by_problem})

@projects_api.route('/getAssignmentDescription', methods=['GET'])
@jwt_required()
@inject
def getAssignmentDescription(project_repo: ProjectRepository = Provide[Container.project_repo]):
    
    project_id = request.args.get('project_id')
    if not current_user_can_download_project_files(parse_int(project_id, 0)):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    ppid_raw = (request.args.get('checkpoint_id', '') or '').strip()
    ppid = int(ppid_raw) if ppid_raw.isdigit() else None
    assignmentdesc_contents = project_repo.get_project_desc_file(int(project_id), checkpoint_id=ppid)
    assignmentdesc_path = project_repo.get_project_desc_path(int(project_id), checkpoint_id=ppid)

    fname = os.path.basename(assignmentdesc_path) if assignmentdesc_path else 'assignment_description'
    ext = os.path.splitext(fname)[1].lower()
    file_stream = BytesIO(assignmentdesc_contents)
    data = file_stream.getvalue()
    preview = parse_bool(request.args.get('preview', False))

    if preview and ext in {'.doc', '.docx'}:
        converted_pdf = office_document_pdf_preview(assignmentdesc_path)
        if converted_pdf is not None:
            data = converted_pdf
            mime = 'application/pdf'
            response_name = f"{os.path.splitext(fname)[0]}.pdf"
        elif ext == '.docx':
            try:
                data = docx_preview_html(data, fname)
            except (KeyError, OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
                data = unavailable_office_preview_html(fname)
            mime = 'text/html; charset=utf-8'
            response_name = f"{os.path.splitext(fname)[0]}.html"
        else:
            data = unavailable_office_preview_html(fname)
            mime = 'text/html; charset=utf-8'
            response_name = f"{os.path.splitext(fname)[0]}.html"
    else:
        response_name = fname
        if ext == '.pdf':
            mime = 'application/pdf'
        elif ext == '.docx':
            mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        elif ext == '.doc':
            mime = 'application/msword'
        elif ext in {'.txt', '.md'}:
            mime = 'text/plain; charset=utf-8'
        else:
            mime = 'application/octet-stream'

    disposition = 'inline' if preview else 'attachment'

    headers = {
        'Content-Disposition': f"{disposition}; filename=\"{response_name}\"; filename*=UTF-8''{quote(response_name)}",
        'Content-Length': str(len(data)),
        'X-Filename': response_name,
        'X-Original-Filename': fname,
        'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'private, no-store',
        'Access-Control-Expose-Headers': 'Content-Disposition, Content-Type, X-Filename, X-Original-Filename',
    }

    if preview and mime.startswith('text/html'):
        headers['Content-Security-Policy'] = (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )

    # Preserve the original filename while allowing the preview representation to differ.
    return Response(
        data,
        content_type=mime,
        headers=headers,
    )

@projects_api.route('/edit_checkpoint_project_files', methods=['POST'])
@jwt_required()
@inject
def edit_checkpoint_project_files(project_repo: ProjectRepository = Provide[Container.project_repo]):
    """
    Upload checkpoint solution files + assignment description (and optional additional files)
    into a dedicated checkpoint folder, and store paths on CheckpointProjects (not Projects).
    """
    def ts_str() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def safe_name(s: str) -> str:
        return secure_filename(s or "").replace(" ", "_")

    if not is_staff_user():
        return access_denied_response()

    pid_str = (request.form.get("project_id", "") or "").strip()
    ppid_str = (request.form.get("checkpoint_id", "") or "").strip()
    if not pid_str.isdigit() or not ppid_str.isdigit():
        return make_response({'message': 'Invalid project_id or checkpoint_id'}, HTTPStatus.BAD_REQUEST)
    pid = int(pid_str)
    ppid = int(ppid_str)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    if not user_can_access_checkpoint_id(ppid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    proj = project_repo.get_selected_project(pid)
    if not proj:
        return make_response({'message': 'Project not found'}, HTTPStatus.NOT_FOUND)

    # Require both solution and description for checkpoint files
    solution_uploads = request.files.getlist('solutionFiles')
    solution_uploads = [f for f in solution_uploads if f and f.filename]
    if not solution_uploads:
        return make_response({'message': 'No selected solution files'}, HTTPStatus.BAD_REQUEST)
    if 'assignmentdesc' not in request.files or not request.files['assignmentdesc'].filename:
        return make_response({'message': 'No assignment description file'}, HTTPStatus.BAD_REQUEST)

    pp = project_repo.get_checkpoint(ppid)
    if not pp:
        return make_response({'message': 'Checkpoint not found'}, HTTPStatus.NOT_FOUND)

    ts = ts_str()
    checkpoint_dir = teacher_checkpoint_project_dir(proj, pp, timestamp_hint=ts)
    base_dir = version_dir(checkpoint_dir, ts)
    os.makedirs(base_dir, exist_ok=True)

    # Save solution file(s)
    uploaded_exts = set()
    for up in solution_uploads:
        orig = safe_name(up.filename)
        ext = os.path.splitext(orig)[1].lower()
        if ext not in ALLOWED_SOURCE_EXTS:
            return make_response({'message': f'Unsupported file type: {ext}'}, HTTPStatus.BAD_REQUEST)
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
    ad = request.files['assignmentdesc']
    ad_name = safe_name(ad.filename or "assignment.pdf")
    desc_path = os.path.join(base_dir, ad_name)
    ad.save(desc_path)

    # Save additional checkpoint files (optional)
    add_names = []
    for add_up in request.files.getlist('additionalFiles'):
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
        from src.repositories.database import db
        db.session.commit()
    except Exception:
        return make_response({'message': 'Failed to save checkpoint paths'}, HTTPStatus.INTERNAL_SERVER_ERROR)

    # Recompute outputs for this PRACTICE PROBLEM's testcases using this checkpoint solution
    try:
        recompute_expected_outputs(
            project_repo,
            int(pid),
            solution_override_path=base_dir,
            language_override=checkpoint_language,
            checkpoint_id=int(ppid),
        )
    except Exception:
        pass

    return jsonify({'ok': True})

@projects_api.route('/rename_checkpoint', methods=['POST'])
@jwt_required()
@inject
def rename_checkpoint(project_repo: ProjectRepository = Provide[Container.project_repo]):
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    try:
        pid = int(str(data.get('project_id', 0)) or 0)
    except ValueError:
        pid = 0
    try:
        ppid = int(str(data.get('checkpoint_id', 0)) or 0)
    except ValueError:
        ppid = 0
    name = str(data.get('name', '') or '').strip()

    if pid <= 0 or ppid <= 0 or not name:
        return make_response({'message': 'Missing required fields'}, HTTPStatus.BAD_REQUEST)
    if not user_can_access_project_id(pid):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    if not user_can_access_checkpoint_id(ppid):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    pp = project_repo.get_checkpoint(ppid)
    if not pp or int(getattr(pp, "ProjectId", 0) or 0) != int(pid):
        return make_response({'message': 'Checkpoint not found'}, HTTPStatus.NOT_FOUND)

    pp.Name = name
    try:
        from src.repositories.database import db
        db.session.commit()
    except Exception:
        return make_response({'message': 'Failed to rename checkpoint'}, HTTPStatus.INTERNAL_SERVER_ERROR)

    return jsonify({'ok': True, 'name': name})

@projects_api.route('/ProjectGrading', methods=['POST'])
@jwt_required()
@inject
def ProjectGrading(submission_repo: SubmissionRepository = Provide[Container.submission_repo], project_repo: ProjectRepository = Provide[Container.project_repo], class_repo: ClassRepository = Provide[Container.class_repo], user_repo: UserRepository = Provide[Container.user_repo]):
    if not is_staff_user():
        return access_denied_response(HTTPStatus.FORBIDDEN)

    input_json = request.get_json()
    project_id = input_json['ProjectId']
    if not user_can_access_project_id(project_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    user_id = input_json['userID']
    checkpoint_raw = (input_json or {}).get('checkpoint', False)
    checkpoint = str(checkpoint_raw).strip().lower() in ('1', 'true', 'yes', 'y', 'on')

    ppid_raw = (input_json or {}).get('checkpoint_id', None)
    try:
        checkpoint_id = int(ppid_raw) if ppid_raw is not None else None
    except (TypeError, ValueError):
        checkpoint_id = None

    if checkpoint and hasattr(Submissions, "IsCheckpoint"):

        q = (
            Submissions.query
            .filter(
                Submissions.Project == project_id,
                Submissions.User == user_id,
                Submissions.IsCheckpoint == True
            )
        )
        # If grading a specific checkpoint, restrict to that checkpoint_id.
        if checkpoint_id is not None and hasattr(Submissions, "CheckpointId"):
            q = q.filter(Submissions.CheckpointId == checkpoint_id)
        sub = q.order_by(Submissions.Time.desc()).first()

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
        for r in (payload or {}).get("results", []):
            test_info.append({
                "name": (r or {}).get("name", ""),
                "passed": bool((r or {}).get("passed", False)),
                "State": bool((r or {}).get("passed", False)),
                "shortDiff": (r or {}).get("shortDiff", ""),
                "longDiff": (r or {}).get("longDiff", ""),
            })

        grading_data[user_id] = [student_code, test_info]
    else:
        grading_data[user_id] = ["", ""]

    return make_response(json.dumps({"Code": student_code, "TestResults": test_info, "Language": project_language}), HTTPStatus.OK)


@projects_api.route('/unlockStudentAccount', methods=['POST'])
@jwt_required()
@inject
def unlockStudentAccount(user_repo: UserRepository = Provide[Container.user_repo]):
    if not is_staff_user():
        return access_denied_response(HTTPStatus.FORBIDDEN)
    input_json = request.get_json()
    user_Id = input_json['UserId']
    if not user_can_access_student_id(user_Id):
        return access_denied_response(HTTPStatus.FORBIDDEN)
    user_repo.unlock_student_account(user_Id)
    message = {
        'message': 'Success'
    }
    return make_response(message, HTTPStatus.OK)
