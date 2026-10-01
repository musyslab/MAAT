"""Attach the signed-in user to frontend error reports.

The authenticated logging endpoint reads a JSON report, adds the username, and
raises the serialized report as an exception for the application's error handling
and monitoring. It does not create a separate database error record.

Endpoints use /api/error/<handler_name>."""

from flask import request
from flask_jwt_extended import current_user
from flask_jwt_extended import jwt_required
from src.core.blueprints import error_api
import json


# Errors HTTP endpoints for routes.


@error_api.route('/log_error', methods=["POST"])
@jwt_required()
def log_error():
    """Handle log error for this component.

    HTTP: POST /api/error/log_error."""
    # Read the JSON request body using the parsing options below.
    error = request.get_json()
    error["user"] = current_user.Username
    raise Exception(json.dumps(error))
