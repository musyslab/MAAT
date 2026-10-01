"""Persist submissions and retrieve the records used for feedback and grading.

Create submission rows; query history, counts, and score summaries; and read
stored code and grading output. Save manual grading, annotations, and student
suggestions. Submission and assignment features enforce caller permissions
before presenting these records through HTTP endpoints."""

from collections import defaultdict
import json
from src.core.database import db
from src.core.models import CheckpointGrades
from src.core.models import MainAssignmentGrades
from src.core.models import StudentSuggestions
from src.core.models import SubmissionAnnotations
from src.core.models import Submissions
from src.core.models import Users
from datetime import datetime
from src.core.constants import chicago_now
from src.core.constants import to_chicago_datetime
from sqlalchemy import event
from sqlalchemy.sql import visitors
from sqlalchemy.orm import Session, with_loader_criteria
from flask_jwt_extended import current_user


@event.listens_for(Session, "do_orm_execute")
def isolate_test_submissions(execute_state):
    """Keep virtual test attempts out of ordinary history and class analytics."""
    if not execute_state.is_select or execute_state.execution_options.get("include_test_submissions"):
        return
    if not any(getattr(node, "name", None) == Submissions.__tablename__
               or getattr(getattr(node, "table", None), "name", None) == Submissions.__tablename__
               for node in visitors.iterate(execute_state.statement)):
        return
    from src.assignment_permissions import is_test_user_request, TEST_USER_FOLDER_PREFIX
    test_path = Submissions.CodeFilepath.contains(
        f"/{TEST_USER_FOLDER_PREFIX}", autoescape=True
    )
    if is_test_user_request():
        condition = and_(Submissions.User == int(current_user.Id),
                         test_path)
    else:
        condition = or_(Submissions.CodeFilepath.is_(None),
                        ~test_path)
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(Submissions, condition, include_aliases=True)
    )


from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import or_
from typing import List
import os
from src.core.models import Projects
from sqlalchemy import desc
from typing import Dict
from datetime import timedelta


class SubmissionRepository:
    """Database operations for submissions; callers share the Flask request session."""

    def get_submission_by_user_and_projectid(self, user_id: int, project_id: int) -> Submissions:
        """Returns the latest submission made by a user for a given project.

        Args:
            user_id (int): The ID of the user.
            project_id (int): The ID of the project.

        Returns:
            Submissions: The latest submission object made by the user for the given project.
        """

        # MAIN submissions only (exclude checkpoint submissions that share the same Project id)
        submission = (
            Submissions.query.filter(
                and_(
                    Submissions.Project == project_id,
                    Submissions.User == user_id,
                    Submissions.IsCheckpoint == False,
                )
            )
            .order_by(Submissions.Time.desc(), Submissions.Id.desc())
            .first()
        )

        return submission

    def get_submission_by_submission_id(self, submission_id: int) -> Submissions:
        """Retrieves a submission by its ID.

        Args:
            submission_id (int): The ID of the submission to retrieve.

        Returns:
            Submissions: The submission object with the specified ID.
        """
        # Execute the database lookup with the filters specified below.
        submission = (
            Submissions.query.execution_options(include_test_submissions=True).filter(Submissions.Id == submission_id).first()
        )
        return submission

    def get_code_path_by_submission_id(self, submission_id: int) -> str:
        """Returns the file path of the code file associated with a submission.

        Args:
            submission_id (int): The ID of the submission.

        Returns:
            str: The file path of the code file associated with the submission.
        """
        submission = self.get_submission_by_submission_id(submission_id)
        return submission.CodeFilepath if submission is not None else None

    def read_code_file(self, code_path) -> str:
        """Returns the contents of the code file associated with a given submission.

        Args:
            submission_id (int): The ID of the submission.

        Returns:
            str: The contents of the code file associated with the submission.
        """
        if os.path.isdir(code_path):
            filenames = sorted(
                name
                for name in os.listdir(code_path)
                if os.path.isfile(os.path.join(code_path, name))
                and os.path.splitext(name)[1].lower() in {".py", ".java", ".c", ".h", ".rkt", ".scm"}
            )
            # Return an empty or negative result when this guard matches.
            if not filenames:
                return ""
            code_path = os.path.join(code_path, filenames[0])

        # Open the file for reading and close it automatically when this block finishes.
        with open(code_path, "r", encoding="utf-8", errors="replace") as code_file:
            return code_file.read()

    def read_output_file(self, output_path) -> str:
        """Returns the contents of the output file associated with a given submission.

        Args:
            submission_id (int): The ID of the submission.

        Returns:
            str: The contents of the output file associated with the submission.
        """
        # Open the file for reading and close it automatically when this block finishes.
        with open(output_path, "r", encoding="utf-8", errors="replace") as output_file:
            return output_file.read()

    def create_submission(
        self,
        user_id: int,
        output: str,
        codepath: str,
        time: datetime,
        project_id: int,
        status: bool,
        testcase_results,
        is_checkpoint: bool = False,
        checkpoint_id: int = None,
        submission_method: str = "unknown",
    ):
        """Creates a new submission record in the database.

        Args:
            user_id (int): The ID of the user who submitted the code.
            output (str): The filepath of the output file generated by the code.
            codepath (str): The filepath of the code file submitted.
            time (str): The time at which the submission was made.
            project_id (int): The ID of the project for which the code was submitted.
            status (bool): Whether the submission passed or failed.
            testcase_results: The testcase results produced by the grader.
            is_checkpoint (bool): Whether this is a checkpoint submission.
            checkpoint_id (int): The checkpoint ID when this is a checkpoint submission.
            submission_method (str): How the student supplied the program: "upload", "editor", or "unknown".

        Returns:
            int: The ID of the newly created submission record.
        """
        submission = Submissions(
            OutputFilepath=output,
            CodeFilepath=codepath,
            Time=to_chicago_datetime(time),
            User=user_id,
            Project=project_id,
            IsPassing=status,
            IsCheckpoint=bool(is_checkpoint),
            CheckpointId=(
                int(checkpoint_id) if (is_checkpoint and checkpoint_id is not None) else None
            ),
            SubmissionMethod=submission_method,
            TestCaseResults=str(testcase_results),
        )
        # Stage the new records in the current database transaction.
        db.session.add(submission)
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        created_id = submission.Id  # Assuming the auto-incremented ID field is named "ID"
        return created_id

    def get_total_submission_for_all_projects(self) -> Dict[int, int]:
        """Return the number of distinct main-assignment submitters per project."""
        # Execute the database lookup with the filters specified below.
        rows = (
            db.session.query(
                Projects.Id,
                func.count(func.distinct(Submissions.User)),
            )
            .outerjoin(
                Submissions,
                and_(
                    Submissions.Project == Projects.Id,
                    Submissions.IsCheckpoint == False,
                ),
            )
            .group_by(Projects.Id)
            .all()
        )
        return {int(project_id): int(count or 0) for project_id, count in rows}

    def get_most_recent_submission_by_project(
        self, project_id: int, user_ids: List[int]
    ) -> Dict[int, Submissions]:
        """Return each requested user's latest main submission for a project."""
        # Return an empty or negative result when this guard matches.
        if not user_ids:
            return {}

        # Aggregate timestamps first, then break equal-time ties by submission ID.
        latest_times = (
            db.session.query(
                Submissions.User.label("user_id"),
                func.max(Submissions.Time).label("latest_time"),
            )
            .filter(
                Submissions.Project == int(project_id),
                Submissions.User.in_(user_ids),
                Submissions.IsCheckpoint == False,
            )
            .group_by(Submissions.User)
            .subquery()
        )
        latest_ids = (
            db.session.query(func.max(Submissions.Id).label("submission_id"))
            .join(
                latest_times,
                and_(
                    Submissions.User == latest_times.c.user_id,
                    or_(
                        Submissions.Time == latest_times.c.latest_time,
                        and_(Submissions.Time.is_(None), latest_times.c.latest_time.is_(None)),
                    ),
                ),
            )
            .filter(
                Submissions.Project == int(project_id),
                Submissions.IsCheckpoint == False,
            )
            .group_by(Submissions.User)
            .subquery()
        )
        # Execute the database lookup with the filters specified below.
        submissions = (
            Submissions.query.join(latest_ids, Submissions.Id == latest_ids.c.submission_id)
            .all()
        )
        return {int(submission.User): submission for submission in submissions}

    def submission_view_verification(self, user_id, submission_id) -> bool:
        """Handle submission view verification for this component.

        Inputs: user_id, submission_id."""
        # Execute the database lookup with the filters specified below.
        submission = db.session.query(Submissions.Id).filter(
            Submissions.Id == submission_id, Submissions.User == user_id
        ).first()
        return submission is not None

    def submission_counter(self, project_id: int, user_ids: List[int]) -> Dict[int, int]:
        """Return main-submission counts for the requested users."""
        # Return an empty or negative result when this guard matches.
        if not user_ids:
            return {}

        # Execute the database lookup with the filters specified below.
        rows = (
            db.session.query(Submissions.User, func.count(Submissions.Id))
            .filter(
                Submissions.Project == int(project_id),
                Submissions.User.in_(user_ids),
                Submissions.IsCheckpoint == False,
            )
            .group_by(Submissions.User)
            .all()
        )
        return {int(user_id): int(count) for user_id, count in rows}

    def get_all_submissions_for_project(self, project_id):
        """Return all submissions for project.

        Inputs: project_id."""
        # Execute the database lookup with the filters specified below.
        submissions = Submissions.query.filter(Submissions.Project == project_id).all()
        return submissions

    def get_all_submission_times(self, project_id):
        """Return main-submission activity over the module's actual date window."""
        project = Projects.query.filter(Projects.Id == project_id).first()
        module = getattr(project, "Module", None) if project is not None else None
        start = getattr(module, "Start", None)
        end = getattr(module, "End", None)
        if start is None or end is None:
            return [], []
        start_date = to_chicago_datetime(start).date()
        end_date = to_chicago_datetime(end).date()
        if end_date < start_date:
            return [], []
        days = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        counts = {day: [0] * 12 for day in days}
        failures = defaultdict(int)
        passed_users = set()
        submissions = Submissions.query.filter(
            Submissions.Project == project_id,
            Submissions.IsCheckpoint == False,
            Submissions.Time >= datetime.combine(start_date, datetime.min.time()),
            Submissions.Time < datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
        ).all()
        for submission in submissions:
            if submission.Time is None:
                continue
            timestamp = to_chicago_datetime(submission.Time)
            if timestamp.date() not in counts:
                continue
            counts[timestamp.date()][timestamp.hour // 2] += 1
            if submission.IsPassing:
                passed_users.add(submission.User)
            else:
                failures[submission.User] += 1
        struggling = sorted(
            ((user_id, count) for user_id, count in failures.items() if user_id not in passed_users),
            key=lambda item: (-item[1], item[0]),
        )[:10]
        ids = [user_id for user_id, _ in struggling]
        users = {user.Id: user for user in Users.query.filter(Users.Id.in_(ids)).all()} if ids else {}
        students = [
            [user_id, count, users[user_id].Firstname, users[user_id].Lastname, users[user_id].Email]
            for user_id, count in struggling if user_id in users
        ]
        heatmap = [{"name": day.strftime("%A %b %d"), "data": counts[day]} for day in reversed(days)]
        return heatmap, students

    def day_to_day_visualizer(self, project_id, user_ids):
        """Return daily pass/fail/no-submission counts for the project window."""
        # Execute the database lookup with the filters specified below.
        project = Projects.query.filter(Projects.Id == project_id).first()
        module = getattr(project, "Module", None) if project is not None else None
        project_start_date = getattr(module, "Start", None)
        project_end_date = getattr(module, "End", None)
        # Handle the case where project_start_date is None or project_end_date is None.
        if project_start_date is None or project_end_date is None:
            return [], [], [], []

        if project_end_date.date() < project_start_date.date():
            return [], [], [], []
        days_live = (project_end_date.date() - project_start_date.date()).days
        dates = [
            (project_start_date + timedelta(days=offset)).strftime("%Y/%m/%d")
            for offset in range(days_live + 1)
        ]
        date_indexes = {date_value: index for index, date_value in enumerate(dates)}
        passed = [0] * len(dates)
        failed = [0] * len(dates)
        no_submission = [0] * len(dates)

        user_id_set = {int(user_id) for user_id in user_ids}
        status_by_user_date = defaultdict(lambda: {"submitted": False, "passed": False})
        if user_id_set:
            # Execute the database lookup with the filters specified below.
            submissions = Submissions.query.filter(
                Submissions.Project == project_id,
                Submissions.User.in_(user_id_set),
                Submissions.IsCheckpoint == False,
                Submissions.Time >= datetime.combine(project_start_date.date(), datetime.min.time()),
                Submissions.Time < datetime.combine(project_end_date.date() + timedelta(days=1), datetime.min.time()),
            ).all()
            # Process each submission from submissions.
            for submission in submissions:
                if submission.Time is None:
                    continue
                date_value = submission.Time.strftime("%Y/%m/%d")
                if date_value not in date_indexes:
                    continue
                key = (int(submission.User), date_value)
                status_by_user_date[key]["submitted"] = True
                status_by_user_date[key]["passed"] = (
                    status_by_user_date[key]["passed"] or bool(submission.IsPassing)
                )

        # Process each user_id from user_id_set.
        for user_id in user_id_set:
            # Process each (date_value, index) from date_indexes.items().
            for date_value, index in date_indexes.items():
                status = status_by_user_date.get((user_id, date_value))
                if not status or not status["submitted"]:
                    no_submission[index] += 1
                elif status["passed"]:
                    passed[index] += 1
                else:
                    failed[index] += 1

        return dates, passed, failed, no_submission

    def get_all_submissions_for_user(self, user_id):
        """Return all submissions for user.

        Inputs: user_id."""
        # Execute the database lookup with the filters specified below.
        submissions = Submissions.query.filter(Submissions.User == user_id).all()
        return submissions

    def get_project_scores(self, project_id):
        """Return project scores.

        Inputs: project_id."""
        # Execute the database lookup with the filters specified below.
        scores = MainAssignmentGrades.query.filter(
            MainAssignmentGrades.ProjectId == project_id
        ).all()
        student_list = []
        # Process each score from scores.
        for score in scores:
            student_list.append([score.UserId, score.Grade])
        return student_list

    def submitSuggestion(self, user_id, suggestion):
        """Handle submit suggestion for this component.

        Inputs: user_id, suggestion.
        Database changes are committed at the explicit transaction boundaries below."""
        suggestion_row = StudentSuggestions(
            UserId=int(user_id),
            Suggestion=str(suggestion or ""),
            SubmittedAt=chicago_now(),
        )
        # Stage the new records in the current database transaction.
        db.session.add(suggestion_row)
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
        return "ok"

    def save_manual_grading(
        self,
        submission_id,
        grade,
        scoring_mode,
        error_points,
        errors,
        error_defs,
        checkpoint=False,
        checkpoint_id=None,
    ):
        """Save manual grading.

        Inputs: submission_id, grade, scoring_mode, error_points, errors, error_defs, checkpoint, checkpoint_id.
        Database changes are committed at the explicit transaction boundaries below."""
        try:
            sub = db.session.get(Submissions, submission_id)
            # Return an empty or negative result when this guard matches.
            if sub is None:
                return False

            sid = sub.User
            pid = sub.Project
            is_checkpoint_submission = bool(getattr(sub, "IsCheckpoint", False)) or bool(checkpoint)

            mode = (
                scoring_mode if scoring_mode in ("perInstance", "flatPerError") else "perInstance"
            )
            clean_pts = {}
            # Process each (k, v) from (error_points or {}).items() if isinstance(error_points, dict) else [].
            for k, v in (error_points or {}).items() if isinstance(error_points, dict) else []:
                try:
                    clean_pts[str(k)] = max(0, int(v))
                # Ignore this failure and allow the surrounding operation to continue.
                except Exception:
                    pass
            points_json = json.dumps(clean_pts, sort_keys=True)

            # error_defs stores custom defs INCLUDING default points.
            defs_json = json.dumps(error_defs or {}, sort_keys=True)

            if is_checkpoint_submission:
                grades = db.session.get(CheckpointGrades, int(submission_id))
                if grades:
                    grades.UserId = sid
                    grades.ProjectId = pid
                    grades.Grade = int(grade) if grade is not None else grades.Grade
                    grades.ScoringMode = mode
                    grades.ErrorPointsJson = points_json
                    grades.ErrorDefsJson = defs_json
                    grades.UpdatedAt = chicago_now()
                else:
                    # Stage the new records in the current database transaction.
                    db.session.add(
                        CheckpointGrades(
                            SubmissionId=int(submission_id),
                            UserId=sid,
                            ProjectId=pid,
                            Grade=int(grade) if grade is not None else 0,
                            ScoringMode=mode,
                            ErrorPointsJson=points_json,
                            ErrorDefsJson=defs_json,
                            UpdatedAt=chicago_now(),
                        )
                    )
            else:
                # Execute the database lookup with the filters specified below.
                grades = (
                    MainAssignmentGrades.query.filter(MainAssignmentGrades.UserId == sid)
                    .filter(MainAssignmentGrades.ProjectId == pid)
                    .first()
                )

                if grades:
                    grades.Grade = int(grade) if grade is not None else grades.Grade
                    grades.SubmissionId = int(submission_id)
                    grades.ScoringMode = mode
                    grades.ErrorPointsJson = points_json
                    grades.ErrorDefsJson = defs_json
                    grades.UpdatedAt = chicago_now()
                else:
                    # Stage the new records in the current database transaction.
                    db.session.add(
                        MainAssignmentGrades(
                            UserId=sid,
                            ProjectId=pid,
                            Grade=int(grade) if grade is not None else 0,
                            SubmissionId=int(submission_id),
                            ScoringMode=mode,
                            ErrorPointsJson=points_json,
                            ErrorDefsJson=defs_json,
                            UpdatedAt=chicago_now(),
                        )
                    )

            # Replace error rows for this exact submission.
            SubmissionAnnotations.query.filter_by(SubmissionId=submission_id).delete()

            # Process each error from errors or [].
            for error in errors or []:
                # Stage the new records in the current database transaction.
                db.session.add(
                    SubmissionAnnotations(
                        SubmissionId=int(submission_id),
                        StartLine=int(error.get("startLine")),
                        EndLine=int(error.get("endLine")),
                        ErrorId=str(error.get("errorId")),
                        Count=max(1, int(error.get("count", 1))),
                        Note=str(error.get("note", "") or ""),
                    )
                )

            # Commit the pending database changes so they persist beyond this request.
            db.session.commit()
            return True
        # Convert this failure into the fallback result or error response below.
        except Exception:
            # Undo pending database changes after the operation fails.
            db.session.rollback()
            return False

    def get_manual_errors(self, submission_id):

        # fetch all errors for this submission
        """Return manual errors.

        Inputs: submission_id."""
        # Execute the database lookup with the filters specified below.
        errors = SubmissionAnnotations.query.filter(
            SubmissionAnnotations.SubmissionId == submission_id
        ).all()

        # convert to list of dicts
        return [
            {
                "startLine": e.StartLine,
                "endLine": e.EndLine,
                "errorId": e.ErrorId,
                "count": getattr(e, "Count", 1) or 1,
                "note": getattr(e, "Note", "") or "",
            }
            for e in errors
        ]

    def get_manual_grade_config(self, submission_id: int):
        """Return persisted manual grading config for this exact submission."""
        sub = db.session.get(Submissions, int(submission_id))
        # Handle the case where sub is None.
        if sub is None:
            return {"grade": None, "scoringMode": "perInstance", "errorPoints": {}, "errorDefs": {}}

        if bool(getattr(sub, "IsCheckpoint", False)):
            row = db.session.get(CheckpointGrades, int(submission_id))
        else:
            sid = sub.User
            pid = sub.Project
            # Execute the database lookup with the filters specified below.
            row = (
                MainAssignmentGrades.query.filter(MainAssignmentGrades.UserId == sid)
                .filter(MainAssignmentGrades.ProjectId == pid)
                .filter(MainAssignmentGrades.SubmissionId == int(submission_id))
                .first()
            )

        # Handle the case where row is None.
        if row is None:
            return {"grade": None, "scoringMode": "perInstance", "errorPoints": {}, "errorDefs": {}}

        mode = getattr(row, "ScoringMode", None)
        if mode not in ("perInstance", "flatPerError"):
            mode = "perInstance"

        raw_pts = getattr(row, "ErrorPointsJson", None) or "{}"
        try:
            pts = json.loads(raw_pts) if isinstance(raw_pts, str) else (raw_pts or {})
        except Exception:
            pts = {}

        raw_defs = getattr(row, "ErrorDefsJson", None) or "{}"
        try:
            defs = json.loads(raw_defs) if isinstance(raw_defs, str) else (raw_defs or {})
        except Exception:
            defs = {}

        return {
            "grade": getattr(row, "Grade", None),
            "scoringMode": mode,
            "errorPoints": pts,
            "errorDefs": defs,
        }

    def _grade_payload_from_row(self, row):
        """Handle grade payload from row for this component.

        Inputs: row."""
        raw_pts = getattr(row, "ErrorPointsJson", None) or "{}"
        try:
            pts = json.loads(raw_pts) if isinstance(raw_pts, str) else (raw_pts or {})
        except Exception:
            pts = {}

        raw_defs = getattr(row, "ErrorDefsJson", None) or "{}"
        try:
            defs = json.loads(raw_defs) if isinstance(raw_defs, str) else (raw_defs or {})
        except Exception:
            defs = {}

        return {
            "grade": getattr(row, "Grade", None),
            "submission_id": getattr(row, "SubmissionId", None),
            "scoring_mode": getattr(row, "ScoringMode", None),
            "error_points": pts,
            "error_defs": defs,
        }

    def get_manual_grade_for_submission(self, submission_id: int):
        """Return manual grade for submission.

        Inputs: submission_id."""
        sub = db.session.get(Submissions, int(submission_id))
        # Return an empty or negative result when this guard matches.
        if sub is None:
            return None

        if bool(getattr(sub, "IsCheckpoint", False)):
            row = db.session.get(CheckpointGrades, int(submission_id))
        else:
            # Execute the database lookup with the filters specified below.
            row = (
                MainAssignmentGrades.query.filter(MainAssignmentGrades.UserId == sub.User)
                .filter(MainAssignmentGrades.ProjectId == sub.Project)
                .filter(MainAssignmentGrades.SubmissionId == int(submission_id))
                .first()
            )
        # Return an empty or negative result when this guard matches.
        if row is None:
            return None
        return self._grade_payload_from_row(row)

    def get_project_grade_info(self, project_id: int, checkpoint=False, checkpoint_id=None):
        # Get grade info for a project. Normal projects use main assignment rows.
        # Checkpoints use per-submission rows so each checkpoint can be graded separately.
        """Return project grade info.

        Inputs: project_id, checkpoint, checkpoint_id."""
        checkpoint = bool(checkpoint)

        if checkpoint:
            # Execute the database lookup with the filters specified below.
            subs_query = Submissions.query.filter(
                Submissions.Project == int(project_id),
                Submissions.IsCheckpoint == True,
            )
            if checkpoint_id is not None:
                subs_query = subs_query.filter(Submissions.CheckpointId == int(checkpoint_id))

            latest_by_student = {}
            # Process each sub from subs_query.order_by(Submissions.User.asc(), Submissions.Time.desc(), Submissions.Id.desc()).all().
            for sub in subs_query.order_by(Submissions.User.asc(), Submissions.Time.desc(), Submissions.Id.desc()).all():
                sid = int(getattr(sub, "User", 0) or 0)
                if sid and sid not in latest_by_student:
                    latest_by_student[sid] = sub

            submission_ids = [int(s.Id) for s in latest_by_student.values()]
            grade_rows = (
                CheckpointGrades.query.filter(
                    CheckpointGrades.SubmissionId.in_(submission_ids)
                ).all()
                if submission_ids
                else []
            )
            grades_by_submission = {
                int(g.SubmissionId): self._grade_payload_from_row(g) for g in grade_rows
            }

            grades_by_student = {}
            # Process each (sid, sub) from latest_by_student.items().
            for sid, sub in latest_by_student.items():
                payload = grades_by_submission.get(
                    int(sub.Id),
                    {
                        "grade": 0,
                        "submission_id": int(sub.Id),
                        "scoring_mode": "perInstance",
                        "error_points": {},
                        "error_defs": {},
                    },
                )
                payload["submission_id"] = int(sub.Id)
                grades_by_student[sid] = payload
        else:
            # Execute the database lookup with the filters specified below.
            grade_rows = MainAssignmentGrades.query.filter(
                MainAssignmentGrades.ProjectId == project_id
            ).all()
            grades_by_student = {}
            # Process each g from grade_rows.
            for g in grade_rows:
                grades_by_student[g.UserId] = self._grade_payload_from_row(g)

        database_ids = list(grades_by_student.keys())
        student_numbers = (
            Users.query.filter(Users.Id.in_(database_ids)).all() if database_ids else []
        )
        numbers_by_student = defaultdict(str)
        # Process each num from student_numbers.
        for num in student_numbers:
            numbers_by_student[num.Id] = num.StudentNumber

        submission_ids = [
            v["submission_id"]
            for v in grades_by_student.values()
            if v.get("submission_id") is not None
        ]
        errors = (
            SubmissionAnnotations.query.filter(
                SubmissionAnnotations.SubmissionId.in_(submission_ids)
            ).all()
            if submission_ids
            else []
        )
        errors_by_submission = defaultdict(list)
        # Process each e from errors.
        for e in errors:
            errors_by_submission[e.SubmissionId].append(e)

        rows = []
        # Process each (sid, data) from grades_by_student.items().
        for sid, data in grades_by_student.items():
            error_list = errors_by_submission.get(data["submission_id"], [])
            error_data = [
                {
                    "errorId": getattr(e, "ErrorId", None),
                    "startLine": getattr(e, "StartLine", None),
                    "endLine": getattr(e, "EndLine", None),
                    "count": getattr(e, "Count", None),
                    "note": getattr(e, "Note", "") or "",
                }
                for e in error_list
            ]

            rows.append(
                {
                    "number": numbers_by_student[sid],
                    "grade": data["grade"],
                    "points": data["error_points"],
                    "scoring_mode": data["scoring_mode"],
                    "error_defs": data["error_defs"],
                    "description": error_data,
                }
            )

        return rows
