"""List classes and verify class access using enrollment and role context.

Combine class endpoints with ClassService, which resolves student, teacher, and
administrator permissions from class assignments. Format accessible classes,
schools, labs, and lecture sections for the frontend. Repository dependencies
are supplied by the application container.

Endpoints use /api/classes/<handler_name>."""

from sqlalchemy import func
from typing import List
from typing import Optional
from typing import Set
from src.core.constants import ADMIN_ROLE
from src.core.constants import STUDENT_ROLE
from src.core.constants import TEACHER_ROLE
from src.repositories.class_repository import ClassRepository
from src.core.models import ClassAssignments
from src.core.models import Classes
from src.core.models import Users
from flask import request
from src.core.models import Labs
from src.core.models import LectureSections
from src.core.models import Schools
from dependency_injector.wiring import Provide
from dependency_injector.wiring import inject
from flask import abort
from flask import jsonify
from flask_jwt_extended import current_user
from flask_jwt_extended import get_current_user
from flask_jwt_extended import jwt_required
from src.core.blueprints import classes_api
from src.core.container import Container


# Class enrollment operations and role-aware class lookup.


def parse_class_service_int(value) -> Optional[int]:
    """Parse an optional identifier using the existing empty-value convention.

    Inputs: value."""
    try:
        return int(value)
    # Convert this failure into the fallback result or error response below.
    except (TypeError, ValueError):
        return None


class ClassService:
    """Represent class service within the enrollment component."""

    def get_user_id(self, current_user: Users) -> Optional[int]:
        """Return user id.

        Inputs: current_user."""
        # Return an empty or negative result when this guard matches.
        if current_user is None:
            return None

        return parse_class_service_int(getattr(current_user, "Id", None))

    def get_assignment_for_user_and_class(self, user_id: int, class_id: int):
        """Return assignment for user and class.

        Inputs: user_id, class_id."""
        return ClassAssignments.query.filter(
            ClassAssignments.UserId == user_id,
            ClassAssignments.ClassId == class_id,
        ).first()

    def get_assignment_role_for_class(self, user_id: int, class_id: int) -> Optional[int]:
        """Return assignment role for class.

        Inputs: user_id, class_id."""
        assignment = self.get_assignment_for_user_and_class(user_id, class_id)

        # Return an empty or negative result when this guard matches.
        if assignment is None:
            return None

        return parse_class_service_int(getattr(assignment, "Role", None))

    def get_user_global_role(self, current_user: Users) -> int:
        """Return the user's highest class-assignment role."""
        user_id = self.get_user_id(current_user)
        # Handle the case where user_id is None.
        if user_id is None:
            return STUDENT_ROLE

        # Execute the database lookup with the filters specified below.
        role = (
            ClassAssignments.query.with_entities(func.max(ClassAssignments.Role))
            .filter(ClassAssignments.UserId == user_id)
            .scalar()
        )
        parsed_role = parse_class_service_int(role)
        return parsed_role if parsed_role is not None else STUDENT_ROLE

    def get_assigned_class_ids(self, user_id: int) -> Set[int]:
        """Return assigned class ids.

        Inputs: user_id."""
        # Execute the database lookup with the filters specified below.
        assignments = ClassAssignments.query.filter(ClassAssignments.UserId == user_id).all()

        class_ids: Set[int] = set()

        # Process each assignment from assignments.
        for assignment in assignments:
            class_id = parse_class_service_int(getattr(assignment, "ClassId", None))
            if class_id is not None:
                class_ids.add(class_id)

        return class_ids

    def get_assigned_classes(
        self, current_user: Users, class_repo: ClassRepository
    ) -> List[Classes]:
        """Return assigned classes.

        Inputs: current_user, class_repo."""
        user_id = self.get_user_id(current_user)

        # Return an empty or negative result when this guard matches.
        if user_id is None:
            return []

        # Handle the case where self.get_user_global_role(current_user) >= ADMIN_ROLE.
        if self.get_user_global_role(current_user) >= ADMIN_ROLE:
            return class_repo.get_classes()

        assigned_class_ids = self.get_assigned_class_ids(user_id)

        # Return an empty or negative result when this guard matches.
        if not assigned_class_ids:
            return []

        return (
            Classes.query.filter(Classes.Id.in_(assigned_class_ids))
            .order_by(Classes.Name.asc())
            .all()
        )

    def user_can_access_school(
        self, current_user: Users, school_id: int, class_repo: ClassRepository
    ) -> bool:
        """Determine whether user can access school.

        Inputs: current_user, school_id, class_repo."""
        user_id = self.get_user_id(current_user)
        parsed_school_id = parse_class_service_int(school_id)

        # Return an empty or negative result when this guard matches.
        if user_id is None or parsed_school_id is None:
            return False

        # Handle the case where self.get_user_global_role(current_user) >= ADMIN_ROLE.
        if self.get_user_global_role(current_user) >= ADMIN_ROLE:
            return True

        classes = class_repo.get_classes_for_school(parsed_school_id)

        return any(
            self.user_can_access_class_item(current_user, class_item, class_repo)
            for class_item in classes
        )

    def user_can_access_class(
        self, current_user: Users, class_id: int, class_repo: ClassRepository
    ) -> bool:
        """Determine whether user can access class.

        Inputs: current_user, class_id, class_repo."""
        parsed_class_id = parse_class_service_int(class_id)

        # Return an empty or negative result when this guard matches.
        if parsed_class_id is None:
            return False

        class_item = class_repo.get_class_by_id(parsed_class_id)
        return self.user_can_access_class_item(current_user, class_item, class_repo)

    def user_can_access_class_item(
        self, current_user: Users, class_item: Classes, class_repo: ClassRepository
    ) -> bool:
        """Determine whether user can access class item.

        Inputs: current_user, class_item, class_repo."""
        # Return an empty or negative result when this guard matches.
        if class_item is None:
            return False

        user_id = self.get_user_id(current_user)
        class_id = parse_class_service_int(getattr(class_item, "Id", None))

        # Return an empty or negative result when this guard matches.
        if user_id is None or class_id is None:
            return False

        # Handle the case where self.get_user_global_role(current_user) >= ADMIN_ROLE.
        if self.get_user_global_role(current_user) >= ADMIN_ROLE:
            return True

        assignment = self.get_assignment_for_user_and_class(user_id, class_id)

        return assignment is not None

    def user_can_teach_class_item(self, current_user: Users, class_item: Classes) -> bool:
        """Determine whether user can teach class item.

        Inputs: current_user, class_item."""
        # Return an empty or negative result when this guard matches.
        if class_item is None:
            return False

        user_id = self.get_user_id(current_user)
        class_id = parse_class_service_int(getattr(class_item, "Id", None))

        # Return an empty or negative result when this guard matches.
        if user_id is None or class_id is None:
            return False

        # Handle the case where self.get_user_global_role(current_user) >= ADMIN_ROLE.
        if self.get_user_global_role(current_user) >= ADMIN_ROLE:
            return True

        assignment_role = self.get_assignment_role_for_class(user_id, class_id)

        return assignment_role is not None and assignment_role >= TEACHER_ROLE

    def user_can_study_class_item(self, current_user: Users, class_item: Classes) -> bool:
        """Determine whether user can study class item.

        Inputs: current_user, class_item."""
        # Return an empty or negative result when this guard matches.
        if class_item is None:
            return False

        user_id = self.get_user_id(current_user)
        class_id = parse_class_service_int(getattr(class_item, "Id", None))

        # Return an empty or negative result when this guard matches.
        if user_id is None or class_id is None:
            return False

        assignment_role = self.get_assignment_role_for_class(user_id, class_id)

        return assignment_role == STUDENT_ROLE

# Class endpoints, access checks, and class/section response formatting.


# Policy constants for classes.


ROLE_CONTEXT_ADMIN = "admin"


ROLE_CONTEXT_STUDENT = "student"


# Classes authorization and current-user scope checks.


def parse_optional_int(value):
    """Parse an optional identifier using the existing empty-value convention.

    Inputs: value."""
    try:
        return int(value)
    # Convert this failure into the fallback result or error response below.
    except (TypeError, ValueError):
        return None


def extract_class_id(item) -> int:
    """Handle extract class id for this component.

    Inputs: item."""
    try:
        # Handle the case where isinstance(item, dict).
        if isinstance(item, dict):
            return int(item.get("id") or item.get("Id") or 0)
        return int(getattr(item, "id", None) or getattr(item, "Id", None) or 0)
    # Convert this failure into the fallback result or error response below.
    except (TypeError, ValueError):
        return 0


def get_requested_role_context():
    """Return requested role context."""
    role_context = (
        str(request.args.get("role_context") or request.args.get("context") or "").strip().lower()
    )

    # Handle the case where role_context in (ROLE_CONTEXT_ADMIN, 'teacher', 'teaching').
    if role_context in (ROLE_CONTEXT_ADMIN, "teacher", "teaching"):
        return ROLE_CONTEXT_ADMIN

    # Handle the case where role_context in (ROLE_CONTEXT_STUDENT, 'learning').
    if role_context in (ROLE_CONTEXT_STUDENT, "learning"):
        return ROLE_CONTEXT_STUDENT

    return None


def get_user_global_role(user) -> int:
    """Return the highest class-assignment role for a user."""
    user_id = parse_optional_int(getattr(user, "Id", None))
    # Handle the case where user_id is None.
    if user_id is None:
        return STUDENT_ROLE

    # Execute the database lookup with the filters specified below.
    role = (
        ClassAssignments.query.with_entities(func.max(ClassAssignments.Role))
        .filter(ClassAssignments.UserId == user_id)
        .scalar()
    )
    parsed_role = parse_optional_int(role)
    return parsed_role if parsed_role is not None else STUDENT_ROLE


def get_assignment_for_user_and_class(user_id: int, class_id: int):
    """Return assignment for user and class.

    Inputs: user_id, class_id."""
    return ClassAssignments.query.filter(
        ClassAssignments.UserId == user_id,
        ClassAssignments.ClassId == class_id,
    ).first()


def get_assignment_role(assignment):
    """Return assignment role.

    Inputs: assignment."""
    # Return an empty or negative result when this guard matches.
    if assignment is None:
        return None

    return parse_optional_int(getattr(assignment, "Role", None))


def user_can_access_class_for_context(user, class_item, role_context, repository, service) -> bool:
    """Determine whether user can access class for context.

    Inputs: user, class_item, role_context, repository, service."""
    # Return an empty or negative result when this guard matches.
    if user is None or class_item is None:
        return False

    assignment = get_assignment_for_user_and_class(int(user.Id), int(class_item.Id))
    assignment_role = get_assignment_role(assignment)
    global_role = get_user_global_role(user)

    # Handle the case where role_context == ROLE_CONTEXT_STUDENT.
    if role_context == ROLE_CONTEXT_STUDENT:
        return assignment_role == STUDENT_ROLE

    if role_context == ROLE_CONTEXT_ADMIN:
        # Handle the case where global_role >= ADMIN_ROLE.
        if global_role >= ADMIN_ROLE:
            return True

        return assignment_role is not None and assignment_role >= TEACHER_ROLE

    return service.user_can_access_class_item(user, class_item, repository)


def filter_classes_for_context(user, classes_list, role_context, repository, service):
    """Filter classes for context.

    Inputs: user, classes_list, role_context, repository, service."""
    # Handle the case where role_context is None.
    if role_context is None:
        return classes_list

    return [
        class_item
        for class_item in classes_list
        if user_can_access_class_for_context(user, class_item, role_context, repository, service)
    ]


# Serialize class, school, lab, and lecture-section responses for class endpoints.


def serialize_class(item, school_names_by_id=None):
    """Serialize class.

    Inputs: item, school_names_by_id."""
    school_names_by_id = school_names_by_id or {}

    if isinstance(item, dict):
        school_id = parse_optional_int(item.get("school_id") or item.get("SchoolId"))
        return {
            "id": extract_class_id(item),
            "name": item.get("name") or item.get("Name") or "",
            "school_id": school_id,
            "school_name": item.get("school_name")
            or item.get("SchoolName")
            or school_names_by_id.get(school_id, ""),
        }

    school_id = parse_optional_int(
        getattr(item, "school_id", None) or getattr(item, "SchoolId", None)
    )
    return {
        "id": extract_class_id(item),
        "name": getattr(item, "name", None) or getattr(item, "Name", "") or "",
        "school_id": school_id,
        "school_name": school_names_by_id.get(school_id, ""),
    }


def serialize_classes(class_items):
    """Serialize classes.

    Inputs: class_items."""
    school_ids = {
        parse_optional_int(getattr(class_item, "SchoolId", None))
        for class_item in class_items
        if parse_optional_int(getattr(class_item, "SchoolId", None)) is not None
    }
    school_names_by_id = {}

    if school_ids:
        # Execute the database lookup with the filters specified below.
        schools = Schools.query.filter(Schools.Id.in_(school_ids)).all()
        school_names_by_id = {school.Id: school.Name for school in schools}

    return [serialize_class(class_item, school_names_by_id) for class_item in class_items]


def serialize_school(school):
    """Serialize school.

    Inputs: school."""
    # Return an empty or negative result when this guard matches.
    if school is None:
        return None

    return {
        "id": school.Id,
        "name": school.Name,
    }


def serialize_class_sections(classes_list):
    """Serialize class sections.

    Inputs: classes_list."""
    class_ids = [cls.Id for cls in classes_list]

    labs_by_class = {class_id: [] for class_id in class_ids}
    lectures_by_class = {class_id: [] for class_id in class_ids}

    if class_ids:
        # Execute the database lookup with the filters specified below.
        labs = Labs.query.filter(Labs.ClassId.in_(class_ids)).order_by(Labs.Name.asc()).all()
        # Execute the database lookup with the filters specified below.
        lectures = (
            LectureSections.query.filter(LectureSections.ClassId.in_(class_ids))
            .order_by(LectureSections.Name.asc())
            .all()
        )

        # Process each lab from labs.
        for lab in labs:
            labs_by_class.setdefault(lab.ClassId, []).append({"name": lab.Name, "id": lab.Id})

        # Process each lecture from lectures.
        for lecture in lectures:
            lectures_by_class.setdefault(lecture.ClassId, []).append(
                {"name": lecture.Name, "id": lecture.Id}
            )

    return [
        {
            "name": cls.Name,
            "id": cls.Id,
            "school_id": cls.SchoolId,
            "labs": labs_by_class.get(cls.Id, []),
            "lectures": lectures_by_class.get(cls.Id, []),
        }
        for cls in classes_list
    ]


# Classes HTTP endpoints for routes.


@classes_api.route('/get_classes_and_ids', methods=["GET"])
@jwt_required()
@inject
def get_classes_and_ids(
    class_repo: ClassRepository = Provide[Container.class_repo],
    class_service: ClassService = Provide[Container.class_service],
):
    """Return classes and ids.

    HTTP: GET /api/classes/get_classes_and_ids.

    Inputs: class_repo, class_service."""
    school_id = parse_optional_int(request.args.get("school_id"))
    include_school = str(request.args.get("include_school", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )
    role_context = get_requested_role_context()
    selected_school = None

    if school_id and school_id > 0:
        # Execute the database lookup with the filters specified below.
        selected_school = Schools.query.filter(Schools.Id == school_id).first()

        if selected_school is None:
            abort(404)

    classes_list = class_service.get_assigned_classes(current_user, class_repo)
    classes_list = filter_classes_for_context(
        current_user, classes_list, role_context, class_repo, class_service
    )

    if school_id and school_id > 0:
        classes_list = [
            class_item
            for class_item in classes_list
            if parse_optional_int(getattr(class_item, "SchoolId", None)) == school_id
        ]

        if role_context is not None and not classes_list:
            abort(403)

        if role_context is None and not class_service.user_can_access_school(
            current_user, school_id, class_repo
        ):
            abort(403)

    serialized_classes = serialize_classes(classes_list)
    serialized_classes.sort(key=lambda class_item: class_item["name"])

    # Return the response below when this validation or access check matches.
    if include_school:
        return jsonify(
            {
                "school": serialize_school(selected_school),
                "classes": serialized_classes,
            }
        )

    return jsonify(serialized_classes)


@classes_api.route('/validate_class_access/<class_id>', methods=["GET"])
@jwt_required()
@inject
def validate_class_access(
    class_id,
    class_repo: ClassRepository = Provide[Container.class_repo],
    class_service: ClassService = Provide[Container.class_service],
):
    """Validate class access.

    HTTP: GET /api/classes/validate_class_access/<class_id>.

    Inputs: class_id, class_repo, class_service."""
    parsed_class_id = parse_optional_int(class_id)
    school_id = parse_optional_int(request.args.get("school_id"))
    role_context = get_requested_role_context()

    if parsed_class_id is None:
        abort(404)

    class_item = class_repo.get_class_by_id(parsed_class_id)

    if class_item is None:
        abort(404)

    if school_id is not None and class_item.SchoolId != school_id:
        abort(403)

    if role_context is not None:
        if not user_can_access_class_for_context(
            current_user, class_item, role_context, class_repo, class_service
        ):
            abort(403)
    elif not class_service.user_can_access_class_item(current_user, class_item, class_repo):
        abort(403)

    return jsonify(serialize_class(class_item))


@classes_api.route('/get_class_labs', methods=["GET"])
@jwt_required(optional=True)
@inject
def get_class_labs(
    class_repo: ClassRepository = Provide[Container.class_repo],
    class_service: ClassService = Provide[Container.class_service],
):
    """Return class labs.

    HTTP: GET /api/classes/get_class_labs.

    Inputs: class_repo, class_service."""
    user = get_current_user()
    school_id = parse_optional_int(request.args.get("school_id"))
    role_context = get_requested_role_context()

    # Execute the database lookup with the filters specified below.
    classes_query = Classes.query.order_by(Classes.Name.asc())

    if school_id and school_id > 0:
        # Execute the database lookup with the filters specified below.
        school = Schools.query.filter(Schools.Id == school_id).first()

        if school is None:
            abort(404)

        classes_query = classes_query.filter(Classes.SchoolId == school_id)

    # Execute the database lookup with the filters specified below.
    classes_list = classes_query.all()

    if user is not None:
        accessible_classes = class_service.get_assigned_classes(user, class_repo)
        accessible_classes = filter_classes_for_context(
            user, accessible_classes, role_context, class_repo, class_service
        )
        accessible_class_ids = {extract_class_id(class_item) for class_item in accessible_classes}

        classes_list = [cls for cls in classes_list if cls.Id in accessible_class_ids]

        if school_id and school_id > 0 and role_context is not None and not classes_list:
            abort(403)

        if (
            school_id
            and school_id > 0
            and role_context is None
            and not class_service.user_can_access_school(user, school_id, class_repo)
        ):
            abort(403)

    return jsonify(serialize_class_sections(classes_list))


@classes_api.route('/get_class_name_from_id/<class_id>', methods=["GET"])
@jwt_required()
@inject
def get_class_name_from_id(
    class_id,
    class_repository: ClassRepository = Provide[Container.class_repo],
    class_service: ClassService = Provide[Container.class_service],
):
    """Return class name from id.

    HTTP: GET /api/classes/get_class_name_from_id/<class_id>.

    Inputs: class_id, class_repository, class_service."""
    parsed_class_id = parse_optional_int(class_id)

    if parsed_class_id is None:
        abort(404)

    class_item = class_repository.get_class_by_id(parsed_class_id)

    if not class_service.user_can_access_class_item(current_user, class_item, class_repository):
        abort(403)

    return jsonify([{"name": class_item.Name}])
