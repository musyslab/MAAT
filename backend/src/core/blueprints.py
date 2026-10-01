"""Declare each feature's HTTP namespace and the registration order.

Create one blueprint per feature file and expose FEATURE_BLUEPRINTS for the
application factory. Keeping these objects separate from handler imports avoids
circular registration dependencies. The factory derives URL prefixes and module
imports from the feature names in this registry."""

from flask import Blueprint

ai_suggestions_api = Blueprint("ai_suggestions_api", __name__)
analytics_api = Blueprint("analytics_api", __name__)
assignment_permissions_api = Blueprint("assignment_permissions_api", __name__)
assignment_setup_api = Blueprint("assignment_setup_api", __name__)
assignment_tracking_api = Blueprint("assignment_tracking_api", __name__)
assignment_materials_api = Blueprint("assignment_materials_api", __name__)
auth_api = Blueprint("auth_api", __name__)
classes_api = Blueprint("classes_api", __name__)
error_api = Blueprint("error_api", __name__)
office_hours_api = Blueprint("office_hours_api", __name__)
plagiarism_api = Blueprint("plagiarism_api", __name__)
python_ide_api = Blueprint("python_ide_api", __name__)
schools_api = Blueprint("schools_api", __name__)
submissions_api = Blueprint("submissions_api", __name__)
upload_api = Blueprint("upload_api", __name__)

FEATURE_BLUEPRINTS = (
    ("ai_suggestions", ai_suggestions_api),
    ("analytics-dashboard", analytics_api),
    ("assignment_permissions", assignment_permissions_api),
    ("assignment_setup", assignment_setup_api),
    ("assignment_tracking", assignment_tracking_api),
    ("assignment_materials", assignment_materials_api),
    ("auth", auth_api),
    ("classes", classes_api),
    ("error", error_api),
    ("office_hours", office_hours_api),
    ("plagiarism", plagiarism_api),
    ("python_ide", python_ide_api),
    ("schools", schools_api),
    ("submissions", submissions_api),
    ("upload", upload_api),
)
