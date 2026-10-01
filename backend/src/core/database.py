"""Define the single SQLAlchemy extension shared by the backend.

Models and repositories import this db object to share metadata and sessions.
The application factory calls init_app to bind it to the configured Flask app;
importing this module alone does not connect to the database."""

from flask_sqlalchemy import SQLAlchemy

# Create the shared SQLAlchemy object used by the application.
db = SQLAlchemy()
