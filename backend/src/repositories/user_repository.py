"""Read account records and maintain login and enrollment-related user data.

Find or create users, resolve class roles and section membership, and record
failed login attempts and account locks. Provide the user details needed by
authentication, class administration, and grading interfaces."""

import datetime
import re
from typing import Dict
from typing import List
from src.core.constants import STUDENT_ROLE
from src.core.constants import chicago_now
from src.core.constants import to_chicago_datetime
from src.core.database import db
from sqlalchemy import func
from src.core.models import ClassAssignments
from src.core.models import LectureSections
from src.core.models import Users
from src.core.models import LoginAttempts
from src.core.models import Labs
from flask_jwt_extended import current_user


class UserRepository:
    """Represent user repository within the users component."""

    def get_user_status(self) -> str:
        """Return user status."""
        return str(self.get_highest_class_role(getattr(current_user, "Id", None)))

    def get_highest_class_role(self, user_id: int) -> int:
        """Return the user's highest class-assignment role."""
        # Handle the case where user_id is None.
        if user_id is None:
            return STUDENT_ROLE

        role = (
            db.session.query(func.max(ClassAssignments.Role))
            .filter(ClassAssignments.UserId == int(user_id))
            .scalar()
        )
        try:
            return int(role) if role is not None else STUDENT_ROLE
        # Convert this failure into the fallback result or error response below.
        except (TypeError, ValueError):
            return STUDENT_ROLE

    def getUserByName(self, username: str) -> Users:
        """
        Returns a user object from the database based on the given username.

        Args:
        - username (str): the username of the user to retrieve

        Returns:
        - Users: the user object corresponding to the given username, or None if no such user exists
        """
        # Execute the database lookup with the filters specified below.
        user = Users.query.filter(Users.Username == username).one_or_none()
        return user

    def get_user(self, user_id: int) -> Users:
        """
        Retrieves a user from the database by their ID.

        Args:
            user_id (int): The ID of the user to retrieve.

        Returns:
            Users: The user object if found, otherwise None.
        """
        # Execute the database lookup with the filters specified below.
        user = Users.query.filter(Users.Id == user_id).one_or_none()
        return user

    def doesUserExist(self, username: str) -> bool:
        """Return whether a user exists for the supplied username."""
        return (
            db.session.query(Users.Id)
            .filter(Users.Username == username)
            .first()
            is not None
        )

    def create_user(
        self, username: str, first_name: str, last_name: str, email: str, student_number: str
    ):
        """Creates a new user with the given information and adds it to the database.

        Args:
            username (str): The username of the new user.
            first_name (str): The first name of the new user.
            last_name (str): The last name of the new user.
            email (str): The email address of the new user.
            student_number (str): The student number of the new user.

        Returns:
            None
        """
        user = Users(
            Username=username,
            Firstname=first_name,
            Lastname=last_name,
            Email=email,
            StudentNumber=student_number,
            IsLocked=False,
        )
        # Stage the new records in the current database transaction.
        db.session.add(user)
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    def get_all_users(self) -> List[Users]:
        """Retrieves all users from the database.

        Returns:
            List[Users]: A list of all user objects in the database.
        """
        # Execute the database lookup with the filters specified below.
        user = Users.query.all()
        return user

    def get_all_users_by_cid(self, class_id) -> List[Users]:
        """Return student users assigned to the given class."""
        return (
            Users.query.join(ClassAssignments, ClassAssignments.UserId == Users.Id)
            .filter(
                ClassAssignments.ClassId == int(class_id),
                ClassAssignments.Role == STUDENT_ROLE,
            )
            .order_by(Users.Lastname.asc(), Users.Firstname.asc(), Users.Id.asc())
            .all()
        )

    def send_attempt_data(self, username: str, ipadr: str, time: datetime):
        """Record a failed login attempt using America/Chicago wall time."""
        attempt_time = time
        if isinstance(attempt_time, datetime.datetime):
            attempt_time = to_chicago_datetime(attempt_time)
        elif isinstance(attempt_time, datetime.date):
            attempt_time = datetime.datetime.combine(attempt_time, datetime.time.min)
        else:
            try:
                attempt_time = datetime.datetime.strptime(
                    str(attempt_time),
                    "%Y/%m/%d %H:%M:%S",
                )
            except (TypeError, ValueError):
                attempt_time = chicago_now()

        # Stage the new records in the current database transaction.
        db.session.add(
            LoginAttempts(
                IPAddress=str(ipadr or ""),
                Username=str(username or ""),
                AttemptedAt=attempt_time,
            )
        )
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    def can_user_login(self, username: str) -> int:
        """Returns the number of login attempts made by a user with the given username.

        Args:
            username (str): The username of the user to check.

        Returns:
            int: The number of login attempts made by the user.
        """
        # Execute the database lookup with the filters specified below.
        number = LoginAttempts.query.filter(LoginAttempts.Username == username).count()
        return number

    def clear_failed_attempts(self, username: str):
        """Delete all recorded login attempts for a username."""
        # Execute the database lookup with the filters specified below.
        LoginAttempts.query.filter(LoginAttempts.Username == username).delete(
            synchronize_session=False
        )
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    def lock_user_account(self, username: str):
        """Locks the user account associated with the given username. This triggers if the same username fails to login 5 times in a row.

        Args:
            username (str): The username of the user account to be locked.

        Returns:
            None
        """
        # Execute the database lookup with the filters specified below.
        query = Users.query.filter(Users.Username == username).one()
        query.IsLocked = True
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()

    def get_user_lectures(self, userIds: List[int], class_id) -> Dict[int, str]:
        """Return lecture-section names for the requested users in one class."""
        result = {int(user_id): "" for user_id in userIds}
        # Handle the case where not userIds.
        if not userIds:
            return result

        # Execute the database lookup with the filters specified below.
        rows = (
            db.session.query(ClassAssignments.UserId, LectureSections.Name)
            .outerjoin(LectureSections, LectureSections.Id == ClassAssignments.LectureId)
            .filter(
                ClassAssignments.UserId.in_(userIds),
                ClassAssignments.ClassId == int(class_id),
            )
            .all()
        )
        # Process each (user_id, lecture_name) from rows.
        for user_id, lecture_name in rows:
            if lecture_name is not None:
                result[int(user_id)] = str(lecture_name)
        return result

    def get_user_labs(self, userIds: List[int], class_id) -> Dict[int, int]:
        """Return lab numbers for the requested users without per-user queries."""
        result: Dict[int, int] = {int(user_id): -1 for user_id in userIds}
        # Handle the case where not userIds.
        if not userIds:
            return result

        # Execute the database lookup with the filters specified below.
        rows = (
            db.session.query(ClassAssignments.UserId, Labs.Name)
            .outerjoin(Labs, Labs.Id == ClassAssignments.LabId)
            .filter(
                ClassAssignments.UserId.in_(userIds),
                ClassAssignments.ClassId == int(class_id),
            )
            .all()
        )
        # Process each (user_id, lab_name) from rows.
        for user_id, lab_name in rows:
            if not lab_name:
                continue
            match = re.search(r"\d+", str(lab_name))
            if match:
                result[int(user_id)] = int(match.group(0))
        return result

    def get_user_email(self, userId) -> str:
        """
        Retrieves the email of a user with the given userId.

        Args:
            userId (int): The ID of the user to retrieve the email for.

        Returns:
            str: The email of the user with the given userId.
        """
        # Execute the database lookup with the filters specified below.
        query = Users.query.filter(Users.Id == userId).one()
        email = query.Email
        return email

    def get_StudentNumber(self, user_id):
        """
        Returns the student number of a user with the given user_id.

        Args:
        - user_id: int, the id of the user whose student number is to be retrieved.

        Returns:
        - StudentNumber: str, the student number of the user with the given user_id.
        """
        # Execute the database lookup with the filters specified below.
        query = Users.query.filter(Users.Id == user_id).one()
        StudentNumber = query.StudentNumber
        return StudentNumber

    def unlock_student_account(self, user_id):
        """Unlock a user and clear their failed-login attempts in one transaction."""
        # Execute the database lookup with the filters specified below.
        user = Users.query.filter(Users.Id == user_id).one()
        user.IsLocked = False
        # Execute the database lookup with the filters specified below.
        LoginAttempts.query.filter(LoginAttempts.Username == user.Username).delete(
            synchronize_session=False
        )
        # Commit the pending database changes so they persist beyond this request.
        db.session.commit()
