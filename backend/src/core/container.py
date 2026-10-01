"""Provide repositories and account/class services through dependency injection.

Expose the provider names referenced by Provide markers in feature functions.
Authentication and class-service instances are constructed through deferred
imports because their classes share files with HTTP handlers. The application
factory wires feature modules after all providers have been declared.
"""

from src.repositories.user_repository import UserRepository
from dependency_injector import containers
from dependency_injector import providers
from src.repositories.class_repository import ClassRepository
from src.repositories.assignment_repository import AssignmentRepository
from src.repositories.submission_repository import SubmissionRepository


def create_authentication_service():
    """Construct authentication logic after the feature modules have loaded."""
    # Delay this import until the service is requested to avoid circular startup imports.
    from src.auth import PAMAuthenticationService

    return PAMAuthenticationService()


def create_class_service():
    """Construct enrollment logic without importing its routes during setup."""
    # Load enrollment logic only when its provider creates a service instance.
    from src.classes import ClassService

    return ClassService()


class Container(containers.DeclarativeContainer):
    """Represent container within the container component."""

    # Hold settings supplied to the dependency-injection container.
    config = providers.Configuration()

    # Factory providers create a fresh repository for each dependency request.
    class_repo = providers.Factory(ClassRepository)

    project_repo = providers.Factory(AssignmentRepository)

    submission_repo = providers.Factory(SubmissionRepository)

    user_repo = providers.Factory(UserRepository)

    # Construct services through the deferred-import helpers above.
    auth_service = providers.Factory(create_authentication_service)

    class_service = providers.Factory(create_class_service)
