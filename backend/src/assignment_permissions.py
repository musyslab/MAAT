"""Control who can view and manage modules, projects, and checkpoints.

Resolve class membership and effective staff roles, enforce student visibility,
and check access to assignment resources. Visibility endpoints can hide or show
a module for an individual student or a class. Other features reuse these checks
before returning assignment data or making changes.

Endpoints use /api/assignment_permissions/<handler_name>."""

from flask_jwt_extended import current_user
from src.core.models import ClassAssignments
from src.core.constants import STUDENT_ROLE
from src.core.database import db
from sqlalchemy import func
from src.core.constants import ADMIN_ROLE
from src.core.constants import TEACHER_ROLE
from http import HTTPStatus
from flask import make_response
from src.core.models import Classes
from src.core.models import Projects
from src.core.models import Modules
from src.core.models import StudentHiddenModules
from src.core.models import Checkpoints
from src.core.container import Container
from src.repositories.assignment_repository import AssignmentRepository
from dependency_injector.wiring import Provide
from dependency_injector.wiring import inject
from flask import jsonify
from flask_jwt_extended import jwt_required
from src.core.blueprints import assignment_permissions_api
from flask import request

# Projects authorization and current-user scope checks.


def parse_bool(v) -> bool:
    """Interpret a request boolean using this feature’s accepted spellings and fallback.

    Inputs: v."""
    # Handle the case where isinstance(v, bool).
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def current_user_id() -> int:
    """Handle current user id for this component."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    return parse_int(getattr(current_user, "Id", 0), 0)


def current_user_global_role() -> int:
    """Handle current user global role for this component."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    user_id = current_user_id()

    # Handle the case where user_id <= 0.
    if user_id <= 0:
        return STUDENT_ROLE

    role_value = (
        db.session.query(func.max(ClassAssignments.Role))
        .filter(ClassAssignments.UserId == user_id)
        .scalar()
    )
    return parse_int(role_value, STUDENT_ROLE)


def is_admin_user() -> bool:
    """Determine whether is admin user."""
    return current_user_global_role() >= ADMIN_ROLE


def is_teacher_user() -> bool:
    """Determine whether is teacher user."""
    return current_user_global_role() == TEACHER_ROLE


def current_user_assignment_for_class(class_id: int):
    """Handle current user assignment for class for this component.

    Inputs: class_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    class_id = parse_int(class_id, 0)
    user_id = current_user_id()

    # Return an empty or negative result when this guard matches.
    if class_id <= 0 or user_id <= 0:
        return None

    try:
        return ClassAssignments.query.filter(
            ClassAssignments.UserId == user_id,
            ClassAssignments.ClassId == class_id,
        ).first()
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return None


def current_user_assignment_role_for_class(class_id: int) -> int | None:
    """Handle current user assignment role for class for this component.

    Inputs: class_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    assignment = current_user_assignment_for_class(class_id)

    # Return an empty or negative result when this guard matches.
    if assignment is None:
        return None

    return parse_int(getattr(assignment, "Role", None), STUDENT_ROLE)


def is_staff_user() -> bool:
    """Determine whether is staff user."""
    return current_user_global_role() >= TEACHER_ROLE


def access_denied_response(status=HTTPStatus.UNAUTHORIZED):
    """Handle access denied response for this component.

    Inputs: status."""
    return make_response({"message": "Access Denied"}, status)


def user_can_access_class_id(class_id: int) -> bool:
    """Determine whether user can access class id.

    Inputs: class_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    class_id = parse_int(class_id, 0)
    # Return an empty or negative result when this guard matches.
    if class_id <= 0:
        return False

    # Execute the database lookup with the filters specified below.
    class_item = Classes.query.filter(Classes.Id == class_id).first()
    # Return an empty or negative result when this guard matches.
    if class_item is None:
        return False

    # Handle the case where is_admin_user().
    if is_admin_user():
        return True

    assignment_role = current_user_assignment_role_for_class(class_id)

    return assignment_role is not None and assignment_role >= TEACHER_ROLE


def user_can_access_project_id(project_id: int) -> bool:
    """Determine whether user can access project id.

    Inputs: project_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    project_id = parse_int(project_id, 0)
    # Return an empty or negative result when this guard matches.
    if project_id <= 0:
        return False

    # Execute the database lookup with the filters specified below.
    project = Projects.query.filter(Projects.Id == project_id).first()
    # Return an empty or negative result when this guard matches.
    if project is None:
        return False

    return user_can_access_class_id(int(getattr(project, "ClassId", 0) or 0))


def current_user_is_enrolled_in_class(class_id: int) -> bool:
    """Handle current user is enrolled in class for this component.

    Inputs: class_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    class_id = parse_int(class_id, 0)
    # Return an empty or negative result when this guard matches.
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
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return False


def current_user_is_enrolled_in_project_class(project_id: int) -> bool:
    """Handle current user is enrolled in project class for this component.

    Inputs: project_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    project_id = parse_int(project_id, 0)
    # Return an empty or negative result when this guard matches.
    if project_id <= 0:
        return False

    # Execute the database lookup with the filters specified below.
    project = Projects.query.filter(Projects.Id == project_id).first()
    # Return an empty or negative result when this guard matches.
    if project is None:
        return False

    class_id = parse_int(getattr(project, "ClassId", 0) or 0, 0)
    return current_user_is_enrolled_in_class(class_id)


def student_module_is_hidden_for_current_user(module_id: int) -> bool:
    """Handle student module is hidden for current user for this component.

    Inputs: module_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    module_id = parse_int(module_id, 0)
    # Return an empty or negative result when this guard matches.
    if module_id <= 0:
        return False

    try:
        # Execute the database lookup with the filters specified below.
        module = Modules.query.filter(Modules.Id == module_id).first()
        # Return an empty or negative result when this guard matches.
        if module is not None and user_can_access_class_id(int(getattr(module, "ClassId", 0) or 0)):
            return False
    # Ignore this failure and allow the surrounding operation to continue.
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
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return False


def current_user_can_access_visible_module_id(module_id: int) -> bool:
    """Determine whether current user can access visible module id.

    Inputs: module_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    module_id = parse_int(module_id, 0)
    # Return an empty or negative result when this guard matches.
    if module_id <= 0:
        return False

    # Handle the case where user_can_access_module_id(module_id).
    if user_can_access_module_id(module_id):
        return True

    # Execute the database lookup with the filters specified below.
    module = Modules.query.filter(Modules.Id == module_id).first()
    # Return an empty or negative result when this guard matches.
    if module is None:
        return False

    class_id = parse_int(getattr(module, "ClassId", 0) or 0, 0)
    return current_user_is_enrolled_in_class(
        class_id
    ) and not student_module_is_hidden_for_current_user(module_id)


def project_is_hidden_for_current_student(project) -> bool:
    """Handle project is hidden for current student for this component.

    Inputs: project."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    # Return an empty or negative result when this guard matches.
    if not project:
        return False

    project_id = parse_int(getattr(project, "Id", 0) or 0, 0)
    # Return an empty or negative result when this guard matches.
    if project_id > 0 and user_can_access_project_id(project_id):
        return False

    module_id = parse_int(getattr(project, "ModuleId", 0) or 0, 0)
    return module_id > 0 and student_module_is_hidden_for_current_user(module_id)


def current_user_can_access_visible_project_id(project_id: int) -> bool:
    """Determine whether current user can access visible project id.

    Inputs: project_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    project_id = parse_int(project_id, 0)
    # Return an empty or negative result when this guard matches.
    if project_id <= 0:
        return False

    # Handle the case where user_can_access_project_id(project_id).
    if user_can_access_project_id(project_id):
        return True

    # Execute the database lookup with the filters specified below.
    project = Projects.query.filter(Projects.Id == project_id).first()
    # Return an empty or negative result when this guard matches.
    if project is None:
        return False

    class_id = parse_int(getattr(project, "ClassId", 0) or 0, 0)
    # Return an empty or negative result when this guard matches.
    if not current_user_is_enrolled_in_class(class_id):
        return False

    return not project_is_hidden_for_current_student(project)


def current_user_can_download_project_files(project_id: int) -> bool:
    """Determine whether current user can download project files.

    Inputs: project_id."""
    # Handle the case where user_can_access_project_id(project_id).
    if user_can_access_project_id(project_id):
        return True

    return current_user_can_access_visible_project_id(project_id)


def user_can_access_module_id(module_id: int) -> bool:
    """Determine whether user can access module id.

    Inputs: module_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    module_id = parse_int(module_id, 0)
    # Return an empty or negative result when this guard matches.
    if module_id <= 0:
        return False

    # Execute the database lookup with the filters specified below.
    module = Modules.query.filter(Modules.Id == module_id).first()
    # Return an empty or negative result when this guard matches.
    if module is None:
        return False

    return user_can_access_class_id(int(getattr(module, "ClassId", 0) or 0))


def user_can_access_checkpoint_id(checkpoint_id: int) -> bool:
    """Determine whether user can access checkpoint id.

    Inputs: checkpoint_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    checkpoint_id = parse_int(checkpoint_id, 0)
    # Return an empty or negative result when this guard matches.
    if checkpoint_id <= 0:
        return False

    # Execute the database lookup with the filters specified below.
    checkpoint = Checkpoints.query.filter(Checkpoints.Id == checkpoint_id).first()
    # Return an empty or negative result when this guard matches.
    if checkpoint is None:
        return False

    return user_can_access_project_id(int(getattr(checkpoint, "ProjectId", 0) or 0))


def user_can_access_student_id(student_id: int) -> bool:
    """Determine whether user can access student id.

    Inputs: student_id."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    student_id = parse_int(student_id, 0)
    # Return an empty or negative result when this guard matches.
    if student_id <= 0:
        return False

    # Handle the case where is_admin_user().
    if is_admin_user():
        return True

    # Execute the database lookup with the filters specified below.
    assignments = ClassAssignments.query.filter(ClassAssignments.UserId == student_id).all()
    return any(user_can_access_class_id(int(assignment.ClassId)) for assignment in assignments)


def filter_projects_for_current_user(projects):
    """Filter projects for current user.

    Inputs: projects."""
    return [
        project
        for project in projects
        if user_can_access_class_id(int(getattr(project, "ClassId", 0) or 0))
    ]


# Projects HTTP endpoints for visibility.


@assignment_permissions_api.route('/set_student_module_visibility', methods=["POST"])
@jwt_required()
@inject
def set_student_module_visibility(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """Set student module visibility.

    HTTP: POST /api/assignment_permissions/set_student_module_visibility.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id"), 0)
    student_id = parse_int(data.get("student_id"), 0)
    module_id = parse_int(data.get("module_id"), 0)
    hidden = parse_bool(data.get("hidden"))

    # Return the response below when this validation or access check matches.
    if class_id <= 0 or student_id <= 0 or module_id <= 0:
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(class_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Execute the database lookup with the filters specified below.
    module = Modules.query.filter(Modules.Id == module_id).first()
    # Return the response below when this validation or access check matches.
    if not module or int(getattr(module, "ClassId", 0) or 0) != class_id:
        return make_response({"message": "Module not found"}, HTTPStatus.NOT_FOUND)

    # Execute the database lookup with the filters specified below.
    assigned = ClassAssignments.query.filter(
        ClassAssignments.ClassId == class_id,
        ClassAssignments.UserId == student_id,
    ).first()
    # Return the response below when this validation or access check matches.
    if assigned is None:
        return make_response(
            {"message": "Student is not enrolled in this class"}, HTTPStatus.BAD_REQUEST
        )

    project_repo.set_student_module_hidden(student_id, module_id, hidden)

    return jsonify(
        {
            "studentId": student_id,
            "moduleId": module_id,
            "hidden": bool(hidden),
        }
    )


@assignment_permissions_api.route('/set_module_visibility_for_all', methods=["POST"])
@jwt_required()
@inject
def set_module_visibility_for_all(
    project_repo: AssignmentRepository = Provide[Container.project_repo],
):
    """Set module visibility for all.

    HTTP: POST /api/assignment_permissions/set_module_visibility_for_all.

    Inputs: project_repo."""
    # Defer shared feature imports until the request or helper call.
    from src.assignment_setup import parse_int

    # Stop here when the caller does not have the required access.
    if not is_staff_user():
        return access_denied_response()

    data = request.get_json(silent=True) or {}
    class_id = parse_int(data.get("class_id"), 0)
    module_id = parse_int(data.get("module_id"), 0)
    hidden = parse_bool(data.get("hidden"))

    # Return the response below when this validation or access check matches.
    if class_id <= 0 or module_id <= 0:
        return make_response({"message": "Missing required fields"}, HTTPStatus.BAD_REQUEST)
    # Stop here when the caller does not have the required access.
    if not user_can_access_class_id(class_id):
        return access_denied_response(HTTPStatus.FORBIDDEN)

    # Execute the database lookup with the filters specified below.
    module = Modules.query.filter(Modules.Id == module_id).first()
    # Return the response below when this validation or access check matches.
    if not module or int(getattr(module, "ClassId", 0) or 0) != class_id:
        return make_response({"message": "Module not found"}, HTTPStatus.NOT_FOUND)

    student_ids = project_repo.set_module_hidden_for_class(module_id, hidden)

    return jsonify(
        {
            "moduleId": module_id,
            "hidden": bool(hidden),
            "studentIds": student_ids,
        }
    )


TEST_USER_ID = -1
TEST_USER_FOLDER_PREFIX = "__maat_test_user_"


def is_test_user_request() -> bool:
    """A virtual test identity owned by the authenticated staff account.

    No Users row or enrollment is created. Submission rows retain a valid
    account foreign key, with a dedicated folder separating test work.
    """
    from flask import has_request_context
    if not has_request_context():
        return False
    requested = (request.headers.get("X-MAAT-Test-User") == "1"
                 or request.form.get("student_id") == str(TEST_USER_ID)
                 or request.args.get("student_id") == str(TEST_USER_ID))
    if not requested:
        return False
    try:
        return is_staff_user()
    except (AttributeError, RuntimeError):
        return False


def is_test_submission(submission) -> bool:
    """Identify virtual test work without extending SubmissionMethod values."""
    path = str(getattr(submission, "CodeFilepath", "") or "").replace("\\", "/")
    return f"/{TEST_USER_FOLDER_PREFIX}" in path
