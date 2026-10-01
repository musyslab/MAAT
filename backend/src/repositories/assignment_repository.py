"""Persist assignment structure, teaching materials, test cases, and project grades.

Manage modules, main projects, checkpoints, ordering, visibility, and assignment
file identities. Discover and import default teaching materials while reusing
shared files, and expose queries used by assignment management and progress
views. Database commits and existing filesystem operations remain explicit."""

import os
import json
from src.core.models import MainAssignmentGrades
from src.core.models import Submissions
from src.core.database import db
from sqlalchemy import and_
from datetime import datetime
from src.core.constants import chicago_now
from src.core.constants import to_chicago_datetime
from src.core.models import DefaultContentImports
from src.core.models import Projects
from src.core.models import Checkpoints
from src.core.models import Testcases
from src.core.models import Classes
from src.core.models import Modules
from src.core.models import ClassAssignments
from src.core.models import StudentHiddenModules
import subprocess
import sys
from typing import Optional
from typing import Dict
from sqlalchemy import func


# Normalize stored language names for the external grader configuration.

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
    # Handle the case where raw in aliases.
    if raw in aliases:
        return aliases[raw]

    try:
        candidates = []
        if solution_root and os.path.isdir(solution_root):
            candidates = [os.path.splitext(name)[1].lower() for name in os.listdir(solution_root)]
        elif solution_root:
            candidates = [os.path.splitext(solution_root)[1].lower()]

        # Handle the case where '.py' in candidates.
        if ".py" in candidates:
            return "py"
        # Handle the case where '.java' in candidates.
        if ".java" in candidates:
            return "java"
        # Handle the case where '.c' in candidates.
        if ".c" in candidates:
            return "c"
        # Handle the case where '.rkt' in candidates or '.scm' in candidates.
        if ".rkt" in candidates or ".scm" in candidates:
            return "racket"
    # Ignore this failure and allow the surrounding operation to continue.
    except Exception:
        pass

    return raw or "py"


class AssignmentRepository:
    """Database operations for assignments; callers share the Flask request session."""

    DEFAULT_ROOT = "/tabot-files/project-files/Default-Assignment-Content"

    def default_catalog(self):
        """Folder identities do not depend on companion filenames."""
        from pathlib import Path
        import re

        root = Path(self.DEFAULT_ROOT)
        # Reject this case with the exception below.
        if not root.is_dir():
            raise ValueError("Default assignment content folder is missing.")

        def natural(path):
            """Handle natural for this component.

            Inputs: path."""
            return [int(v) if v.isdigit() else v.casefold() for v in re.split(r"(\d+)", path.name)]

        items = []
        # Process each folder from sorted((p for p in root.iterdir() if p.is_dir()), key=natural).
        for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=natural):
            # Process each assignment from sorted((p for p in folder.iterdir() if p.is_dir()), key=natural).
            for assignment in sorted((p for p in folder.iterdir() if p.is_dir()), key=natural):
                match = re.fullmatch(r"Checkpoint\s*#?\s*([1-9]\d*)", assignment.name, re.I)
                if assignment.name.casefold() != "main project" and not match:
                    continue
                number = int(match.group(1)) if match else None
                key = folder.name + "/" + ("main" if number is None else f"checkpoint-{number}")
                # Reject this case with the exception below.
                if any(item["key"] == key for item in items):
                    raise ValueError(f"Duplicate assignment folder: {assignment}")
                items.append(
                    dict(
                        key=key,
                        module=folder.name,
                        label=assignment.name,
                        number=number,
                        path=assignment,
                    )
                )
        return items

    def default_files(self, item):
        """Handle default files for this component.

        Inputs: item."""
        files = [p for p in item["path"].iterdir() if p.is_file()]

        def single(extension, candidates=files):
            """Handle single for this component.

            Inputs: extension, candidates."""
            matches = [p for p in candidates if p.suffix.lower() == extension]
            # Reject this case with the exception below.
            if len(matches) != 1:
                raise ValueError(
                    f"{item['module']}/{item['label']}: expected exactly one {extension} file; found {len(matches)}"
                )
            return matches[0]

        source, pdf, cases_file = single(".py"), single(".pdf"), single(".json")
        presentation = single(".pdf", [p for p in item["path"].parent.iterdir() if p.is_file()])
        cases = json.loads(cases_file.read_text(encoding="utf-8-sig"))
        # Reject this case with the exception below.
        if not isinstance(cases, list) or not cases:
            raise ValueError(f"{item['key']}: expected a nonempty testcase array")
        orders = set()
        # Process each case from cases.
        for case in cases:
            # Reject this case with the exception below.
            if (
                not isinstance(case, dict)
                or any(not isinstance(case.get(k), str) for k in ("name", "input", "output"))
                or type(case.get("hidden")) is not bool
                or type(case.get("order")) is not int
                or case["order"] < 1
                or case["order"] in orders
            ):
                raise ValueError(f"{item['key']}: invalid testcase or duplicate order")
            orders.add(case["order"])
        return source, pdf, cases_file, presentation, cases

    def default_expected_output(self, source, input_data, project_id, class_id, item):
        """Build an imported testcase transcript with the same grader used for manual cases."""
        grading_script = "/tabot-files/grading-scripts/grade.py"
        # Launch the external command with the execution options below.
        result = subprocess.run(
            [
                sys.executable,
                grading_script,
                "ADMIN",
                "py",
                input_data,
                str(source.parent.resolve()),
                "[]",
                str(project_id),
                str(class_id),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(source.parent),
            timeout=60,
        )
        # Reject this case with the exception below.
        if result.returncode != 0:
            raise ValueError(
                f"{item['key']}: could not run the solution for an imported test case: "
                f"{result.stderr.strip() or 'grader exited with an error'}"
            )
        return (result.stdout or "").replace("\r\n", "\n").replace("\r", "\n")


    def default_existing(self, cls):
        """Return the explicit default-content import records for this class."""
        return {
            row.SourceKey: row
            for row in DefaultContentImports.query.filter_by(ClassId=cls.Id).all()
        }

    def default_content_options(self, class_id):
        """Handle default content options for this component.

        Inputs: class_id."""
        # Execute the database lookup with the filters specified below.
        cls = Classes.query.filter_by(Id=int(class_id)).first()
        # Handle the case where not cls or not cls.School.UseDefaultMaterials.
        if not cls or not cls.School.UseDefaultMaterials:
            return dict(enabled=False, items=[])
        items = self.default_catalog()
        existing = self.default_existing(cls)
        result = []
        # Process each item from items.
        for item in items:
            error = None
            if item["key"] not in existing:
                try:
                    self.default_files(item)
                except (ValueError, OSError) as exc:
                    error = str(exc)
            result.append(
                dict(
                    key=item["key"],
                    module=item["module"],
                    label=item["label"],
                    imported=item["key"] in existing,
                    error=error,
                )
            )
        return dict(enabled=True, items=result)

    def import_default_content(self, class_id, selected):
        """Handle import default content for this component.

        Inputs: class_id, selected.
        Database changes are committed at the explicit transaction boundaries below."""
        from datetime import timedelta

        # Reject this case with the exception below.
        if (
            not isinstance(selected, list)
            or not selected
            or any(not isinstance(k, str) for k in selected)
        ):
            raise ValueError("Select at least one project.")
        try:
            # Execute the database lookup with the filters specified below.
            cls = (
                Classes.query.filter_by(Id=int(class_id))
                .populate_existing()
                .with_for_update()
                .one()
            )
            db.session.refresh(cls.School)
            # Reject this case with the exception below.
            if not cls.School.UseDefaultMaterials:
                raise PermissionError("Default assignment content is disabled for this school.")
            items = self.default_catalog()
            catalog = {item["key"]: item for item in items}
            # Reject this case with the exception below.
            if set(selected) - catalog.keys():
                raise ValueError("The catalog changed. Refresh and select projects again.")
            existing = self.default_existing(cls)
            pending = [
                item for item in items if item["key"] in selected and item["key"] not in existing
            ]
            validated = [(item, self.default_files(item)) for item in pending]
            now = chicago_now()
            module_ids = {row.ModuleId for row in existing.values()}
            modules_by_id = {
                module.Id: module
                for module in Modules.query.filter(Modules.Id.in_(module_ids)).all()
            } if module_ids else {}
            modules = {}
            # Process each (key, row) from existing.items().
            for key, row in existing.items():
                if key in catalog:
                    modules[catalog[key]["module"]] = modules_by_id.get(row.ModuleId)
            # Process each (item, (source, pdf, cases_file, presentation, cases)) from validated.
            for item, (source, pdf, cases_file, presentation, cases) in validated:
                module = modules.get(item["module"])
                if module is None:
                    module = Modules(
                        ClassId=cls.Id,
                        Name=item["module"],
                        FirstName=item["module"],
                        Start=now,
                        End=now + timedelta(days=365),
                    )
                    # Stage the new records in the current database transaction.
                    db.session.add(module)
                    # Send pending changes to the database without committing the transaction yet.
                    db.session.flush()
                    module.FileTimestamp = f"{now:%Y%m%d_%H%M%S}_{module.Id}"
                    modules[item["module"]] = module
                    project = Projects(
                        ClassId=cls.Id,
                        ModuleId=module.Id,
                        Name=item["module"],
                        FirstName=item["module"],
                        Language="python",
                        AdditionalFilePath="[]",
                    )
                    # Stage the new records in the current database transaction.
                    db.session.add(project)
                    # Send pending changes to the database without committing the transaction yet.
                    db.session.flush()
                else:
                    project = self.get_main_project_for_module(module.Id)
                    # Reject this case with the exception below.
                    if project is None:
                        raise ValueError("The imported module no longer has its main project.")
                if item["number"] is None:
                    target = project
                    # Reject this case with the exception below.
                    if target.solutionpath or target.AsnDescriptionPath:
                        raise ValueError(
                            f"{item['module']}: main project already contains teacher content."
                        )
                    target.Name = target.FirstName = source.stem
                else:
                    # Append without renumbering existing teacher work.
                    used = [
                        cp.CheckpointNumber or 0
                        for cp in Checkpoints.query.filter_by(ProjectId=project.Id).all()
                    ]
                    number = item["number"] if item["number"] not in used else max(used + [0]) + 1
                    name = f"Checkpoint {item['number']}: {source.stem}"
                    target = Checkpoints(
                        ProjectId=project.Id,
                        CheckpointNumber=number,
                        Enabled=True,
                        Name=name,
                        FirstName=name,
                        Language="python",
                        AdditionalFilePath="[]",
                    )
                    # Stage the new records in the current database transaction.
                    db.session.add(target)
                    # Send pending changes to the database without committing the transaction yet.
                    db.session.flush()
                # Class-owned database rows reference the same canonical files.
                # Upload endpoints create private version directories on edits.
                target.solutionpath = str(source.parent.resolve())
                target.AsnDescriptionPath = str(pdf.resolve())
                target.Language = "python"
                # Process each case from sorted(cases, key=lambda case: case['order']).
                for case in sorted(cases, key=lambda case: case["order"]):
                    input_data = case["input"].replace("\r\n", "\n").replace("\r", "\n")
                    output = self.default_expected_output(
                        source, input_data, project.Id, cls.Id, item
                    )
                    # Stage the new records in the current database transaction.
                    db.session.add(
                        Testcases(
                            ProjectId=project.Id,
                            CheckpointId=target.Id if item["number"] is not None else None,
                            Name=case["name"],
                            input=input_data,
                            Output=output,
                            Hidden=case["hidden"],
                            SortOrder=case["order"],
                            Checkpoint=item["number"] is not None,
                        )
                    )
                # Stage the new records in the current database transaction.
                db.session.add(
                    DefaultContentImports(
                        ClassId=cls.Id,
                        SourceKey=item["key"],
                        ModuleId=module.Id,
                        AssignmentId=target.Id,
                    )
                )
            if pending:
                cls.DefaultContentInitialized = True
            # Commit the pending database changes so they persist beyond this request.
            db.session.commit()
            return dict(imported=len(pending), skipped=len(set(selected)) - len(pending))
        except Exception:
            # Undo pending database changes after the operation fails.
            db.session.rollback()
            raise

    def default_module_presentation(self, module):
        """Shared fallback; a teacher-uploaded presentation takes precedence."""
        from pathlib import Path

        # Return an empty or negative result when this guard matches.
        if module is None:
            return None
        # Execute the database lookup with the filters specified below.
        rows = DefaultContentImports.query.filter_by(
            ClassId=module.ClassId, ModuleId=module.Id
        ).all()
        names = {row.SourceKey.rsplit("/", 1)[0] for row in rows}
        root = Path(self.DEFAULT_ROOT).resolve()
        # Process each name from sorted(names).
        for name in sorted(names):
            folder = (root / name).resolve()
            if folder.parent != root or not folder.is_dir():
                continue
            pdfs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
            # Handle the case where len(pdfs) == 1.
            if len(pdfs) == 1:
                return str(pdfs[0])
        return None


    def json_list_field(self, raw: str) -> list[str]:
        """Handle json list field for this component.

        Inputs: raw."""
        try:
            s = (raw or "").strip()
            # Return an empty or negative result when this guard matches.
            if not s:
                return []
            vals = json.loads(s) if s.startswith("[") else [s]
            return [v for v in (vals or []) if v]
        # Convert this failure into the fallback result or error response below.
        except Exception:
            return []

    def basename_or_empty(self, p: str) -> str:
        """Handle basename or empty for this component.

        Inputs: p."""
        return os.path.basename(p) if p else ""

    def expand_additional_paths(self, add_path: str, project_base: str) -> str:
        """
        grade.py expects JSON list of absolute paths.
        DB may store basenames or JSON list of basenames.
        """
        try:
            base_dir = (
                project_base if os.path.isdir(project_base) else os.path.dirname(project_base)
            )
            lst = self.json_list_field(add_path)
            abs_list = []
            # Process each p from lst or [].
            for p in lst or []:
                if not p:
                    continue
                if os.path.isabs(p):
                    abs_list.append(p)
                else:
                    abs_list.append(os.path.join(base_dir, os.path.basename(p)))
            return json.dumps(abs_list)
        # Convert this failure into the fallback result or error response below.
        except Exception:
            return add_path or ""

    def coerce_datetime(self, value):
        """Handle coerce datetime for this component.

        Inputs: value."""
        # Handle the case where isinstance(value, datetime).
        if isinstance(value, datetime):
            return to_chicago_datetime(value)
        return to_chicago_datetime(datetime.fromisoformat(str(value)))

    def new_file_timestamp(self) -> str:
        """Handle new file timestamp for this component."""
        return chicago_now().strftime("%Y%m%d_%H%M%S")

    def ensure_module_file_identity(
        self, module: Modules | None, fallback_name: str = ""
    ) -> Modules | None:
        """Ensure module file identity.

        Inputs: module, fallback_name.
        Database changes are committed at the explicit transaction boundaries below."""
        # Return an empty or negative result when this guard matches.
        if not module:
            return None

        changed = False

        if not getattr(module, "FirstName", None):
            module.FirstName = getattr(module, "Name", None) or fallback_name or "module"
            changed = True

        if not getattr(module, "FileTimestamp", None):
            module.FileTimestamp = self.new_file_timestamp()
            changed = True

        if changed:
            # Commit the pending database changes so they persist beyond this request.
            db.session.commit()

        return module

    def create_module(self, class_id: int, name: str, start: datetime, end: datetime) -> int:
        """Create module.

        Inputs: class_id, name, start, end.
        Database changes are committed at the explicit transaction boundaries below."""
        module = Modules(
            ClassId=int(class_id),
            Name=name,
            FirstName=name,
            FileTimestamp=self.new_file_timestamp(),
            Start=self.coerce_datetime(start),
            End=self.coerce_datetime(end),
        )
        # Stage the new records in the current database transaction.
        db.session.add(module)
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

        project = Projects(
            ClassId=int(class_id),
            ModuleId=module.Id,
            Name=name,
            FirstName=name,
            Language="",
            solutionpath=None,
            AsnDescriptionPath=None,
            AdditionalFilePath="[]",
        )
        # Stage the new records in the current database transaction.
        db.session.add(project)
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

        self.create_checkpoint(project.Id, name="Checkpoint 1")
        return int(module.Id)

    def get_modules_by_class_id(self, class_id: int):
        """Return modules by class id.

        Inputs: class_id."""
        return (
            Modules.query.filter(Modules.ClassId == int(class_id))
            .order_by(Modules.Start.asc(), Modules.Id.asc())
            .all()
        )

    def get_module(self, module_id: int):
        """Return module.

        Inputs: module_id."""
        return Modules.query.filter(Modules.Id == int(module_id)).first()

    def get_hidden_module_ids_for_student(self, class_id: int, user_id: int) -> set[int]:
        """Return hidden module ids for student.

        Inputs: class_id, user_id."""
        # Execute the database lookup with the filters specified below.
        rows = (
            db.session.query(StudentHiddenModules.ModuleId)
            .join(Modules, StudentHiddenModules.ModuleId == Modules.Id)
            .filter(
                Modules.ClassId == int(class_id),
                StudentHiddenModules.UserId == int(user_id),
            )
            .all()
        )

        return {int(row[0]) for row in rows if row and row[0] is not None and int(row[0] or 0) > 0}

    def get_hidden_module_ids_by_student_for_class(self, class_id: int) -> dict[int, list[int]]:
        """Return hidden module ids by student for class.

        Inputs: class_id."""
        # Execute the database lookup with the filters specified below.
        rows = (
            db.session.query(StudentHiddenModules.UserId, StudentHiddenModules.ModuleId)
            .join(Modules, StudentHiddenModules.ModuleId == Modules.Id)
            .join(
                ClassAssignments,
                and_(
                    ClassAssignments.UserId == StudentHiddenModules.UserId,
                    ClassAssignments.ClassId == Modules.ClassId,
                ),
            )
            .filter(Modules.ClassId == int(class_id))
            .order_by(StudentHiddenModules.UserId.asc(), StudentHiddenModules.ModuleId.asc())
            .all()
        )

        hidden_by_student: dict[int, list[int]] = {}
        # Process each (user_id, module_id) from rows.
        for user_id, module_id in rows:
            user_id_int = int(user_id or 0)
            module_id_int = int(module_id or 0)

            if user_id_int <= 0 or module_id_int <= 0:
                continue

            hidden_by_student.setdefault(user_id_int, []).append(module_id_int)

        return hidden_by_student

    def set_student_module_hidden(self, user_id: int, module_id: int, hidden: bool) -> bool:
        """Set student module hidden.

        Inputs: user_id, module_id, hidden.
        Database changes are committed at the explicit transaction boundaries below."""
        user_id = int(user_id)
        module_id = int(module_id)

        # Execute the database lookup with the filters specified below.
        existing = StudentHiddenModules.query.filter(
            StudentHiddenModules.UserId == user_id,
            StudentHiddenModules.ModuleId == module_id,
        ).first()

        if hidden and existing is None:
            # Stage the new records in the current database transaction.
            db.session.add(
                StudentHiddenModules(
                    UserId=user_id,
                    ModuleId=module_id,
                    CreatedAt=chicago_now(),
                )
            )

        if not hidden and existing is not None:
            # Mark this record for deletion in the current transaction.
            db.session.delete(existing)

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return True

    def set_module_hidden_for_class(self, module_id: int, hidden: bool) -> list[int]:
        """Set module hidden for class.

        Inputs: module_id, hidden.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        module = Modules.query.filter(Modules.Id == int(module_id)).first()
        # Return an empty or negative result when this guard matches.
        if not module:
            return []

        # Execute the database lookup with the filters specified below.
        student_rows = (
            db.session.query(ClassAssignments.UserId)
            .filter(ClassAssignments.ClassId == int(module.ClassId))
            .order_by(ClassAssignments.UserId.asc())
            .all()
        )
        student_ids = [int(row[0]) for row in student_rows if row and int(row[0] or 0) > 0]

        if hidden:
            # Execute the database lookup with the filters specified below.
            existing_rows = (
                db.session.query(StudentHiddenModules.UserId)
                .filter(StudentHiddenModules.ModuleId == int(module_id))
                .all()
            )
            existing_user_ids = {
                int(row[0]) for row in existing_rows if row and int(row[0] or 0) > 0
            }

            # Process each student_id from student_ids.
            for student_id in student_ids:
                if student_id not in existing_user_ids:
                    # Stage the new records in the current database transaction.
                    db.session.add(
                        StudentHiddenModules(
                            UserId=student_id,
                            ModuleId=int(module_id),
                            CreatedAt=chicago_now(),
                        )
                    )
        else:
            # Execute the database lookup with the filters specified below.
            StudentHiddenModules.query.filter(
                StudentHiddenModules.ModuleId == int(module_id)
            ).delete(synchronize_session=False)

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return student_ids

    def get_module_by_project_id(self, project_id: int):
        """Return module by project id.

        Inputs: project_id.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == int(project_id)).first()
        # Return an empty or negative result when this guard matches.
        if not project:
            return None
        # Handle the case where getattr(project, 'ModuleId', None).
        if getattr(project, "ModuleId", None):
            return Modules.query.filter(Modules.Id == int(project.ModuleId)).first()

        now = chicago_now()
        module = Modules(
            ClassId=project.ClassId,
            Name=project.Name,
            FirstName=project.Name,
            FileTimestamp=self.new_file_timestamp(),
            Start=now,
            End=now,
        )
        # Stage the new records in the current database transaction.
        db.session.add(module)
        # Send pending changes to the database without committing the transaction yet.
        db.session.flush()
        project.ModuleId = module.Id
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return module

    def get_main_project_for_module(self, module_id: int):
        """Return main project for module.

        Inputs: module_id."""
        return (
            Projects.query.filter(Projects.ModuleId == int(module_id))
            .order_by(Projects.Id.asc())
            .first()
        )

    def update_module(self, module_id: int, name: str, start: datetime, end: datetime):
        """Update module.

        Inputs: module_id, name, start, end.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        module = Modules.query.filter(Modules.Id == int(module_id)).first()
        # Return an empty or negative result when this guard matches.
        if not module:
            return None

        if not getattr(module, "FirstName", None):
            module.FirstName = getattr(module, "Name", None) or name
        if not getattr(module, "FileTimestamp", None):
            module.FileTimestamp = self.new_file_timestamp()

        module.Name = name
        module.Start = self.coerce_datetime(start)
        module.End = self.coerce_datetime(end)

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return module

    def update_project_name(self, project_id: int, name: str):
        """Update project name.

        Inputs: project_id, name.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == int(project_id)).first()
        # Return an empty or negative result when this guard matches.
        if not project:
            return None
        project.Name = name
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return project

    def update_checkpoint_name(self, checkpoint_id: int, name: str):
        """Update checkpoint name.

        Inputs: checkpoint_id, name.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        pp = Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()
        # Return an empty or negative result when this guard matches.
        if not pp:
            return None
        pp.Name = name
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return pp

    def list_checkpoints(self, project_id: int):
        """List checkpoints.

        Inputs: project_id."""
        return (
            Checkpoints.query.filter(
                Checkpoints.ProjectId == int(project_id),
                Checkpoints.Enabled == True,
            )
            .order_by(Checkpoints.CheckpointNumber.asc(), Checkpoints.Id.asc())
            .all()
        )

    def list_checkpoints_by_project_ids(self, project_ids: list[int]):
        """Fetch enabled checkpoints for multiple projects in one query."""
        # Return an empty or negative result when this guard matches.
        if not project_ids:
            return []
        return (
            Checkpoints.query.filter(
                Checkpoints.ProjectId.in_(project_ids),
                Checkpoints.Enabled == True,
            )
            .order_by(
                Checkpoints.ProjectId.asc(),
                Checkpoints.CheckpointNumber.asc(),
                Checkpoints.Id.asc(),
            )
            .all()
        )

    def next_checkpoint_number_scratch_base(self, project_id: int) -> int:
        """Handle next checkpoint number scratch base for this component.

        Inputs: project_id."""
        max_num, total_rows = (
            db.session.query(
                func.max(Checkpoints.CheckpointNumber), func.count(Checkpoints.Id)
            )
            .filter(Checkpoints.ProjectId == int(project_id))
            .one()
        )
        return int(max_num or 0) + int(total_rows or 0) + 1000

    def checkpoint_rows_for_project(self, project_id: int):
        """Handle checkpoint rows for project for this component.

        Inputs: project_id."""
        return (
            Checkpoints.query.filter(Checkpoints.ProjectId == int(project_id))
            .order_by(Checkpoints.CheckpointNumber.asc(), Checkpoints.Id.asc())
            .all()
        )

    def write_checkpoint_order(self, project_id: int, ordered_active_ids: list[int]):
        """Handle write checkpoint order for this component.

        Inputs: project_id, ordered_active_ids.
        Database changes are committed at the explicit transaction boundaries below."""
        all_rows = self.checkpoint_rows_for_project(project_id)
        active_rows = [row for row in all_rows if bool(getattr(row, "Enabled", True))]
        inactive_rows = [row for row in all_rows if not bool(getattr(row, "Enabled", True))]

        active_by_id = {int(row.Id): row for row in active_rows}
        ordered_rows = []
        seen: set[int] = set()

        # Process each raw_id from ordered_active_ids or [].
        for raw_id in ordered_active_ids or []:
            try:
                row_id = int(raw_id)
            except Exception:
                continue

            row = active_by_id.get(row_id)
            if row is not None and row_id not in seen:
                ordered_rows.append(row)
                seen.add(row_id)

        # Process each row from active_rows.
        for row in active_rows:
            row_id = int(row.Id)
            if row_id not in seen:
                ordered_rows.append(row)
                seen.add(row_id)

        final_rows = [*ordered_rows, *inactive_rows]

        scratch_base = self.next_checkpoint_number_scratch_base(project_id)
        # Process each (index, row) from enumerate(all_rows, start=1).
        for index, row in enumerate(all_rows, start=1):
            row.CheckpointNumber = scratch_base + index

        # Send pending changes to the database without committing the transaction yet.
        db.session.flush()

        # Process each (index, row) from enumerate(final_rows, start=1).
        for index, row in enumerate(final_rows, start=1):
            row.CheckpointNumber = index

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return ordered_rows

    def reorder_checkpoints(self, project_id: int, ordered_ids: list[int]):
        """Reorder checkpoints.

        Inputs: project_id, ordered_ids."""
        try:
            return self.write_checkpoint_order(project_id, ordered_ids)
        except Exception:
            # Undo pending database changes after the operation fails.
            db.session.rollback()
            raise

    def renumber_checkpoints(self, project_id: int):
        """Handle renumber checkpoints for this component.

        Inputs: project_id."""
        try:
            rows = self.list_checkpoints(project_id)
            return self.write_checkpoint_order(
                project_id,
                [int(row.Id) for row in rows],
            )
        except Exception:
            # Undo pending database changes after the operation fails.
            db.session.rollback()
            raise

    def delete_checkpoint(self, checkpoint_id: int):
        """Delete checkpoint.

        Inputs: checkpoint_id."""
        # Execute the database lookup with the filters specified below.
        pp = Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()
        # Return an empty or negative result when this guard matches.
        if not pp:
            return None

        # Reject this case with the exception below.
        if DefaultContentImports.query.filter_by(AssignmentId=pp.Id).first():
            raise ValueError("Imported default projects cannot be deleted.")
        project_id = int(pp.ProjectId)
        pp.Enabled = False
        pp.CheckpointNumber = self.next_checkpoint_number_scratch_base(project_id)
        # Send pending changes to the database without committing the transaction yet.
        db.session.flush()
        self.renumber_checkpoints(project_id)
        return pp

    def get_checkpoint(self, checkpoint_id: int) -> Optional[Checkpoints]:
        """Return checkpoint.

        Inputs: checkpoint_id."""
        return Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()

    def create_checkpoint(self, project_id: int, *, name: str = "") -> int:
        """Create checkpoint.

        Inputs: project_id, name.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        proj = Projects.query.filter(Projects.Id == int(project_id)).first()
        # Handle the case where not proj.
        if not proj:
            return 0

        max_num = (
            db.session.query(func.max(Checkpoints.CheckpointNumber))
            .filter(Checkpoints.ProjectId == int(project_id))
            .scalar()
        )
        next_num = int(max_num or 0) + 1

        pp = Checkpoints(
            ProjectId=int(project_id),
            CheckpointNumber=next_num,
            Enabled=True,
            Name=(name or f"Checkpoint {next_num}"),
            FirstName=(name or f"Checkpoint {next_num}"),
            Language=getattr(proj, "Language", ""),
            solutionpath=None,
            AsnDescriptionPath=None,
            AdditionalFilePath="[]",
        )
        # Stage the new records in the current database transaction.
        db.session.add(pp)
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return int(pp.Id)

    def get_current_project(self) -> Optional[Projects]:
        """Return current project."""
        now = chicago_now()
        return (
            Projects.query.join(Modules, Projects.ModuleId == Modules.Id)
            .filter(Modules.End >= now, Modules.Start < now)
            .order_by(Modules.Start.asc(), Projects.Id.asc())
            .first()
        )

    def get_current_project_by_class(self, class_id: int) -> Optional[Projects]:
        """Return current project by class.

        Inputs: class_id."""
        now = chicago_now()
        return (
            Projects.query.join(Modules, Projects.ModuleId == Modules.Id)
            .filter(
                Projects.ClassId == class_id,
                Modules.End >= now,
                Modules.Start < now,
            )
            .order_by(Modules.Start.asc(), Projects.Id.asc())
            .first()
        )

    def get_all_projects(self) -> Projects:
        """Return all projects."""
        return (
            Projects.query.outerjoin(Modules, Projects.ModuleId == Modules.Id)
            .order_by(Modules.End.asc(), Projects.Id.asc())
            .all()
        )

    def get_selected_project(self, project_id: int) -> Projects:
        """Return selected project.

        Inputs: project_id."""
        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == project_id).first()
        return project

    def get_projects_by_class_id(self, class_id: int) -> int:
        """Return projects by class id.

        Inputs: class_id."""
        # Execute the database lookup with the filters specified below.
        class_projects = Projects.query.filter(Projects.ClassId == class_id)
        return class_projects

    def create_project(
        self,
        name: str,
        language: str,
        class_id: int,
        file_path: str,
        description_path: str,
        additional_file_path: str,
        checkpoints_enabled: bool = False,
        module_id: Optional[int] = None,
    ):
        """Create project.

        Inputs: name, language, class_id, file_path, description_path, additional_file_path, checkpoints_enabled, module_id.
        Database changes are committed at the explicit transaction boundaries below."""
        module_obj = None
        if module_id:
            # Execute the database lookup with the filters specified below.
            module_obj = Modules.query.filter(Modules.Id == int(module_id)).first()
            self.ensure_module_file_identity(module_obj, name)

        project = Projects(
            Name=name,
            FirstName=name,
            Language=language,
            ClassId=class_id,
            ModuleId=int(module_id) if module_id else None,
            solutionpath=file_path,
            AsnDescriptionPath=description_path,
            AdditionalFilePath=additional_file_path,
        )

        # Stage the new records in the current database transaction.
        db.session.add(project)
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

        if bool(checkpoints_enabled):
            pp = Checkpoints(
                ProjectId=project.Id,
                Enabled=True,
                Name="Checkpoint 1",
                FirstName="Checkpoint 1",
                CheckpointNumber=1,
                Language=language,
                solutionpath=None,
                AsnDescriptionPath=None,
                AdditionalFilePath="[]",
            )
            # Stage the new records in the current database transaction.
            db.session.add(pp)
            # Commit the pending database changes so they persist beyond this request.
            db.session.commit()

        return project.Id

    def set_checkpoints_enabled(self, project_id: int, enabled: bool):
        """Set checkpoints enabled.

        Inputs: project_id, enabled.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        proj = Projects.query.filter(Projects.Id == int(project_id)).first()
        # Handle the case where not proj.
        if not proj:
            return
        if bool(enabled):
            existing = self.list_checkpoints(int(project_id))
            if not existing:
                self.create_checkpoint(int(project_id), name="Checkpoint 1")
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    def get_checkpoints_enabled(self, project_id: int) -> bool:
        """Return checkpoints enabled.

        Inputs: project_id."""
        # Execute the database lookup with the filters specified below.
        q = Checkpoints.query.filter(
            Checkpoints.ProjectId == int(project_id),
            Checkpoints.Enabled == True,
        ).first()
        return bool(q)

    def get_project(self, project_id: int, checkpoint_id: Optional[int] = None) -> Projects:
        """Return project.

        Inputs: project_id, checkpoint_id."""
        # Execute the database lookup with the filters specified below.
        project_data = Projects.query.filter(Projects.Id == project_id).first()
        if project_data is None:
            return {}
        pp = None
        if checkpoint_id:
            # Execute the database lookup with the filters specified below.
            pp = Checkpoints.query.filter(
                Checkpoints.Id == int(checkpoint_id),
                Checkpoints.ProjectId == int(project_id),
            ).first()

        if checkpoint_id and pp is None:
            return {}
        project = {}
        module = self.get_module_by_project_id(project_id)
        start_value = module.Start if module and module.Start else chicago_now()
        end_value = module.End if module and module.End else start_value
        start_string = start_value.strftime("%Y-%m-%dT%H:%M:%S")
        end_string = end_value.strftime("%Y-%m-%dT%H:%M:%S")

        if checkpoint_id:
            project_solutionFile = self.basename_or_empty(
                pp.solutionpath if (pp and pp.solutionpath) else ""
            )
            project_descriptionfile = self.basename_or_empty(
                pp.AsnDescriptionPath if (pp and pp.AsnDescriptionPath) else ""
            )
            add_field = (getattr(pp, "AdditionalFilePath", "") if pp else "") or ""
        else:
            project_solutionFile = self.basename_or_empty(
                getattr(project_data, "solutionpath", "") or ""
            )
            project_descriptionfile = self.basename_or_empty(
                getattr(project_data, "AsnDescriptionPath", "") or ""
            )
            add_field = getattr(project_data, "AdditionalFilePath", "") or ""

        project_additionalfiles = [os.path.basename(p) for p in self.json_list_field(add_field)]

        display_name = project_data.Name
        checkpoint_num = 0

        if checkpoint_id and pp and getattr(pp, "Name", None):
            display_name = pp.Name
            checkpoint_num = int(getattr(pp, "CheckpointNumber", 0) or 0)

        project[project_data.Id] = [
            str(display_name),
            str(project_data.Name),
            str(start_string),
            str(end_string),
            str(project_data.Language),
            str(project_solutionFile),
            str(project_descriptionfile),
            project_additionalfiles,
            self.get_checkpoints_enabled(project_data.Id),
            checkpoint_num,
        ]
        return project

    def edit_project(
        self,
        name: str,
        language: str,
        project_id: int,
        path: str,
        description_path: str,
        additional_file_path: str,
        checkpoints_enabled: bool = False,
    ):
        """Handle edit project for this component.

        Inputs: name, language, project_id, path, description_path, additional_file_path, checkpoints_enabled.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == project_id).first()
        if not getattr(project, "FirstName", None):
            project.FirstName = getattr(project, "Name", None) or name
        module_obj = self.get_module_by_project_id(project_id)
        self.ensure_module_file_identity(module_obj, name)
        project.Name = name
        project.Language = language
        project.solutionpath = path
        project.AsnDescriptionPath = description_path
        project.AdditionalFilePath = additional_file_path

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

        self.set_checkpoints_enabled(project_id, bool(checkpoints_enabled))

    def get_testcase(self, testcase_id: int):
        """Return a testcase so callers can authorize its owning assignment."""
        return Testcases.query.filter(Testcases.Id == int(testcase_id)).first()

    def get_testcases(
        self, project_id: int, checkpoint_id: Optional[int] = None
    ) -> Dict[int, list]:
        """Return testcases.

        Inputs: project_id, checkpoint_id."""
        # Execute the database lookup with the filters specified below.
        q = Testcases.query.filter(Testcases.ProjectId == int(project_id))
        if checkpoint_id:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))

        # Execute the database lookup with the filters specified below.
        testcases = q.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all()
        testcase_info: Dict[int, list] = {}

        # Process each (index, test) from enumerate(testcases, start=1).
        for index, test in enumerate(testcases, start=1):
            testcase_info[test.Id] = [
                test.Id,
                test.Name,
                test.input,
                test.Output,
                bool(getattr(test, "Hidden", False)),
                index,
            ]

        return testcase_info

    def add_or_update_testcase(
        self,
        project_id: int,
        testcase_id: int,
        name: str,
        input_data: str,
        output: str,
        class_id: int,
        hidden: bool = False,
        sort_order: Optional[int] = None,
        checkpoint_id: Optional[int] = None,
        *,
        recompute_output: bool = True,
    ):
        """Handle add or update testcase for this component.

        Inputs: project_id, testcase_id, name, input_data, output, class_id, hidden, sort_order, checkpoint_id.
        Database changes are committed at the explicit transaction boundaries below.
        Invokes the configured external runner; preserve its timeout and output handling."""
        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == project_id).first()
        if project is None or int(project.ClassId) != int(class_id):
            raise ValueError("Assignment does not belong to this class")
        testcase = self.get_testcase(testcase_id)
        checkpoint_key = int(checkpoint_id) if checkpoint_id else None
        if testcase is not None and (
            int(testcase.ProjectId) != int(project_id)
            or testcase.CheckpointId != checkpoint_key
        ):
            raise ValueError("Testcase does not belong to this assignment scope")
        pp = None
        if checkpoint_id:
            # Execute the database lookup with the filters specified below.
            pp = Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()

        if checkpoint_id:
            # Reject this case with the exception below.
            if pp is None or int(pp.ProjectId or 0) != int(project_id):
                raise ValueError("Checkpoint does not belong to this assignment")
            if not getattr(pp, "solutionpath", None):
                raise ValueError("Checkpoint has no solution files")
            project_base = pp.solutionpath
        else:
            # Reject this case with the exception below.
            if not project or not getattr(project, "solutionpath", None):
                raise ValueError("Assignment has no solution files")
            project_base = project.solutionpath

        grading_script = "/tabot-files/grading-scripts/grade.py"

        add_path = (
            getattr(pp, "AdditionalFilePath", "")
            if pp
            else getattr(project, "AdditionalFilePath", "")
        ) or ""
        add_path = self.expand_additional_paths(add_path, project_base)

        if recompute_output:
            # Launch the external command with the execution options below.
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        grading_script,
                        "ADMIN",
                        normalize_grader_language(
                            (pp.Language if (pp and pp.Language) else project.Language), project_base
                        ),
                        input_data,
                        project_base,
                        add_path,
                        str(project_id),
                        str(class_id),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=120,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError("Could not run the solution to compute testcase output") from exc

            if result.returncode != 0:
                raise ValueError("Could not run the solution to compute testcase output")
            output = (result.stdout or "").replace("\r\n", "\n").replace("\r", "\n")

        if testcase is None:
            if sort_order is None or int(sort_order or 0) <= 0:
                # Execute the database lookup with the filters specified below.
                scope = Testcases.query.filter(Testcases.ProjectId == int(project_id))
                if checkpoint_id:
                    scope = scope.filter(Testcases.CheckpointId == int(checkpoint_id))
                else:
                    scope = scope.filter(Testcases.CheckpointId.is_(None))
                max_order = scope.with_entities(func.max(Testcases.SortOrder)).scalar()
                sort_order = (
                    max(
                        int(max_order or 0),
                        int(scope.count() or 0),
                    )
                    + 1
                )

            testcase = Testcases(
                ProjectId=project_id,
                CheckpointId=(int(checkpoint_id) if checkpoint_id else None),
                Name=name,
                input=input_data,
                Output=output,
                Hidden=bool(hidden),
                SortOrder=int(sort_order or 1),
                Checkpoint=bool(checkpoint_id),
            )
            # Stage the new records in the current database transaction.
            db.session.add(testcase)
        else:
            testcase.Name = name
            testcase.input = input_data
            testcase.Output = output
            testcase.Hidden = bool(hidden)
            if sort_order is not None and int(sort_order or 0) > 0:
                testcase.SortOrder = int(sort_order)
            testcase.CheckpointId = int(checkpoint_id) if checkpoint_id else None
            testcase.Checkpoint = bool(checkpoint_id)

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    def remove_testcase(self, testcase_id: int):
        """Remove testcase.

        Inputs: testcase_id.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        testcase = Testcases.query.filter(Testcases.Id == testcase_id).first()
        # Handle the case where testcase is None.
        if testcase is None:
            return

        project_id = int(testcase.ProjectId)
        checkpoint_id = int(testcase.CheckpointId) if testcase.CheckpointId is not None else None
        # Mark this record for deletion in the current transaction.
        db.session.delete(testcase)
        # Send pending changes to the database without committing the transaction yet.
        db.session.flush()

        # Execute the database lookup with the filters specified below.
        q = Testcases.query.filter(Testcases.ProjectId == project_id)
        if checkpoint_id is not None:
            q = q.filter(Testcases.CheckpointId == checkpoint_id)
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))
        # Process each (index, remaining) from enumerate(q.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all(), start=1).
        for index, remaining in enumerate(
            q.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all(),
            start=1,
        ):
            remaining.SortOrder = index

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    def reorder_testcases(
        self,
        project_id: int,
        testcase_ids: list[int],
        checkpoint_id: Optional[int] = None,
    ):
        """Reorder testcases.

        Inputs: project_id, testcase_ids, checkpoint_id.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        q = Testcases.query.filter(Testcases.ProjectId == int(project_id))
        if checkpoint_id:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))

        # Execute the database lookup with the filters specified below.
        testcases = q.all()
        by_id = {int(test.Id): test for test in testcases}
        ordered_ids = [int(testcase_id) for testcase_id in testcase_ids]

        # Reject this case with the exception below.
        if len(ordered_ids) != len(set(ordered_ids)):
            raise ValueError("Duplicate testcase id")
        # Reject this case with the exception below.
        if set(ordered_ids) != set(by_id):
            raise ValueError("Testcase order must include every testcase exactly once")

        # Process each (index, testcase_id) from enumerate(ordered_ids, start=1).
        for index, testcase_id in enumerate(ordered_ids, start=1):
            by_id[testcase_id].SortOrder = index

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    def count_testcases(self, project_id: int, checkpoint_id: Optional[int] = None) -> int:
        """Count testcases.

        Inputs: project_id, checkpoint_id."""
        # Execute the database lookup with the filters specified below.
        q = Testcases.query.filter(Testcases.ProjectId == int(project_id))
        if checkpoint_id:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))
        return int(q.count() or 0)

    def count_testcases_by_checkpoint(self, project_id: int) -> Dict[int, int]:
        """Count testcases by checkpoint.

        Inputs: project_id."""
        # Execute the database lookup with the filters specified below.
        rows = (
            db.session.query(Testcases.CheckpointId, func.count(Testcases.Id))
            .filter(
                Testcases.ProjectId == int(project_id),
                Testcases.CheckpointId.isnot(None),
            )
            .group_by(Testcases.CheckpointId)
            .all()
        )
        return {int(ppid): int(count or 0) for ppid, count in rows if ppid is not None}

    def testcases_to_json(self, project_id: int, checkpoint_id: Optional[int] = None) -> str:
        """Handle testcases to json for this component.

        Inputs: project_id, checkpoint_id."""
        testcase_holder: list[list] = []
        # Execute the database lookup with the filters specified below.
        proj = Projects.query.filter(Projects.Id == project_id).first()
        owner = proj
        if checkpoint_id:
            owner = self.get_checkpoint(int(checkpoint_id))
            if owner is None or int(owner.ProjectId or 0) != int(project_id):
                raise ValueError("Checkpoint does not belong to this assignment")
        add_field = getattr(owner, "AdditionalFilePath", "") if owner else ""
        add_list = self.json_list_field(add_field)

        base_dir = ""
        if owner and getattr(owner, "solutionpath", ""):
            sp = getattr(owner, "solutionpath", "")
            base_dir = sp if os.path.isdir(sp) else os.path.dirname(sp)

        try:
            add_list = json.loads(self.expand_additional_paths(json.dumps(add_list), base_dir))
        # Ignore this failure and allow the surrounding operation to continue.
        except Exception:
            pass

        # Execute the database lookup with the filters specified below.
        q = Testcases.query.filter(Testcases.ProjectId == project_id)
        if checkpoint_id:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))

        # Execute the database lookup with the filters specified below.
        tests = q.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all()

        # Process each (index, test) from enumerate(tests, start=1).
        for index, test in enumerate(tests, start=1):
            testcase_holder.append(
                [
                    test.Name,
                    test.input,
                    test.Output,
                    bool(getattr(test, "Hidden", False)),
                    add_list,
                    index,
                ]
            )

        return json.dumps(testcase_holder)

    def get_className_by_projectId(self, project_id):
        """Return class name by project id.

        Inputs: project_id."""
        try:
            pid = int(project_id)
        # Convert this failure into the fallback result or error response below.
        except (TypeError, ValueError):
            return ""

        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == pid).first()
        # Return an empty or negative result when this guard matches.
        if project is None:
            return ""

        # Execute the database lookup with the filters specified below.
        class_obj = Classes.query.filter(Classes.Id == project.ClassId).first()
        # Return an empty or negative result when this guard matches.
        if class_obj is None:
            return ""

        return class_obj.Name

    def get_class_id_by_name(self, class_name):
        """Return class id by name.

        Inputs: class_name."""
        class_id = Classes.query.filter(Classes.Name == class_name).first().Id
        return class_id

    def get_project_path(self, project_id, checkpoint_id: Optional[int] = None):
        """Return project path.

        Inputs: project_id, checkpoint_id."""
        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == project_id).first()
        # Return an empty or negative result when this guard matches.
        if not project:
            return ""
        if checkpoint_id:
            # Execute the database lookup with the filters specified below.
            pp = Checkpoints.query.filter(
                Checkpoints.Id == int(checkpoint_id),
                Checkpoints.ProjectId == int(project_id),
            ).first()
            return pp.solutionpath if (pp and pp.solutionpath) else ""
        return project.solutionpath

    def get_project_desc_path(self, project_id, checkpoint_id: Optional[int] = None):
        """Return project desc path.

        Inputs: project_id, checkpoint_id."""
        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == project_id).first()
        # Return an empty or negative result when this guard matches.
        if not project:
            return ""
        if checkpoint_id:
            # Execute the database lookup with the filters specified below.
            pp = Checkpoints.query.filter(
                Checkpoints.Id == int(checkpoint_id),
                Checkpoints.ProjectId == int(project_id),
            ).first()
            return pp.AsnDescriptionPath if (pp and pp.AsnDescriptionPath) else ""
        return project.AsnDescriptionPath

    def get_project_desc_file(self, project_id, checkpoint_id: Optional[int] = None):
        """Return project desc file.

        Inputs: project_id, checkpoint_id."""
        filepath = self.get_project_desc_path(project_id, checkpoint_id=checkpoint_id)
        # Handle the case where not filepath.
        if not filepath:
            return b""
        # Open the file for reading and close it automatically when this block finishes.
        with open(filepath, "rb") as file:
            file_contents = file.read()
        return file_contents

    def get_student_grade(self, project_id, user_id):
        """Return student grade.

        Inputs: project_id, user_id."""
        # Execute the database lookup with the filters specified below.
        student_progress = MainAssignmentGrades.query.filter(
            and_(
                MainAssignmentGrades.UserId == user_id,
                MainAssignmentGrades.ProjectId == project_id,
            )
        ).first()

        # Handle the case where student_progress is None.
        if student_progress is None:
            return 0

        return student_progress.Grade

    def set_student_grade(self, project_id, user_id, grade):
        """Set student grade.

        Inputs: project_id, user_id, grade.
        Database changes are committed at the explicit transaction boundaries below."""
        project_id = int(project_id)
        user_id = int(user_id)

        # Execute the database lookup with the filters specified below.
        latest_submission = (
            Submissions.query.filter(
                Submissions.Project == project_id,
                Submissions.User == user_id,
                Submissions.IsCheckpoint == False,
            )
            .order_by(Submissions.Time.desc(), Submissions.Id.desc())
            .first()
        )

        # Grades are keyed by the submission they describe. A grade without a
        # submission cannot be displayed or edited reliably, so leave the
        # database unchanged when the student has not submitted this project.
        if latest_submission is None:
            return False

        # Execute the database lookup with the filters specified below.
        student_grade = MainAssignmentGrades.query.filter(
            and_(
                MainAssignmentGrades.UserId == user_id,
                MainAssignmentGrades.ProjectId == project_id,
            )
        ).first()

        if student_grade is None:
            student_grade = MainAssignmentGrades(
                SubmissionId=int(latest_submission.Id),
                UserId=user_id,
                ProjectId=project_id,
                Grade=int(grade),
                UpdatedAt=chicago_now(),
            )
            # Stage the new records in the current database transaction.
            db.session.add(student_grade)
        else:
            student_grade.SubmissionId = int(latest_submission.Id)
            student_grade.Grade = int(grade)
            student_grade.UpdatedAt = chicago_now()

        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return True
