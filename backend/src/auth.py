"""Authenticate accounts and connect identity providers to backend user records.

Handle login, account creation, class enrollment creation, effective-role queries,
and school-specific OAuth configuration and token verification. Include the client
for the external PAM authentication process and the JWT callbacks that resolve
signed-in users for protected endpoints.

Endpoints use /api/auth/<handler_name>."""

from abc import ABC
from abc import abstractmethod
import os
import requests
import json
from typing import Any
from src.core.models import Schools
from typing import Tuple
from src.core.constants import STUDENT_ROLE
from src.core.constants import TEACHER_ROLE
from src.core.models import ClassAssignments
from src.core.models import Classes
from src.core.models import Labs
from src.core.models import LectureSections
from typing import Dict
from dependency_injector.wiring import Provide
from dependency_injector.wiring import inject
from flask import make_response
from flask import request
from flask_jwt_extended import create_access_token
from flask_jwt_extended import current_user
from flask_jwt_extended import jwt_required
from http import HTTPStatus
from src.core.blueprints import auth_api
from src.core.container import Container
from src.core.config import env_bool
from src.core.config import env_int
from src.core.config import require_env
from src.repositories.class_repository import ClassRepository
from src.repositories.user_repository import UserRepository
from typing import Literal
from flask import current_app
from itsdangerous import URLSafeTimedSerializer
from jwt import PyJWKClient
import jwt as pyjwt
from itsdangerous import BadSignature
from itsdangerous import SignatureExpired
from src.core.jwt_manager import jwt
from src.core.models import Users
from sqlalchemy import func


# Authentication interface and HTTP client for the separate PAM process.


# Read optional account-request fields using the existing empty fallback.

def get_value_or_empty(dictionary, key):
    """Return value or empty.

    Inputs: dictionary, key."""
    # Handle the case where key in dictionary.
    if key in dictionary:
        return dictionary[key]
    return ""


class AuthenticationService(ABC):
    """Contract for a credential authentication provider."""

    @abstractmethod
    def login(self, username: str, password: str) -> bool:
        """Return whether the provider accepts the supplied credentials."""
        pass


class PAMAuthenticationService(AuthenticationService):
    """Authenticate credentials through the separate host PAM service."""

    def login(self, username, password):
        # Preserve the existing development-mode authentication bypass.
        """Handle login for this component.

        Inputs: username, password."""
        # Handle the case where env_bool('FLASK_DEBUG', False).
        if env_bool("FLASK_DEBUG", False):
            return True

        url = (os.getenv("AUTH_URL") or "").strip()
        # Reject this case with the exception below.
        if not url:
            raise RuntimeError("AUTH_URL is not set.")

        data = {
            "username": json.dumps(username),
            "password": json.dumps(password),
        }

        # Call the remote HTTP service with the request settings below.
        response = requests.post(url, json=data, timeout=30)
        # Treat an unsuccessful HTTP status as an exception.
        response.raise_for_status()
        # Decode the remote response as JSON before reading its fields.
        response_json = response.json()
        return bool(response_json.get("success", False))

# Account sessions, school-specific OAuth verification, and JWT user callbacks.


# Helpers for auth configuration.


def parse_int(value: Any) -> int:
    """Parse an integer request value, returning the supplied fallback on failure.

    Inputs: value."""
    try:
        return int(value)
    # Convert this failure into the fallback result or error response below.
    except Exception:
        return 0


def normalize_email(value: str) -> str:
    """Normalize email.

    Inputs: value."""
    return (value or "").strip().lower()


# School authentication-provider settings and provider-specific environment names.


def get_school_by_id(school_id: int):
    """Return school by id.

    Inputs: school_id."""
    # Return an empty or negative result when this guard matches.
    if school_id <= 0:
        return None
    return Schools.query.filter(Schools.Id == school_id).first()


def get_school_auth_provider(school: Any) -> str:
    """Return school auth provider.

    Inputs: school."""
    return str(getattr(school, "AuthProvider", "") or "").strip().lower()


def school_requires_lab_and_lecture(school: Any) -> bool:
    """Handle school requires lab and lecture for this component.

    Inputs: school."""
    return bool(getattr(school, "RequiresLabAndLecture", True))


def microsoft_env_name(school_id: int, setting: str) -> str:
    """Handle microsoft env name for this component.

    Inputs: school_id, setting."""
    return f"MICROSOFT_SCHOOL_{school_id}_{setting}"


def get_microsoft_oauth_settings(school_id: int) -> Tuple[str, str]:
    """Return microsoft oauth settings.

    Inputs: school_id."""
    client_id = (os.environ.get(microsoft_env_name(school_id, "CLIENT_ID")) or "").strip()
    tenant_id = (os.environ.get(microsoft_env_name(school_id, "TENANT_ID")) or "").strip()
    return client_id, tenant_id


# Auth authorization and current-user scope checks.


def is_user_locked(user: Any) -> bool:
    """Determine whether is user locked.

    Inputs: user."""
    return bool(getattr(user, "IsLocked", False))


def get_user_global_role(user: Any) -> int:
    """Return the highest class-assignment role for a user."""
    # Handle the case where user is None or getattr(user, 'Id', None) is None.
    if user is None or getattr(user, "Id", None) is None:
        return STUDENT_ROLE

    # Execute the database lookup with the filters specified below.
    role = (
        ClassAssignments.query.with_entities(func.max(ClassAssignments.Role))
        .filter(ClassAssignments.UserId == int(user.Id))
        .scalar()
    )
    return parse_int(role) if role is not None else STUDENT_ROLE


def get_assignment_role(assignment: Any) -> int:
    """Return assignment role.

    Inputs: assignment."""
    return parse_int(getattr(assignment, "Role", STUDENT_ROLE))


def user_class_assignments(user: Any):
    """Handle user class assignments for this component.

    Inputs: user."""
    # Return an empty or negative result when this guard matches.
    if user is None:
        return []

    return ClassAssignments.query.filter(ClassAssignments.UserId == user.Id).all()


def user_has_school_assignment(user_id: int, school_id: int) -> bool:
    """Handle user has school assignment for this component.

    Inputs: user_id, school_id."""
    return (
        Classes.query.join(ClassAssignments, Classes.Id == ClassAssignments.ClassId)
        .filter(
            ClassAssignments.UserId == user_id,
            Classes.SchoolId == school_id,
        )
        .first()
    ) is not None


def build_access_summary(user: Any) -> Dict[str, Any]:
    """Build access summary.

    Inputs: user."""
    assignments = user_class_assignments(user)
    assignment_roles = [get_assignment_role(assignment) for assignment in assignments]
    effective_role = max([STUDENT_ROLE] + assignment_roles)

    can_study = any(role == STUDENT_ROLE for role in assignment_roles)
    can_teach = any(role >= TEACHER_ROLE for role in assignment_roles)

    if not assignments:
        can_study = True

    default_dashboard = "admin" if can_teach else "student"

    return {
        "role": effective_role,
        "can_teach": can_teach,
        "can_study": can_study,
        "default_dashboard": default_dashboard,
    }


def build_session_payload(user: Any, access_token: str, message: str = "Success") -> Dict[str, Any]:
    """Build session payload.

    Inputs: user, access_token, message."""
    return {
        "message": message,
        "access_token": access_token,
        **build_access_summary(user),
    }


def set_class_assignment_role(user_id: int, class_id: int, role: int) -> None:
    """Set class assignment role.

    Inputs: user_id, class_id, role.
    Database changes are committed at the explicit transaction boundaries below."""
    # Execute the database lookup with the filters specified below.
    assignment = ClassAssignments.query.filter(
        ClassAssignments.UserId == user_id,
        ClassAssignments.ClassId == class_id,
    ).first()

    if assignment is not None:
        assignment.Role = role
        # Commit the pending database changes so they persist beyond this request.
        ClassAssignments.query.session.commit()


def is_valid_school_selection(school_id: int, class_id: int, lab_id: int, lecture_id: int) -> bool:
    """Determine whether is valid school selection.

    Inputs: school_id, class_id, lab_id, lecture_id."""
    # Return an empty or negative result when this guard matches.
    if school_id <= 0 or class_id <= 0:
        return False

    # Execute the database lookup with the filters specified below.
    school = Schools.query.filter(Schools.Id == school_id).first()
    # Execute the database lookup with the filters specified below.
    school_class = Classes.query.filter(
        Classes.Id == class_id,
        Classes.SchoolId == school_id,
    ).first()

    # Return an empty or negative result when this guard matches.
    if school is None or school_class is None:
        return False

    # Handle the case where not school_requires_lab_and_lecture(school).
    if not school_requires_lab_and_lecture(school):
        return True

    # Return an empty or negative result when this guard matches.
    if lab_id <= 0 or lecture_id <= 0:
        return False

    # Execute the database lookup with the filters specified below.
    lab = Labs.query.filter(
        Labs.Id == lab_id,
        Labs.ClassId == class_id,
    ).first()
    # Execute the database lookup with the filters specified below.
    lecture = LectureSections.query.filter(
        LectureSections.Id == lecture_id,
        LectureSections.ClassId == class_id,
    ).first()

    return lab is not None and lecture is not None


def split_display_name(name: str) -> Tuple[str, str]:
    """Handle split display name for this component.

    Inputs: name."""
    cleaned = (name or "").strip()
    # Handle the case where not cleaned.
    if not cleaned:
        return "", ""

    # Microsoft tenants commonly format the display-name claim as
    # "Last, First". When given_name/family_name are absent, normalize that
    # format before falling back to the usual "First Last" split.
    if cleaned.count(",") == 1:
        last_name, first_name = (part.strip() for part in cleaned.split(",", 1))
        # Handle the case where first_name and last_name.
        if first_name and last_name:
            return first_name, last_name

    parts = cleaned.split()
    # Handle the case where len(parts) == 1.
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


# Auth HTTP endpoints for session.


@auth_api.route('/get_user_role', methods=["GET"])
@jwt_required()
@inject
def get_user_role(user_repo: UserRepository = Provide[Container.user_repo]):
    """Return user role.

    HTTP: GET /api/auth/get_user_role.

    Inputs: user_repo."""
    return user_repo.get_user_status()


@auth_api.route('/access_summary', methods=["GET"])
@jwt_required()
def access_summary():
    """Handle access summary for this component.

    HTTP: GET /api/auth/access_summary."""
    return make_response(build_access_summary(current_user), HTTPStatus.OK)


@auth_api.route('/auth', methods=["POST"])
def auth():
    """Authenticate the supplied credentials using the configured authentication service.

    HTTP: POST /api/auth/auth."""
    return make_response(
        {
            "message": "Password login is no longer supported. Choose a school and use Google or Microsoft."
        },
        HTTPStatus.GONE,
    )


@auth_api.route('/create_user', methods=["POST"])
def create_user():
    """Create user.

    HTTP: POST /api/auth/create_user."""
    return make_response(
        {"message": "Password-based account creation is no longer supported."},
        HTTPStatus.GONE,
    )


@auth_api.route('/add_class', methods=["POST"])
@jwt_required()
@inject
def add_class(
    user_repo: UserRepository = Provide[Container.user_repo],
    class_repo: ClassRepository = Provide[Container.class_repo],
):
    """Enroll the current user using validated class and section identifiers."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return make_response({"message": "Expected a JSON object"}, HTTPStatus.BAD_REQUEST)
    class_id = parse_int(data.get("classid"))
    lab_id = parse_int(data.get("labid"))
    lecture_id = parse_int(data.get("lectureid"))
    class_item = class_repo.get_class_by_id(class_id) if class_id > 0 else None
    if class_item is None:
        return make_response({"message": "Invalid class"}, HTTPStatus.BAD_REQUEST)
    school = get_school_by_id(int(class_item.SchoolId))
    if not is_valid_school_selection(int(class_item.SchoolId), class_id, lab_id, lecture_id):
        return make_response({"message": "Invalid class or section selection"}, HTTPStatus.BAD_REQUEST)
    user = user_repo.get_user(int(current_user.Id))
    if user is None:
        return make_response({"message": "User not found"}, HTTPStatus.NOT_FOUND)
    requires_sections = school_requires_lab_and_lecture(school)
    class_repo.add_class_assignment(
        class_id,
        lab_id if requires_sections else None,
        int(user.Id),
        lecture_id if requires_sections else None,
    )
    # New memberships default to student; retain any existing class-specific role.
    access_token = create_access_token(identity=user)
    return make_response(build_session_payload(user, access_token), HTTPStatus.OK)


# Policy constants for auth.


OAuthProvider = Literal["google", "microsoft"]


LOCKED_ACCOUNT_MESSAGE = "Your account has been locked! Please contact an administrator!"


# Verify provider tokens and build Google/Microsoft signup identities.


def oauth_signup_serializer() -> URLSafeTimedSerializer:
    """Handle oauth signup serializer for this component."""
    return URLSafeTimedSerializer(str(current_app.config["JWT_SECRET_KEY"]))


def create_oauth_signup_token(profile: Dict[str, Any]) -> str:
    """Create oauth signup token.

    Inputs: profile."""
    salt = os.environ.get("OAUTH_SIGNUP_TOKEN_SALT", "oauth-signup")
    return oauth_signup_serializer().dumps(profile, salt=salt)


def decode_oauth_signup_token(token: str) -> Dict[str, Any]:
    """Handle decode oauth signup token for this component.

    Inputs: token."""
    salt = os.environ.get("OAUTH_SIGNUP_TOKEN_SALT", "oauth-signup")
    max_age = env_int("OAUTH_SIGNUP_TOKEN_MAX_AGE_SECONDS", 900)
    return oauth_signup_serializer().loads(token, salt=salt, max_age=max_age)


def verify_google_id_token(id_token: str) -> Dict[str, Any]:
    """Handle verify google id token for this component.

    Inputs: id_token."""
    client_id = require_env("GOOGLE_OAUTH_CLIENT_ID")
    jwks_client = PyJWKClient("https://www.googleapis.com/oauth2/v3/certs")
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    claims = pyjwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=["accounts.google.com", "https://accounts.google.com"],
    )

    # Reject this case with the exception below.
    if not claims.get("email_verified", False):
        raise ValueError("Google account email is not verified.")

    return claims


def verify_microsoft_id_token(id_token: str, school_id: int) -> Dict[str, Any]:
    """Handle verify microsoft id token for this component.

    Inputs: id_token, school_id."""
    client_id, tenant_id = get_microsoft_oauth_settings(school_id)

    # Reject this case with the exception below.
    if not client_id:
        raise RuntimeError(f"{microsoft_env_name(school_id, 'CLIENT_ID')} is not set.")
    # Reject this case with the exception below.
    if not tenant_id:
        raise RuntimeError(f"{microsoft_env_name(school_id, 'TENANT_ID')} is not set.")

    jwks_client = PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)

    return pyjwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
    )


def build_oauth_profile(
    provider: OAuthProvider,
    claims: Dict[str, Any],
    school_id: int,
) -> Dict[str, Any]:
    """Build oauth profile.

    Inputs: provider, claims, school_id."""
    if provider == "google":
        email = normalize_email(str(claims.get("email") or ""))
        first_name = (claims.get("given_name") or "").strip()
        last_name = (claims.get("family_name") or "").strip()
        display_name = (claims.get("name") or "").strip()
        external_id = str(claims.get("sub") or "")
    else:
        email = normalize_email(
            str(claims.get("preferred_username") or claims.get("email") or claims.get("upn") or "")
        )
        first_name = (claims.get("given_name") or "").strip()
        last_name = (claims.get("family_name") or "").strip()
        display_name = (claims.get("name") or "").strip()
        external_id = str(claims.get("oid") or claims.get("sub") or "")

    if not display_name:
        display_name = " ".join(part for part in [first_name, last_name] if part).strip()

    if not first_name and not last_name:
        first_name, last_name = split_display_name(display_name)

    # Reject this case with the exception below.
    if not email:
        raise ValueError("No email address was returned by the identity provider.")

    return {
        "provider": provider,
        "school_id": school_id,
        "external_id": external_id,
        "email": email,
        "username": email,
        "first_name": first_name,
        "last_name": last_name,
        "display_name": display_name or email,
    }


def verify_oauth_token(
    provider: OAuthProvider,
    id_token: str,
    school_id: int,
) -> Dict[str, Any]:
    """Handle verify oauth token for this component.

    Inputs: provider, id_token, school_id."""
    if provider == "google":
        claims = verify_google_id_token(id_token)
    elif provider == "microsoft":
        claims = verify_microsoft_id_token(id_token, school_id)
    else:
        raise ValueError("Unsupported OAuth provider.")
    return build_oauth_profile(provider, claims, school_id)


# Auth HTTP endpoints for oauth.


@auth_api.route('/oauth_config', methods=["GET"])
def oauth_config():
    """Handle oauth config for this component.

    HTTP: GET /api/auth/oauth_config."""
    school_id = parse_int(request.args.get("school_id"))
    school = get_school_by_id(school_id)

    # Return the response below when this validation or access check matches.
    if school is None:
        return make_response({"message": "School not found."}, HTTPStatus.NOT_FOUND)

    provider = get_school_auth_provider(school)
    response = {
        "enabled": False,
        "provider": provider,
        "school": {
            "id": school.Id,
            "name": school.Name,
            "requires_lab_and_lecture": school_requires_lab_and_lecture(school),
        },
        "google_client_id": "",
        "microsoft_client_id": "",
        "microsoft_authority": "",
    }

    if provider == "google":
        client_id = (os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
        response["enabled"] = bool(client_id)
        response["google_client_id"] = client_id
    elif provider == "microsoft":
        client_id, tenant_id = get_microsoft_oauth_settings(school.Id)
        response["enabled"] = bool(client_id and tenant_id)
        response["microsoft_client_id"] = client_id
        response["microsoft_authority"] = (
            f"https://login.microsoftonline.com/{tenant_id}" if tenant_id else ""
        )
    else:
        return make_response(
            {"message": "This school has an unsupported authentication provider."},
            HTTPStatus.CONFLICT,
        )

    return make_response(response, HTTPStatus.OK)


@auth_api.route('/oauth_login', methods=["POST"])
@inject
def oauth_login(user_repo: UserRepository = Provide[Container.user_repo]):
    """Handle oauth login for this component.

    HTTP: POST /api/auth/oauth_login.

    Inputs: user_repo."""
    input_json = request.get_json() or {}
    provider = str(get_value_or_empty(input_json, "provider")).strip().lower()
    id_token = get_value_or_empty(input_json, "id_token").strip()
    school_id = parse_int(get_value_or_empty(input_json, "school_id"))

    # Return the response below when this validation or access check matches.
    if not provider or not id_token or school_id <= 0:
        return make_response(
            {"message": "provider, id_token, and school_id are required."},
            HTTPStatus.NOT_ACCEPTABLE,
        )

    school = get_school_by_id(school_id)
    # Return the response below when this validation or access check matches.
    if school is None:
        return make_response({"message": "School not found."}, HTTPStatus.NOT_FOUND)

    school_provider = get_school_auth_provider(school)
    # Return the response below when this validation or access check matches.
    if provider != school_provider:
        return make_response(
            {"message": f"{school.Name} requires {school_provider.title()} login."},
            HTTPStatus.FORBIDDEN,
        )

    try:
        profile = verify_oauth_token(provider, id_token, school_id)
    # Convert this failure into the fallback result or error response below.
    except Exception as exc:
        return make_response(
            {"message": f"OAuth login failed: {str(exc)}"},
            HTTPStatus.FORBIDDEN,
        )

    username = profile["username"]

    if user_repo.doesUserExist(username):
        user = user_repo.getUserByName(username)
        # Return the response below when this validation or access check matches.
        if is_user_locked(user):
            return make_response(
                {"message": LOCKED_ACCOUNT_MESSAGE},
                HTTPStatus.FORBIDDEN,
            )

        # Return the response below when this validation or access check matches.
        if not user_has_school_assignment(int(user.Id), school_id):
            return make_response(
                {"message": f"Your MAAT account is not assigned to {school.Name}."},
                HTTPStatus.FORBIDDEN,
            )

        access_token = create_access_token(identity=user)
        return make_response(
            build_session_payload(user, access_token),
            HTTPStatus.OK,
        )

    signup_token = create_oauth_signup_token(profile)
    return make_response(
        {
            "message": "New OAuth User",
            "signup_token": signup_token,
            "oauth_profile": {
                "provider": profile["provider"],
                "email": profile["email"],
                "first_name": profile["first_name"],
                "last_name": profile["last_name"],
                "display_name": profile["display_name"],
            },
        },
        HTTPStatus.OK,
    )


@auth_api.route('/create_oauth_user', methods=["POST"])
@inject
def create_oauth_user(
    user_repo: UserRepository = Provide[Container.user_repo],
    class_repo: ClassRepository = Provide[Container.class_repo],
):
    """Create oauth user.

    HTTP: POST /api/auth/create_oauth_user.

    Inputs: user_repo, class_repo."""
    input_json = request.get_json() or {}
    signup_token = get_value_or_empty(input_json, "signup_token").strip()
    student_number = get_value_or_empty(input_json, "id")
    school_id = parse_int(get_value_or_empty(input_json, "school_id"))
    class_id = parse_int(get_value_or_empty(input_json, "class_id"))
    lab_id = parse_int(get_value_or_empty(input_json, "lab_id"))
    lecture_id = parse_int(get_value_or_empty(input_json, "lecture_id"))

    # Return the response below when this validation or access check matches.
    if not signup_token:
        return make_response(
            {"message": "signup_token is required."},
            HTTPStatus.NOT_ACCEPTABLE,
        )

    school = get_school_by_id(school_id)
    # Return the response below when this validation or access check matches.
    if school is None:
        return make_response({"message": "School not found."}, HTTPStatus.NOT_FOUND)

    requires_lab_and_lecture = school_requires_lab_and_lecture(school)

    # Return the response below when this validation or access check matches.
    if not (student_number and school_id > 0 and class_id > 0):
        return make_response(
            {"message": "Missing required data. School ID and class are required."},
            HTTPStatus.NOT_ACCEPTABLE,
        )

    # Return the response below when this validation or access check matches.
    if requires_lab_and_lecture and (lab_id <= 0 or lecture_id <= 0):
        return make_response(
            {"message": "Please choose a valid lecture and lab."},
            HTTPStatus.NOT_ACCEPTABLE,
        )

    if not is_valid_school_selection(school_id, class_id, lab_id, lecture_id):
        selection_message = (
            "The selected school, class, lecture, and lab combination is invalid."
            if requires_lab_and_lecture
            else "The selected school and class combination is invalid."
        )
        return make_response(
            {"message": selection_message},
            HTTPStatus.NOT_ACCEPTABLE,
        )

    try:
        profile = decode_oauth_signup_token(signup_token)
    # Convert this failure into the fallback result or error response below.
    except SignatureExpired:
        return make_response(
            {"message": "Your sign-up session expired. Please sign in again."},
            HTTPStatus.FORBIDDEN,
        )
    # Convert this failure into the fallback result or error response below.
    except BadSignature:
        return make_response(
            {"message": "Invalid sign-up session. Please sign in again."},
            HTTPStatus.FORBIDDEN,
        )

    token_school_id = parse_int(profile.get("school_id"))
    token_provider = str(profile.get("provider") or "").strip().lower()

    # Return the response below when this validation or access check matches.
    if token_school_id != school_id:
        return make_response(
            {"message": "The selected school does not match the school used to sign in."},
            HTTPStatus.FORBIDDEN,
        )

    # Return the response below when this validation or access check matches.
    if token_provider != get_school_auth_provider(school):
        return make_response(
            {"message": "The login provider does not match the selected school."},
            HTTPStatus.FORBIDDEN,
        )

    username = normalize_email(str(profile.get("username") or profile.get("email") or ""))
    email = normalize_email(str(profile.get("email") or ""))
    first_name = str(profile.get("first_name") or "").strip()
    last_name = str(profile.get("last_name") or "").strip()

    # Return the response below when this validation or access check matches.
    if not username or not email:
        return make_response(
            {"message": "OAuth profile did not contain a usable email address."},
            HTTPStatus.NOT_ACCEPTABLE,
        )

    if user_repo.doesUserExist(username):
        user = user_repo.getUserByName(username)
        # Return the response below when this validation or access check matches.
        if is_user_locked(user):
            return make_response(
                {"message": LOCKED_ACCOUNT_MESSAGE},
                HTTPStatus.FORBIDDEN,
            )
    else:
        user_repo.create_user(username, first_name, last_name, email, student_number)
        user = user_repo.getUserByName(username)

    assignment_lab_id = lab_id if requires_lab_and_lecture else None
    assignment_lecture_id = lecture_id if requires_lab_and_lecture else None
    class_repo.add_class_assignment(
        class_id,
        assignment_lab_id,
        int(user.Id),
        assignment_lecture_id,
    )

    access_token = create_access_token(identity=user)
    return make_response(
        build_session_payload(user, access_token),
        HTTPStatus.OK,
    )


# JWT identity, user lookup, and rejected-account callbacks.


@jwt.user_identity_loader
def user_identity_lookup(user):
    """Handle user identity lookup for this component.

    Inputs: user."""
    return str(user.Id)


@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data):
    """Handle user lookup callback for this component.

    Inputs: _jwt_header, jwt_data."""
    identity = jwt_data["sub"]
    # Execute the database lookup with the filters specified below.
    user = Users.query.filter_by(Id=identity).one_or_none()
    # Return an empty or negative result when this guard matches.
    if user is None or is_user_locked(user):
        return None
    return user


@jwt.user_lookup_error_loader
def user_lookup_error_callback(_jwt_header, _jwt_data):
    """Handle user lookup error callback for this component.

    Inputs: _jwt_header, _jwt_data."""
    return make_response(
        {"message": LOCKED_ACCOUNT_MESSAGE},
        HTTPStatus.FORBIDDEN,
    )
