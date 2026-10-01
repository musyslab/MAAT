"""Expose school choices and check whether a user can access a school.

Provide login-page school options and authenticated school listings. Derive a
user's accessible schools from class assignments and effective roles, then return
the school metadata required by login and class-selection interfaces.

Endpoints use /api/schools/<handler_name>."""

from src.core.constants import ADMIN_ROLE
from src.core.constants import STUDENT_ROLE
from src.core.models import ClassAssignments
from src.core.models import Classes
from src.core.models import Schools
from sqlalchemy import func
from flask import abort
from flask import jsonify
from flask_jwt_extended import current_user
from flask_jwt_extended import get_current_user
from flask_jwt_extended import jwt_required
from src.core.blueprints import schools_api


# School selection endpoints and school access rules.


# Schools authorization and current-user scope checks.


def parse_optional_int(value):
    """Parse an optional identifier using the existing empty-value convention.

    Inputs: value."""
    try:
        return int(value)
    # Convert this failure into the fallback result or error response below.
    except (TypeError, ValueError):
        return None


def get_user_id(user):
    """Return user id.

    Inputs: user."""
    # Return an empty or negative result when this guard matches.
    if user is None:
        return None

    return parse_optional_int(getattr(user, "Id", None))


def get_user_global_role(user) -> int:
    """Return the highest class-assignment role for a user."""
    user_id = get_user_id(user)
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


def get_assigned_school_ids(user_id: int):
    """Return assigned school ids.

    Inputs: user_id."""
    # Execute the database lookup with the filters specified below.
    school_rows = (
        Classes.query.with_entities(Classes.SchoolId)
        .join(ClassAssignments, Classes.Id == ClassAssignments.ClassId)
        .filter(
            ClassAssignments.UserId == user_id,
            Classes.SchoolId.isnot(None),
        )
        .distinct()
        .all()
    )

    school_ids = set()

    # Process each row from school_rows.
    for row in school_rows:
        school_id = row.SchoolId if hasattr(row, "SchoolId") else row[0]
        parsed_school_id = parse_optional_int(school_id)

        if parsed_school_id is not None:
            school_ids.add(parsed_school_id)

    return school_ids


def get_accessible_schools_for_user(user):
    """Return accessible schools for user.

    Inputs: user."""
    # Handle the case where user is None.
    if user is None:
        return Schools.query.order_by(Schools.Name.asc()).all()

    user_id = get_user_id(user)

    # Return an empty or negative result when this guard matches.
    if user_id is None:
        return []

    # Handle the case where get_user_global_role(user) >= ADMIN_ROLE.
    if get_user_global_role(user) >= ADMIN_ROLE:
        return Schools.query.order_by(Schools.Name.asc()).all()

    school_ids = get_assigned_school_ids(user_id)

    # Return an empty or negative result when this guard matches.
    if not school_ids:
        return []

    return Schools.query.filter(Schools.Id.in_(school_ids)).order_by(Schools.Name.asc()).all()


def user_can_access_school(user, school_id: int) -> bool:
    """Determine whether user can access school.

    Inputs: user, school_id."""
    # Return an empty or negative result when this guard matches.
    if user is None:
        return False

    user_id = get_user_id(user)
    parsed_school_id = parse_optional_int(school_id)

    # Return an empty or negative result when this guard matches.
    if user_id is None or parsed_school_id is None:
        return False

    # Handle the case where get_user_global_role(user) >= ADMIN_ROLE.
    if get_user_global_role(user) >= ADMIN_ROLE:
        return True

    return (
        Classes.query.join(ClassAssignments, Classes.Id == ClassAssignments.ClassId)
        .filter(
            ClassAssignments.UserId == user_id,
            Classes.SchoolId == parsed_school_id,
        )
        .first()
    ) is not None


# Policy constants for schools.


SUPPORTED_AUTH_PROVIDERS = {"google", "microsoft"}


# Serialize school choices and public school-login options.


def serialize_school(school):
    """Serialize school.

    Inputs: school."""
    return {
        "id": school.Id,
        "name": school.Name,
    }


def serialize_login_school(school):
    """Serialize login school.

    Inputs: school."""
    return {
        **serialize_school(school),
        "auth_provider": str(school.AuthProvider or "").strip().lower(),
    }


# Schools HTTP endpoints for routes.


@schools_api.route('/get_school_login_options', methods=["GET"])
def get_school_login_options():
    """Return school login options.

    HTTP: GET /api/schools/get_school_login_options."""
    # Execute the database lookup with the filters specified below.
    schools = Schools.query.order_by(Schools.Name.asc()).all()
    configured_schools = [
        school
        for school in schools
        if str(school.AuthProvider or "").strip().lower() in SUPPORTED_AUTH_PROVIDERS
    ]
    return jsonify([serialize_login_school(school) for school in configured_schools])


@schools_api.route('/get_schools', methods=["GET"])
@jwt_required(optional=True)
def get_schools():
    """Return schools.

    HTTP: GET /api/schools/get_schools."""
    user = get_current_user()
    schools = get_accessible_schools_for_user(user)
    return jsonify([serialize_school(school) for school in schools])


@schools_api.route('/validate_school_access/<school_id>', methods=["GET"])
@jwt_required()
def validate_school_access(school_id):
    """Validate school access.

    HTTP: GET /api/schools/validate_school_access/<school_id>.

    Inputs: school_id."""
    try:
        parsed_school_id = int(school_id)
    except (TypeError, ValueError):
        abort(404)

    # Execute the database lookup with the filters specified below.
    school = Schools.query.filter(Schools.Id == parsed_school_id).first()

    if school is None:
        abort(404)

    if not user_can_access_school(current_user, parsed_school_id):
        abort(403)

    return jsonify(serialize_school(school))
