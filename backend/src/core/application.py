"""Construct and configure the Flask application.

Import every registered feature, wire its injectable functions to the container,
and register each blueprint under the matching /api/<feature> prefix. Load required
environment settings, prepare the project-files directory, configure CORS and JWT,
and bind the shared database and JWT extensions to the application."""

from datetime import timedelta
import importlib
import os
from flask import Flask
from flask_cors import CORS
from src.core.container import Container
from src.core.config import optional_env
from src.core.config import require_env
from src.core.config import env_int
from src.core.config import csv_env
from src.core.config import build_database_uri
from src.core.database import db
from src.core.jwt_manager import jwt
from src.core import models as models
from src.core.blueprints import FEATURE_BLUEPRINTS

# Load and wire each merged feature once, including its injected helper functions.
ROUTE_MODULES = tuple(f"src.{name}" for name, _ in FEATURE_BLUEPRINTS)


def wiring_modules():
    """Load route registrations and return every module requiring DI wiring."""
    return [importlib.import_module(name) for name in ROUTE_MODULES]


def create_app():
    """Create app."""
    app = Flask(__name__)
    container = Container()
    app.container = container

    # Wire the actual owners of injected functions after all routes are imported.
    # Wiring the old aggregate modules would leave moved Provide markers unresolved.
    container.wire(modules=wiring_modules())

    tabot_dir = optional_env("TABOT_DIR", "/tabot-files")
    project_files_dir = os.path.join(tabot_dir, "project-files")

    os.makedirs(project_files_dir, exist_ok=True)

    app.config.update(
        {
            "PROJECT_FILES_DIR": project_files_dir,
            "TEACHER_FILES_DIR": project_files_dir,
            "STUDENT_FILES_DIR": project_files_dir,
            "JWT_SECRET_KEY": require_env("JWT_SECRET_KEY"),
            "MAX_FAILED_LOGINS": env_int("MAX_FAILED_LOGINS", 5),
            "MAX_CONTENT_LENGTH": 16 * 1000 * 1000,
            "JWT_ACCESS_TOKEN_EXPIRES": timedelta(
                hours=env_int("JWT_ACCESS_TOKEN_EXPIRES_HOURS", 1)
            ),
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "SQLALCHEMY_DATABASE_URI": build_database_uri(),
        }
    )

    cors_origins = csv_env("CORS_ORIGINS", "http://localhost:3000")
    CORS(app, supports_credentials=True, origins=cors_origins)

    # URL prefixes match the feature filenames; route suffixes match handlers.
    for name, blueprint in FEATURE_BLUEPRINTS:
        app.register_blueprint(blueprint, url_prefix=f"/api/{name}")

    jwt.init_app(app)
    db.init_app(app)

    return app
