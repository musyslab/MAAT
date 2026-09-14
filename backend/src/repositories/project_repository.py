import os
import subprocess
from typing import Optional, Dict
from .models import (
    DefaultContentImports,
    Projects,
    Checkpoints,
    MainAssignmentGrades,
    Submissions,
    Testcases,
    Classes,
    Modules,
    ClassAssignments,
    StudentHiddenModules,
)
from src.repositories.database import db
from sqlalchemy import and_, func
from datetime import datetime
import json


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


class ProjectRepository():

    DEFAULT_ROOT = '/tabot-files/project-files/Default-Assignment-Content'

    def _default_catalog(self):
        """Folder identities do not depend on companion filenames."""
        from pathlib import Path
        import re
        root = Path(self.DEFAULT_ROOT)
        if not root.is_dir():
            raise ValueError('Default assignment content folder is missing.')
        def natural(path):
            return [int(v) if v.isdigit() else v.casefold()
                    for v in re.split(r'(\d+)', path.name)]
        items = []
        for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=natural):
            for assignment in sorted((p for p in folder.iterdir() if p.is_dir()), key=natural):
                match = re.fullmatch(r'Checkpoint\s*#?\s*([1-9]\d*)', assignment.name, re.I)
                if assignment.name.casefold() != 'main project' and not match:
                    continue
                number = int(match.group(1)) if match else None
                key = folder.name + '/' + ('main' if number is None else f'checkpoint-{number}')
                if any(item['key'] == key for item in items):
                    raise ValueError(f'Duplicate assignment folder: {assignment}')
                items.append(dict(key=key, module=folder.name, label=assignment.name,
                                  number=number, path=assignment))
        return items

    def _default_files(self, item):
        files = [p for p in item['path'].iterdir() if p.is_file()]
        def single(extension, candidates=files):
            matches = [p for p in candidates if p.suffix.lower() == extension]
            if len(matches) != 1:
                raise ValueError(f"{item['module']}/{item['label']}: expected exactly one {extension} file; found {len(matches)}")
            return matches[0]
        source, pdf, cases_file = single('.py'), single('.pdf'), single('.json')
        presentation = single('.pdf', [p for p in item['path'].parent.iterdir() if p.is_file()])
        cases = json.loads(cases_file.read_text(encoding='utf-8-sig'))
        if not isinstance(cases, list) or not cases:
            raise ValueError(f"{item['key']}: expected a nonempty testcase array")
        orders = set()
        for case in cases:
            if (not isinstance(case, dict)
                    or any(not isinstance(case.get(k), str) for k in ('name', 'input', 'output'))
                    or type(case.get('hidden')) is not bool
                    or type(case.get('order')) is not int
                    or case['order'] < 1 or case['order'] in orders):
                raise ValueError(f"{item['key']}: invalid testcase or duplicate order")
            orders.add(case['order'])
        return source, pdf, cases_file, presentation, cases

    def _default_existing(self, cls, items):
        existing = {row.SourceKey: row for row in DefaultContentImports.query.filter_by(ClassId=cls.Id).all()}
        # Recognize the previous one-shot importer without changing rows on GET.
        # FirstName survives teacher renames; the ID suffix identifies its modules.
        if cls.DefaultContentInitialized:
            from types import SimpleNamespace
            for module in Modules.query.filter_by(ClassId=cls.Id).all():
                if any(row.ModuleId == module.Id for row in existing.values()):
                    continue
                if not (module.FileTimestamp or '').endswith(f'_{module.Id}'):
                    continue
                project = self.get_main_project_for_module(module.Id)
                if not project:
                    continue
                for item in items:
                    if item['key'] in existing or item['module'] != module.FirstName:
                        continue
                    target = project if item['number'] is None else next((
                        cp for cp in Checkpoints.query.filter_by(ProjectId=project.Id).all()
                        if (cp.FirstName or '').startswith(f"Checkpoint {item['number']}: ")), None)
                    if target and target.solutionpath:
                        existing[item['key']] = SimpleNamespace(ModuleId=module.Id, AssignmentId=target.Id)
        return existing

    def default_content_options(self, class_id):
        cls = Classes.query.filter_by(Id=int(class_id)).first()
        if not cls or not cls.School.UseDefaultMaterials:
            return dict(enabled=False, items=[])
        items = self._default_catalog()
        existing = self._default_existing(cls, items)
        result = []
        for item in items:
            error = None
            if item['key'] not in existing:
                try:
                    self._default_files(item)
                except (ValueError, OSError) as exc:
                    error = str(exc)
            result.append(dict(key=item['key'], module=item['module'], label=item['label'],
                               imported=item['key'] in existing, error=error))
        return dict(enabled=True, items=result)

    def import_default_content(self, class_id, selected):
        from datetime import timedelta
        from pathlib import Path
        if not isinstance(selected, list) or not selected or any(not isinstance(k, str) for k in selected):
            raise ValueError('Select at least one project.')
        try:
            cls = (Classes.query.filter_by(Id=int(class_id)).populate_existing().with_for_update().one())
            db.session.refresh(cls.School)
            if not cls.School.UseDefaultMaterials:
                raise PermissionError('Default assignment content is disabled for this school.')
            items = self._default_catalog()
            catalog = {item['key']: item for item in items}
            if set(selected) - catalog.keys():
                raise ValueError('The catalog changed. Refresh and select projects again.')
            existing = self._default_existing(cls, items)
            pending = [item for item in items if item['key'] in selected and item['key'] not in existing]
            validated = [(item, self._default_files(item)) for item in pending]
            now = datetime.now()
            modules = {}
            for key, row in existing.items():
                if key in catalog:
                    modules[catalog[key]['module']] = self.get_module(row.ModuleId)
                if not isinstance(row, DefaultContentImports):
                    db.session.add(DefaultContentImports(ClassId=cls.Id, SourceKey=key,
                        ModuleId=row.ModuleId, AssignmentId=row.AssignmentId))
            for item, (source, pdf, cases_file, presentation, cases) in validated:
                module = modules.get(item['module'])
                if module is None:
                    module = Modules(ClassId=cls.Id, Name=item['module'], FirstName=item['module'],
                                     Start=now, End=now + timedelta(days=365))
                    db.session.add(module)
                    db.session.flush()
                    module.FileTimestamp = f'{now:%Y%m%d_%H%M%S}_{module.Id}'
                    modules[item['module']] = module
                    project = Projects(ClassId=cls.Id, ModuleId=module.Id, Name=item['module'],
                                       FirstName=item['module'], Language='python', AdditionalFilePath='[]')
                    db.session.add(project)
                    db.session.flush()
                else:
                    project = self.get_main_project_for_module(module.Id)
                    if project is None:
                        raise ValueError('The imported module no longer has its main project.')
                if item['number'] is None:
                    target = project
                    if target.solutionpath or target.AsnDescriptionPath:
                        raise ValueError(f"{item['module']}: main project already contains teacher content.")
                    target.Name = target.FirstName = source.stem
                else:
                    # Append without renumbering existing teacher work.
                    used = [cp.CheckpointNumber or 0 for cp in Checkpoints.query.filter_by(ProjectId=project.Id).all()]
                    number = item['number'] if item['number'] not in used else max(used + [0]) + 1
                    name = f"Checkpoint {item['number']}: {source.stem}"
                    target = Checkpoints(ProjectId=project.Id, CheckpointNumber=number, Enabled=True,
                                         Name=name, FirstName=name, Language='python', AdditionalFilePath='[]')
                    db.session.add(target)
                    db.session.flush()
                # Class-owned database rows reference the same canonical files.
                # Upload endpoints create private version directories on edits.
                target.solutionpath = str(source.parent.resolve())
                target.AsnDescriptionPath = str(pdf.resolve())
                target.Language = 'python'
                for case in sorted(cases, key=lambda case: case['order']):
                    db.session.add(Testcases(ProjectId=project.Id,
                        CheckpointId=target.Id if item['number'] is not None else None,
                        Name=case['name'], input=case['input'], Output=case['output'],
                        Hidden=case['hidden'], SortOrder=case['order'], Checkpoint=item['number'] is not None))
                db.session.add(DefaultContentImports(ClassId=cls.Id, SourceKey=item['key'],
                                                     ModuleId=module.Id, AssignmentId=target.Id))
            if pending:
                cls.DefaultContentInitialized = True
            db.session.commit()
            return dict(imported=len(pending), skipped=len(set(selected)) - len(pending))
        except Exception:
            db.session.rollback()
            raise

    def default_module_presentation(self, module):
        """Shared fallback; a teacher-uploaded presentation takes precedence."""
        from pathlib import Path
        if module is None:
            return None
        rows = DefaultContentImports.query.filter_by(
            ClassId=module.ClassId, ModuleId=module.Id).all()
        names = {row.SourceKey.rsplit('/', 1)[0] for row in rows}
        root = Path(self.DEFAULT_ROOT).resolve()
        for name in sorted(names):
            folder = (root / name).resolve()
            if folder.parent != root or not folder.is_dir():
                continue
            pdfs = sorted(p for p in folder.iterdir()
                          if p.is_file() and p.suffix.lower() == '.pdf')
            if len(pdfs) == 1:
                return str(pdfs[0])
        return None

    def deduplicate_default_content(self, apply=False):
        """Run with application writers stopped. Default is a read-only preview.

        Relink only byte-identical files in the old importer's directory layout.
        Teacher-modified assignments and extra files are deliberately retained.
        Commit references before removing any duplicates; never delete recursively.
        """
        from pathlib import Path
        from werkzeug.utils import secure_filename
        import filecmp
        report = dict(assignments=[], presentations=[], skipped=[], cleanup_errors=[])
        cleanup = set()
        items = self._default_catalog()
        def segment(value):
            return secure_filename(str(value)) or 'content'
        try:
            for cls in Classes.query.all():
                existing = self._default_existing(cls, items)
                for item in items:
                    row = existing.get(item['key'])
                    if row is None:
                        continue
                    module = self.get_module(row.ModuleId)
                    project = self.get_main_project_for_module(row.ModuleId)
                    if not module or not project or module.ClassId != cls.Id:
                        continue
                    target = (project if item['number'] is None else
                              Checkpoints.query.filter_by(Id=row.AssignmentId,
                                                          ProjectId=project.Id).first())
                    if not target or target.Id != row.AssignmentId:
                        continue
                    try:
                        source, pdf, cases_file, presentation, _ = self._default_files(item)
                    except (ValueError, OSError) as exc:
                        report['skipped'].append(str(exc))
                        continue
                    module_dir = (Path('/tabot-files/project-files') /
                                  segment(cls.School.Name) / segment(cls.Name) /
                                  'teacher-files' /
                                  f'{module.FileTimestamp}_{segment(module.FirstName)}')
                    old = Path(target.solutionpath or '')
                    expected_parent = (module_dir /
                                       ('main' if item['number'] is None else 'checkpoint') /
                                       f'default_{target.Id}')
                    originals = (source, pdf, cases_file)
                    can_relink = (
                        old.is_dir() and not old.is_symlink()
                        and old.parent == expected_parent
                        and Path(target.AsnDescriptionPath or '') == old / pdf.name
                        and not self.json_list_field(target.AdditionalFilePath)
                        and {p.name for p in old.iterdir()} == {p.name for p in originals}
                        and all((old / p.name).is_file() and not (old / p.name).is_symlink()
                                and filecmp.cmp(old / p.name, p, shallow=False)
                                for p in originals))
                    if can_relink:
                        report['assignments'].append(dict(class_id=cls.Id, key=item['key']))
                        if apply:
                            target.solutionpath = str(source.parent.resolve())
                            target.AsnDescriptionPath = str(pdf.resolve())
                            cleanup.update(old / p.name for p in originals)
                    elif old.resolve() != source.parent.resolve():
                        report['skipped'].append(f"Class {cls.Id}: {item['key']} (customized or unrecognized files)")
                    # Record legacy imports so presentation fallback also works.
                    if apply and not isinstance(row, DefaultContentImports):
                        db.session.add(DefaultContentImports(ClassId=cls.Id,
                            SourceKey=item['key'], ModuleId=row.ModuleId,
                            AssignmentId=row.AssignmentId))
                    local_pdf = module_dir / presentation.name
                    if (local_pdf.is_file() and not local_pdf.is_symlink()
                            and filecmp.cmp(local_pdf, presentation, shallow=False)):
                        if str(local_pdf) not in report['presentations']:
                            report['presentations'].append(str(local_pdf))
                        if apply:
                            cleanup.add(local_pdf)
            if apply:
                db.session.commit()
        except Exception:
            if apply:
                db.session.rollback()
            raise
        # The caller must stop web/grading workers during migration, preventing
        # uploads or reads from racing the database switch and file cleanup.
        for path in sorted(cleanup):
            try:
                path.unlink()
            except OSError as exc:
                report['cleanup_errors'].append(f'{path}: {exc}')
        return report

    def json_list_field(self, raw: str) -> list[str]:
        try:
            s = (raw or "").strip()
            if not s:
                return []
            vals = json.loads(s) if s.startswith("[") else [s]
            return [v for v in (vals or []) if v]
        except Exception:
            return []

    def basename_or_empty(self, p: str) -> str:
        return os.path.basename(p) if p else ""

    def expand_additional_paths(self, add_path: str, project_base: str) -> str:
        """
        grade.py expects JSON list of absolute paths.
        DB may store basenames or JSON list of basenames.
        """
        try:
            base_dir = project_base if os.path.isdir(project_base) else os.path.dirname(project_base)
            lst = self.json_list_field(add_path)
            abs_list = []
            for p in (lst or []):
                if not p:
                    continue
                if os.path.isabs(p):
                    abs_list.append(p)
                else:
                    abs_list.append(os.path.join(base_dir, os.path.basename(p)))
            return json.dumps(abs_list)
        except Exception:
            return add_path or ""

    def _coerce_datetime(self, value):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    def _new_file_timestamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def ensure_module_file_identity(self, module: Modules | None, fallback_name: str = "") -> Modules | None:
        if not module:
            return None

        changed = False

        if not getattr(module, "FirstName", None):
            module.FirstName = getattr(module, "Name", None) or fallback_name or "module"
            changed = True

        if not getattr(module, "FileTimestamp", None):
            module.FileTimestamp = self._new_file_timestamp()
            changed = True

        if changed:
            db.session.commit()

        return module

    def create_module(self, class_id: int, name: str, start: datetime, end: datetime) -> int:
        module = Modules(
            ClassId=int(class_id),
            Name=name,
            FirstName=name,
            FileTimestamp=self._new_file_timestamp(),
            Start=self._coerce_datetime(start),
            End=self._coerce_datetime(end),
        )
        db.session.add(module)
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
        db.session.add(project)
        db.session.commit()

        self.create_checkpoint(project.Id, name="Checkpoint 1")
        return int(module.Id)

    def get_modules_by_class_id(self, class_id: int):
        return (
            Modules.query
            .filter(Modules.ClassId == int(class_id))
            .order_by(Modules.Start.asc(), Modules.Id.asc())
            .all()
        )

    def get_module(self, module_id: int):
        return Modules.query.filter(Modules.Id == int(module_id)).first()

    def get_hidden_module_ids_for_student(self, class_id: int, user_id: int) -> set[int]:
        rows = (
            db.session.query(StudentHiddenModules.ModuleId)
            .join(Modules, StudentHiddenModules.ModuleId == Modules.Id)
            .filter(
                Modules.ClassId == int(class_id),
                StudentHiddenModules.UserId == int(user_id),
            )
            .all()
        )

        return {
            int(row[0])
            for row in rows
            if row and row[0] is not None and int(row[0] or 0) > 0
        }

    def get_hidden_module_ids_by_student_for_class(self, class_id: int) -> dict[int, list[int]]:
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
        for user_id, module_id in rows:
            user_id_int = int(user_id or 0)
            module_id_int = int(module_id or 0)

            if user_id_int <= 0 or module_id_int <= 0:
                continue

            hidden_by_student.setdefault(user_id_int, []).append(module_id_int)

        return hidden_by_student

    def set_student_module_hidden(self, user_id: int, module_id: int, hidden: bool) -> bool:
        user_id = int(user_id)
        module_id = int(module_id)

        existing = StudentHiddenModules.query.filter(
            StudentHiddenModules.UserId == user_id,
            StudentHiddenModules.ModuleId == module_id,
        ).first()

        if hidden and existing is None:
            db.session.add(StudentHiddenModules(
                UserId=user_id,
                ModuleId=module_id,
                CreatedAt=datetime.now(),
            ))

        if not hidden and existing is not None:
            db.session.delete(existing)

        db.session.commit()
        return True

    def set_module_hidden_for_class(self, module_id: int, hidden: bool) -> list[int]:
        module = Modules.query.filter(Modules.Id == int(module_id)).first()
        if not module:
            return []

        student_rows = (
            db.session.query(ClassAssignments.UserId)
            .filter(ClassAssignments.ClassId == int(module.ClassId))
            .order_by(ClassAssignments.UserId.asc())
            .all()
        )
        student_ids = [int(row[0]) for row in student_rows if row and int(row[0] or 0) > 0]

        if hidden:
            existing_rows = (
                db.session.query(StudentHiddenModules.UserId)
                .filter(StudentHiddenModules.ModuleId == int(module_id))
                .all()
            )
            existing_user_ids = {int(row[0]) for row in existing_rows if row and int(row[0] or 0) > 0}

            for student_id in student_ids:
                if student_id not in existing_user_ids:
                    db.session.add(StudentHiddenModules(
                        UserId=student_id,
                        ModuleId=int(module_id),
                        CreatedAt=datetime.now(),
                    ))
        else:
            StudentHiddenModules.query.filter(
                StudentHiddenModules.ModuleId == int(module_id)
            ).delete(synchronize_session=False)

        db.session.commit()
        return student_ids

    def get_module_by_project_id(self, project_id: int):
        project = Projects.query.filter(Projects.Id == int(project_id)).first()
        if not project:
            return None
        if getattr(project, "ModuleId", None):
            return Modules.query.filter(Modules.Id == int(project.ModuleId)).first()

        now = datetime.now()
        module = Modules(
            ClassId=project.ClassId,
            Name=project.Name,
            FirstName=project.Name,
            FileTimestamp=self._new_file_timestamp(),
            Start=now,
            End=now,
        )
        db.session.add(module)
        db.session.commit()
        project.ModuleId = module.Id
        db.session.commit()
        return module

    def get_main_project_for_module(self, module_id: int):
        return (
            Projects.query
            .filter(Projects.ModuleId == int(module_id))
            .order_by(Projects.Id.asc())
            .first()
        )

    def update_module(self, module_id: int, name: str, start: datetime, end: datetime):
        module = Modules.query.filter(Modules.Id == int(module_id)).first()
        if not module:
            return None

        if not getattr(module, "FirstName", None):
            module.FirstName = getattr(module, "Name", None) or name
        if not getattr(module, "FileTimestamp", None):
            module.FileTimestamp = self._new_file_timestamp()

        module.Name = name
        module.Start = self._coerce_datetime(start)
        module.End = self._coerce_datetime(end)

        db.session.commit()
        return module

    def update_project_name(self, project_id: int, name: str):
        project = Projects.query.filter(Projects.Id == int(project_id)).first()
        if not project:
            return None
        project.Name = name
        db.session.commit()
        return project

    def update_checkpoint_name(self, checkpoint_id: int, name: str):
        pp = Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()
        if not pp:
            return None
        pp.Name = name
        db.session.commit()
        return pp

    def list_checkpoints(self, project_id: int):
        return (
            Checkpoints.query
            .filter(
                Checkpoints.ProjectId == int(project_id),
                Checkpoints.Enabled == True,
            )
            .order_by(Checkpoints.CheckpointNumber.asc(), Checkpoints.Id.asc())
            .all()
        )

    def _next_checkpoint_number_scratch_base(self, project_id: int) -> int:
        max_num = (
            db.session.query(func.max(Checkpoints.CheckpointNumber))
            .filter(Checkpoints.ProjectId == int(project_id))
            .scalar()
        )
        total_rows = (
            Checkpoints.query
            .filter(Checkpoints.ProjectId == int(project_id))
            .count()
        )
        return int(max_num or 0) + int(total_rows or 0) + 1000

    def _checkpoint_rows_for_project(self, project_id: int):
        return (
            Checkpoints.query
            .filter(Checkpoints.ProjectId == int(project_id))
            .order_by(Checkpoints.CheckpointNumber.asc(), Checkpoints.Id.asc())
            .all()
        )

    def _write_checkpoint_order(self, project_id: int, ordered_active_ids: list[int]):
        all_rows = self._checkpoint_rows_for_project(project_id)
        active_rows = [row for row in all_rows if bool(getattr(row, "Enabled", True))]
        inactive_rows = [row for row in all_rows if not bool(getattr(row, "Enabled", True))]

        active_by_id = {int(row.Id): row for row in active_rows}
        ordered_rows = []
        seen: set[int] = set()

        for raw_id in ordered_active_ids or []:
            try:
                row_id = int(raw_id)
            except Exception:
                continue

            row = active_by_id.get(row_id)
            if row is not None and row_id not in seen:
                ordered_rows.append(row)
                seen.add(row_id)

        for row in active_rows:
            row_id = int(row.Id)
            if row_id not in seen:
                ordered_rows.append(row)
                seen.add(row_id)

        final_rows = [*ordered_rows, *inactive_rows]

        scratch_base = self._next_checkpoint_number_scratch_base(project_id)
        for index, row in enumerate(all_rows, start=1):
            row.CheckpointNumber = scratch_base + index

        db.session.flush()

        for index, row in enumerate(final_rows, start=1):
            row.CheckpointNumber = index

        db.session.commit()
        return ordered_rows

    def reorder_checkpoints(self, project_id: int, ordered_ids: list[int]):
        try:
            return self._write_checkpoint_order(project_id, ordered_ids)
        except Exception:
            db.session.rollback()
            raise

    def renumber_checkpoints(self, project_id: int):
        try:
            rows = self.list_checkpoints(project_id)
            return self._write_checkpoint_order(
                project_id,
                [int(row.Id) for row in rows],
            )
        except Exception:
            db.session.rollback()
            raise

    def delete_checkpoint(self, checkpoint_id: int):
        pp = Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()
        if not pp:
            return None

        project = Projects.query.filter_by(Id=pp.ProjectId).first()
        cls = Classes.query.filter_by(Id=project.ClassId).first() if project else None
        legacy_imported = False
        if cls and cls.DefaultContentInitialized:
            try:
                legacy_imported = any(row.AssignmentId == pp.Id for row in self._default_existing(cls, self._default_catalog()).values())
            except (ValueError, OSError):
                pass
        if legacy_imported or DefaultContentImports.query.filter_by(AssignmentId=pp.Id).first():
            raise ValueError("Imported default projects cannot be deleted.")
        project_id = int(pp.ProjectId)
        pp.Enabled = False
        pp.CheckpointNumber = self._next_checkpoint_number_scratch_base(project_id)
        db.session.flush()
        self.renumber_checkpoints(project_id)
        return pp

    def get_checkpoint(self, checkpoint_id: int) -> Optional[Checkpoints]:
        return Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()

    def create_checkpoint(self, project_id: int, *, name: str = "") -> int:
        proj = Projects.query.filter(Projects.Id == int(project_id)).first()
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
        db.session.add(pp)
        db.session.commit()
        return int(pp.Id)

    def get_current_project(self) -> Optional[Projects]:
        now = datetime.now()
        return (
            Projects.query
            .join(Modules, Projects.ModuleId == Modules.Id)
            .filter(Modules.End >= now, Modules.Start < now)
            .order_by(Modules.Start.asc(), Projects.Id.asc())
            .first()
        )

    def get_current_project_by_class(self, class_id: int) -> Optional[Projects]:
        now = datetime.now()
        return (
            Projects.query
            .join(Modules, Projects.ModuleId == Modules.Id)
            .filter(
                Projects.ClassId == class_id,
                Modules.End >= now,
                Modules.Start < now,
            )
            .order_by(Modules.Start.asc(), Projects.Id.asc())
            .first()
        )

    def get_all_projects(self) -> Projects:
        return (
            Projects.query
            .outerjoin(Modules, Projects.ModuleId == Modules.Id)
            .order_by(Modules.End.asc(), Projects.Id.asc())
            .all()
        )

    def get_selected_project(self, project_id: int) -> Projects:
        project = Projects.query.filter(Projects.Id == project_id).first()
        return project

    def get_projects_by_class_id(self, class_id: int) -> int:
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
        module_obj = None
        if module_id:
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

        db.session.add(project)
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
            db.session.add(pp)
            db.session.commit()

        return project.Id

    def set_checkpoints_enabled(self, project_id: int, enabled: bool):
        proj = Projects.query.filter(Projects.Id == int(project_id)).first()
        if not proj:
            return
        if bool(enabled):
            existing = self.list_checkpoints(int(project_id))
            if not existing:
                self.create_checkpoint(int(project_id), name="Checkpoint 1")
        db.session.commit()

    def get_checkpoints_enabled(self, project_id: int) -> bool:
        q = Checkpoints.query.filter(
            Checkpoints.ProjectId == int(project_id),
            Checkpoints.Enabled == True,
        ).first()
        return bool(q)

    def get_project(self, project_id: int, checkpoint_id: Optional[int] = None) -> Projects:
        project_data = Projects.query.filter(Projects.Id == project_id).first()
        pp = None
        if checkpoint_id:
            pp = Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()

        project = {}
        module = self.get_module_by_project_id(project_id)
        start_value = module.Start if module and module.Start else datetime.now()
        end_value = module.End if module and module.End else start_value
        start_string = start_value.strftime("%Y-%m-%dT%H:%M:%S")
        end_string = end_value.strftime("%Y-%m-%dT%H:%M:%S")

        if checkpoint_id:
            project_solutionFile = self.basename_or_empty(pp.solutionpath if (pp and pp.solutionpath) else "")
            project_descriptionfile = self.basename_or_empty(pp.AsnDescriptionPath if (pp and pp.AsnDescriptionPath) else "")
            add_field = (getattr(pp, "AdditionalFilePath", "") if pp else "") or ""
        else:
            project_solutionFile = self.basename_or_empty(getattr(project_data, "solutionpath", "") or "")
            project_descriptionfile = self.basename_or_empty(getattr(project_data, "AsnDescriptionPath", "") or "")
            add_field = (getattr(project_data, "AdditionalFilePath", "") or "")

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

        db.session.commit()

        self.set_checkpoints_enabled(project_id, bool(checkpoints_enabled))

    def get_testcases(self, project_id: int, checkpoint_id: Optional[int] = None) -> Dict[int, list]:
        q = Testcases.query.filter(Testcases.ProjectId == int(project_id))
        if checkpoint_id:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))

        testcases = q.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all()
        testcase_info: Dict[int, list] = {}

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
    ):
        from flask import current_app

        project = Projects.query.filter(Projects.Id == project_id).first()
        pp = None
        if checkpoint_id:
            pp = Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()

        if checkpoint_id:
            if not pp or not getattr(pp, "solutionpath", None):
                raise ValueError("Checkpoint has no solution files")
            project_base = pp.solutionpath
        else:
            if not project or not getattr(project, "solutionpath", None):
                raise ValueError("Assignment has no solution files")
            project_base = project.solutionpath

        grading_script = os.path.join(
            current_app.root_path, "..", "tabot-files", "grading-scripts", "grade.py"
        )

        add_path = (getattr(pp, "AdditionalFilePath", "") if pp else getattr(project, "AdditionalFilePath", "")) or ""
        add_path = self.expand_additional_paths(add_path, project_base)

        result = subprocess.run(
            [
                "python",
                grading_script,
                "ADMIN",
                normalize_grader_language((pp.Language if (pp and pp.Language) else project.Language), project_base),
                input_data,
                project_base,
                add_path,
                str(project_id),
                str(class_id),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )

        recomputed = (result.stdout or "").strip()
        if recomputed:
            output = recomputed

        testcase = Testcases.query.filter(Testcases.Id == testcase_id).first()

        if testcase is None:
            if sort_order is None or int(sort_order or 0) <= 0:
                scope = Testcases.query.filter(Testcases.ProjectId == int(project_id))
                if checkpoint_id:
                    scope = scope.filter(Testcases.CheckpointId == int(checkpoint_id))
                else:
                    scope = scope.filter(Testcases.CheckpointId.is_(None))
                max_order = scope.with_entities(func.max(Testcases.SortOrder)).scalar()
                sort_order = max(
                    int(max_order or 0),
                    int(scope.count() or 0),
                ) + 1

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
            db.session.add(testcase)
        else:
            testcase.Name = name
            testcase.input = input_data
            testcase.Output = output
            testcase.Hidden = bool(hidden)
            if sort_order is not None and int(sort_order or 0) > 0:
                testcase.SortOrder = int(sort_order)
            testcase.CheckpointId = (int(checkpoint_id) if checkpoint_id else None)
            testcase.Checkpoint = bool(checkpoint_id)

        db.session.commit()

    def remove_testcase(self, testcase_id: int):
        testcase = Testcases.query.filter(Testcases.Id == testcase_id).first()
        if testcase is None:
            return

        project_id = int(testcase.ProjectId)
        checkpoint_id = int(testcase.CheckpointId) if testcase.CheckpointId is not None else None
        db.session.delete(testcase)
        db.session.flush()

        q = Testcases.query.filter(Testcases.ProjectId == project_id)
        if checkpoint_id is not None:
            q = q.filter(Testcases.CheckpointId == checkpoint_id)
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))
        for index, remaining in enumerate(
            q.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all(),
            start=1,
        ):
            remaining.SortOrder = index

        db.session.commit()

    def reorder_testcases(
        self,
        project_id: int,
        testcase_ids: list[int],
        checkpoint_id: Optional[int] = None,
    ):
        q = Testcases.query.filter(Testcases.ProjectId == int(project_id))
        if checkpoint_id:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))

        testcases = q.all()
        by_id = {int(test.Id): test for test in testcases}
        ordered_ids = [int(testcase_id) for testcase_id in testcase_ids]

        if len(ordered_ids) != len(set(ordered_ids)):
            raise ValueError("Duplicate testcase id")
        if set(ordered_ids) != set(by_id):
            raise ValueError("Testcase order must include every testcase exactly once")

        for index, testcase_id in enumerate(ordered_ids, start=1):
            by_id[testcase_id].SortOrder = index

        db.session.commit()

    def count_testcases(self, project_id: int, checkpoint_id: Optional[int] = None) -> int:
        q = Testcases.query.filter(Testcases.ProjectId == int(project_id))
        if checkpoint_id:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))
        return int(q.count() or 0)

    def count_testcases_by_checkpoint(self, project_id: int) -> Dict[int, int]:
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
        testcase_holder: list[list] = []
        proj = Projects.query.filter(Projects.Id == project_id).first()
        add_field = getattr(proj, "AdditionalFilePath", "") if proj else ""
        add_list = self.json_list_field(add_field)

        base_dir = ""
        if proj and getattr(proj, "solutionpath", ""):
            sp = getattr(proj, "solutionpath", "")
            base_dir = sp if os.path.isdir(sp) else os.path.dirname(sp)

        try:
            add_list = json.loads(self.expand_additional_paths(json.dumps(add_list), base_dir))
        except Exception:
            pass

        q = Testcases.query.filter(Testcases.ProjectId == project_id)
        if checkpoint_id:
            q = q.filter(Testcases.CheckpointId == int(checkpoint_id))
        else:
            q = q.filter(Testcases.CheckpointId.is_(None))

        tests = q.order_by(Testcases.SortOrder.asc(), Testcases.Id.asc()).all()

        for index, test in enumerate(tests, start=1):
            testcase_holder.append([
                test.Name,
                test.input,
                test.Output,
                bool(getattr(test, "Hidden", False)),
                add_list,
                index,
            ])

        json_object = json.dumps(testcase_holder)
        print(json_object, flush=True)
        return json_object

    def get_className_by_projectId(self, project_id):
        try:
            pid = int(project_id)
        except (TypeError, ValueError):
            return ""

        project = Projects.query.filter(Projects.Id == pid).first()
        if project is None:
            return ""

        class_obj = Classes.query.filter(Classes.Id == project.ClassId).first()
        if class_obj is None:
            return ""

        return class_obj.Name

    def get_class_id_by_name(self, class_name):
        class_id = Classes.query.filter(Classes.Name == class_name).first().Id
        return class_id

    def get_project_path(self, project_id, checkpoint_id: Optional[int] = None):
        project = Projects.query.filter(Projects.Id == project_id).first()
        if not project:
            return ""
        if checkpoint_id:
            pp = Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()
            return pp.solutionpath if (pp and pp.solutionpath) else ""
        return project.solutionpath

    def get_project_desc_path(self, project_id, checkpoint_id: Optional[int] = None):
        project = Projects.query.filter(Projects.Id == project_id).first()
        if not project:
            return ""
        if checkpoint_id:
            pp = Checkpoints.query.filter(Checkpoints.Id == int(checkpoint_id)).first()
            return pp.AsnDescriptionPath if (pp and pp.AsnDescriptionPath) else ""
        return project.AsnDescriptionPath

    def get_project_desc_file(self, project_id, checkpoint_id: Optional[int] = None):
        filepath = self.get_project_desc_path(project_id, checkpoint_id=checkpoint_id)
        if not filepath:
            return b""
        with open(filepath, "rb") as file:
            file_contents = file.read()
        return file_contents

    def get_student_grade(self, project_id, user_id):
        student_progress = MainAssignmentGrades.query.filter(
            and_(
                MainAssignmentGrades.UserId == user_id,
                MainAssignmentGrades.ProjectId == project_id,
            )
        ).first()

        if student_progress is None:
            return 0

        return student_progress.Grade

    def set_student_grade(self, project_id, user_id, grade):
        project_id = int(project_id)
        user_id = int(user_id)

        latest_submission = (
            Submissions.query
            .filter(
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
                UpdatedAt=datetime.utcnow(),
            )
            db.session.add(student_grade)
        else:
            student_grade.SubmissionId = int(latest_submission.Id)
            student_grade.Grade = int(grade)
            student_grade.UpdatedAt = datetime.utcnow()

        db.session.commit()
        return True
