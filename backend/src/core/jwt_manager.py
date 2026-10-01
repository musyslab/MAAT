"""Define the JWT extension shared by authentication and protected endpoints.

The application factory binds this object to Flask. Identity serialization,
user lookup, and missing-user callbacks are registered in src.auth so token
handling and account resolution use the same extension instance."""

from flask_jwt_extended import JWTManager

# Create the shared JWTManager object used by the application.
jwt = JWTManager()
