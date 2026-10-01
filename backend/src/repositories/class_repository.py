"""Query classes and persist class-membership assignments.

Retrieve class lists, individual classes, and classes belonging to a school.
Create class assignments with the requested role and section information.
ClassService and feature handlers apply user-specific access rules around these
operations."""

from typing import List
from typing import Optional
from sqlalchemy import desc
from src.core.models import ClassAssignments
from src.core.models import Classes


class ClassRepository:
    """Represent class repository within the classes component."""

    def get_classes(self) -> List[Classes]:
        """Return classes."""
        return Classes.query.order_by(desc(Classes.Name)).all()

    def get_class_by_id(self, class_id: int):
        """Return class by id.

        Inputs: class_id."""
        return Classes.query.filter(Classes.Id == class_id).first()

    def get_classes_for_school(self, school_id: int) -> List[Classes]:
        """Return classes for school.

        Inputs: school_id."""
        return (
            Classes.query.filter(Classes.SchoolId == school_id).order_by(Classes.Name.asc()).all()
        )

    def add_class_assignment(
        self,
        class_id: int,
        lab_id: Optional[int],
        user_id: int,
        lecture_id: Optional[int],
    ) -> ClassAssignments:
        """Handle add class assignment for this component.

        Inputs: class_id, lab_id, user_id, lecture_id.
        Database changes are committed at the explicit transaction boundaries below."""
        # Execute the database lookup with the filters specified below.
        assignment = ClassAssignments.query.filter(
            ClassAssignments.UserId == user_id,
            ClassAssignments.ClassId == class_id,
        ).first()

        if assignment is None:
            assignment = ClassAssignments(
                UserId=user_id,
                ClassId=class_id,
                LabId=lab_id,
                LectureId=lecture_id,
            )
            # Stage the new records in the current database transaction.
            ClassAssignments.query.session.add(assignment)
        else:
            assignment.LabId = lab_id
            assignment.LectureId = lecture_id

        # Commit the pending database changes so they persist beyond this request.
        ClassAssignments.query.session.commit()
        return assignment
