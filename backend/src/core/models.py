"""Map the backend's persistent records to SQLAlchemy models.

Define users, schools, class memberships, assignment hierarchies, submissions,
test cases, grading records, student incentives, upload state, and office-hours
records. Columns and relationships use the shared db extension. This module
describes the data model; feature modules decide when records are changed."""

from src.core.constants import chicago_now
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import relationship
from src.core.database import db
from sqlalchemy import Text

# SQLAlchemy entities for identity.


class Schools(db.Model):
    """Map schools to the existing database schema and relationships."""

    __tablename__ = "Schools"
    __table_args__ = (
        CheckConstraint(
            "AuthProvider IN ('google', 'microsoft')",
            name="ck_schools_auth_provider",
        ),
    )

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(255), nullable=False, unique=True)
    AuthProvider = Column(String(20), nullable=False)
    RequiresLabAndLecture = Column(Boolean, nullable=False, default=True)
    UseDefaultMaterials = Column(Boolean, nullable=False, default=False, server_default="0")

    # Expose related database records through this ORM attribute.
    Classes = relationship("Classes", back_populates="School")


class Users(db.Model):
    """Map users to the existing database schema and relationships."""

    __tablename__ = "Users"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    Username = Column(String(255), nullable=False, unique=True)
    Firstname = Column(String(255), nullable=False)
    Lastname = Column(String(255), nullable=False)
    Email = Column(String(320), nullable=False)
    StudentNumber = Column(String(255), nullable=False)
    IsLocked = Column(Boolean, nullable=False, default=False)

    # Expose related database records through this ORM attribute.
    Submissions = relationship("Submissions")
    # Expose related database records through this ORM attribute.
    ClassAssignments = relationship("ClassAssignments")


class Classes(db.Model):
    """Map classes to the existing database schema and relationships."""

    __tablename__ = "Classes"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(255), nullable=False)
    # Link this record to the referenced table through a foreign key.
    SchoolId = Column(Integer, ForeignKey("Schools.Id"), nullable=False)

    DefaultContentInitialized = Column(Boolean, nullable=False, default=False, server_default="0")

    # Expose related database records through this ORM attribute.
    School = relationship("Schools", back_populates="Classes")


class Labs(db.Model):
    """Map labs to the existing database schema and relationships."""

    __tablename__ = "Labs"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(255), nullable=False)
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )

    # Expose related database records through this ORM attribute.
    ClassAssignments = relationship(
        "ClassAssignments",
        foreign_keys="ClassAssignments.LabId",
    )


class LectureSections(db.Model):
    """Map lecture sections to the existing database schema and relationships."""

    __tablename__ = "LectureSections"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(255), nullable=False)
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )

    # Expose related database records through this ORM attribute.
    ClassAssignments = relationship(
        "ClassAssignments",
        foreign_keys="ClassAssignments.LectureId",
    )


class ClassAssignments(db.Model):
    """Map class assignments to the existing database schema and relationships."""

    __tablename__ = "ClassAssignments"

    # Link this record to the referenced table through a foreign key.
    UserId = Column(Integer, ForeignKey("Users.Id"), primary_key=True)
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(Integer, ForeignKey("Classes.Id"), primary_key=True)
    # Link this record to the referenced table through a foreign key.
    LabId = Column(
        Integer,
        ForeignKey("Labs.Id"),
        nullable=True,
    )
    # Link this record to the referenced table through a foreign key.
    LectureId = Column(
        Integer,
        ForeignKey("LectureSections.Id"),
        nullable=True,
    )
    Role = Column(Integer, nullable=False, default=0)


class LoginAttempts(db.Model):
    """Failed or otherwise recorded login attempts."""

    __tablename__ = "LoginAttempts"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    Username = Column(String(255), nullable=False)
    IPAddress = Column(String(64), nullable=False, default="")
    AttemptedAt = Column(DateTime, nullable=False, default=chicago_now)


# SQLAlchemy entities for assignments.


class Modules(db.Model):
    """Map modules to the existing database schema and relationships."""

    __tablename__ = "Modules"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(Integer, ForeignKey("Classes.Id"), nullable=False)
    Name = Column(String(1000), nullable=False)
    FirstName = Column(String(1000))
    FileTimestamp = Column(String(32))
    Start = Column(DateTime, nullable=False)
    End = Column(DateTime, nullable=False)

    # Expose related database records through this ORM attribute.
    Projects = relationship(
        "Projects",
        back_populates="Module",
        cascade="all, delete-orphan",
    )


class Assignments(db.Model):
    """Map assignments to the existing database schema and relationships."""

    __tablename__ = "Assignments"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    AssignmentType = Column(String(20), nullable=False)
    # Link this record to the referenced table through a foreign key.
    ProjectId = Column(
        "ParentAssignmentId",
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=True,
    )
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=True,
    )
    # Link this record to the referenced table through a foreign key.
    ModuleId = Column(
        Integer,
        ForeignKey("Modules.Id", ondelete="SET NULL"),
        nullable=True,
    )
    CheckpointNumber = Column(Integer, nullable=True)
    Enabled = Column(Boolean, nullable=False, default=True)
    Name = Column(String(1000), nullable=False)
    FirstName = Column(String(1000))
    Language = Column(String(45))
    solutionpath = Column(String(1000))
    AsnDescriptionPath = Column(String(1000))
    AdditionalFilePath = Column(Text)
    CreatedAt = Column(DateTime, nullable=False, default=chicago_now)

    # Expose related database records through this ORM attribute.
    Project = relationship(
        "Assignments",
        remote_side=[Id],
        foreign_keys=[ProjectId],
        back_populates="Checkpoints",
    )
    # Expose related database records through this ORM attribute.
    Checkpoints = relationship(
        "Assignments",
        foreign_keys=[ProjectId],
        back_populates="Project",
        cascade="all, delete-orphan",
    )

    __mapper_args__ = {
        "polymorphic_on": AssignmentType,
        "polymorphic_identity": "assignment",
    }


class Projects(Assignments):
    """Map projects to the existing database schema and relationships."""

    # Expose related database records through this ORM attribute.
    Module = relationship(
        "Modules",
        back_populates="Projects",
        foreign_keys=[Assignments.ModuleId],
    )
    # Expose related database records through this ORM attribute.
    Submissions = relationship(
        "Submissions",
        foreign_keys="Submissions.Project",
    )
    __mapper_args__ = {"polymorphic_identity": "project"}


class Checkpoints(Assignments):
    """Map checkpoints to the existing database schema and relationships."""

    __mapper_args__ = {"polymorphic_identity": "checkpoint"}


class StudentHiddenModules(db.Model):
    """Modules hidden for a specific student."""

    __tablename__ = "StudentHiddenModules"

    # Link this record to the referenced table through a foreign key.
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Link this record to the referenced table through a foreign key.
    ModuleId = Column(
        Integer,
        ForeignKey("Modules.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    CreatedAt = Column(DateTime, nullable=False, default=chicago_now)


class DefaultContentImports(db.Model):
    """Map default content imports to the existing database schema and relationships."""

    __tablename__ = "DefaultContentImports"
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(Integer, ForeignKey("Classes.Id"), primary_key=True)
    # Use this column as the unique identifier for each database record.
    SourceKey = Column(String(700, collation="utf8mb4_bin"), primary_key=True)
    # Link this record to the referenced table through a foreign key.
    ModuleId = Column(Integer, ForeignKey("Modules.Id"), nullable=False)
    # Link this record to the referenced table through a foreign key.
    AssignmentId = Column(Integer, ForeignKey("Assignments.Id"), nullable=False)


# SQLAlchemy entities for submissions.


class Submissions(db.Model):
    """Map submissions to the existing database schema and relationships."""

    __tablename__ = "Submissions"
    __table_args__ = (
        CheckConstraint(
            "SubmissionMethod IN ('upload', 'editor', 'unknown')",
            name="ck_submissions_submission_method",
        ),
    )

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    OutputFilepath = Column(String(1000), nullable=False)
    CodeFilepath = Column(String(1000), nullable=False)
    IsPassing = Column(Boolean, nullable=False, default=False)
    IsCheckpoint = Column(Boolean, nullable=False, default=False)
    # Link this record to the referenced table through a foreign key.
    CheckpointId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="SET NULL"),
        nullable=True,
    )
    SubmissionMethod = Column(String(20), nullable=False, default="unknown")
    Time = Column(DateTime, nullable=False, default=chicago_now)
    # Link this record to the referenced table through a foreign key.
    User = Column(Integer, ForeignKey("Users.Id"), nullable=False)
    # Link this record to the referenced table through a foreign key.
    Project = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
    TestCaseResults = Column(Text)


class Testcases(db.Model):
    """Map testcases to the existing database schema and relationships."""

    __tablename__ = "Testcases"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    # Link this record to the referenced table through a foreign key.
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Link this record to the referenced table through a foreign key.
    CheckpointId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=True,
    )
    Name = Column(Text)
    input = Column(Text)
    Output = Column(Text)
    Hidden = Column(Boolean, nullable=False, default=False)
    SortOrder = Column(Integer, nullable=False, default=0)
    Checkpoint = Column(Boolean, nullable=False, default=False)


class Grades(db.Model):
    """Map grades to the existing database schema and relationships."""

    __tablename__ = "Grades"

    # Link this record to the referenced table through a foreign key.
    SubmissionId = Column(
        Integer,
        ForeignKey("Submissions.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    GradeScope = Column(String(20), nullable=False)
    # Link this record to the referenced table through a foreign key.
    UserId = Column(Integer, ForeignKey("Users.Id"), nullable=False)
    # Link this record to the referenced table through a foreign key.
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
    Grade = Column(Integer, nullable=False, default=0)
    ScoringMode = Column(String(20))
    ErrorPointsJson = Column(Text)
    ErrorDefsJson = Column(Text)
    UpdatedAt = Column(DateTime, nullable=False, default=chicago_now)

    __mapper_args__ = {
        "polymorphic_on": GradeScope,
        "polymorphic_identity": "grade",
    }


class MainAssignmentGrades(Grades):
    """Rows in Grades whose GradeScope is ``main``."""

    __mapper_args__ = {"polymorphic_identity": "main"}


class CheckpointGrades(Grades):
    """Rows in Grades whose GradeScope is ``checkpoint``."""

    __mapper_args__ = {"polymorphic_identity": "checkpoint"}


class StudentSuggestions(db.Model):
    """Suggestions submitted by authenticated students."""

    __tablename__ = "StudentSuggestions"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    # Link this record to the referenced table through a foreign key.
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        nullable=False,
    )
    Suggestion = Column(Text, nullable=False)
    SubmittedAt = Column(DateTime, nullable=False, default=chicago_now)


class StudentUploadState(db.Model):
    """Per-assignment upload cooldown state for a student."""

    __tablename__ = "StudentUploadStates"

    # Link this record to the referenced table through a foreign key.
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Link this record to the referenced table through a foreign key.
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Main-assignment state uses 0; checkpoint state uses an Assignments.Id.
    CheckpointId = Column(Integer, primary_key=True, default=0)
    CooldownLiftedAt = Column(DateTime)
    UpdatedAt = Column(
        DateTime,
        nullable=False,
        default=chicago_now,
        onupdate=chicago_now,
    )


class SubmissionAnnotations(db.Model):
    """Line-level manual grading annotations attached to a submission."""

    __tablename__ = "SubmissionAnnotations"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    # Link this record to the referenced table through a foreign key.
    SubmissionId = Column(
        Integer,
        ForeignKey("Submissions.Id", ondelete="CASCADE"),
        nullable=False,
    )
    StartLine = Column(Integer, nullable=False)
    EndLine = Column(Integer, nullable=False)
    ErrorId = Column(String(80), nullable=False)
    Count = Column(Integer, nullable=False, default=1)
    Note = Column(Text)


# SQLAlchemy entities for incentives.


class StudentCheckpointSkips(db.Model):
    """Checkpoint skips purchased by a student."""

    __tablename__ = "StudentCheckpointSkips"

    # Link this record to the referenced table through a foreign key.
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Link this record to the referenced table through a foreign key.
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Link this record to the referenced table through a foreign key.
    CheckpointId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    SpentStars = Column(Integer, nullable=False)
    CreatedAt = Column(DateTime, nullable=False, default=chicago_now)


class StudentCooldownSkips(db.Model):
    """One-time submission cooldown skips purchased by a student."""

    __tablename__ = "StudentCooldownSkips"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    # Link this record to the referenced table through a foreign key.
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Link this record to the referenced table through a foreign key.
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Main-assignment state uses 0; checkpoint state uses an Assignments.Id.
    CheckpointId = Column(Integer, nullable=False, default=0)
    SpentStars = Column(Integer, nullable=False)
    CreatedAt = Column(DateTime, nullable=False, default=chicago_now)
    UsedAt = Column(DateTime)


class StudentTestcaseInputPurchases(db.Model):
    """Testcase inputs purchased by a student."""

    __tablename__ = "StudentTestcaseInputPurchases"

    # Link this record to the referenced table through a foreign key.
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Link this record to the referenced table through a foreign key.
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Use this column as the unique identifier for each database record.
    CheckpointId = Column(Integer, primary_key=True, default=0)
    # Link this record to the referenced table through a foreign key.
    TestcaseId = Column(
        Integer,
        ForeignKey("Testcases.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    SpentStars = Column(Integer, nullable=False)
    CreatedAt = Column(DateTime, nullable=False, default=chicago_now)


class StudentStarAwards(db.Model):
    """
    Stars awarded for completing a main assignment or checkpoint.

    A student's current balance is derived from these awards minus the
    star-purchase tables. No separate balance or generic spending row is stored.
    """

    __tablename__ = "StudentStarAwards"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    # Link this record to the referenced table through a foreign key.
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Link this record to the referenced table through a foreign key.
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Main-assignment awards use 0.
    CheckpointId = Column(Integer, nullable=False, default=0)
    # Link this record to the referenced table through a foreign key.
    SubmissionId = Column(
        Integer,
        ForeignKey("Submissions.Id", ondelete="SET NULL"),
    )
    AwardType = Column(String(40), nullable=False)
    AwardedStars = Column(Integer, nullable=False)
    BaseAwardStars = Column(Integer, nullable=False)
    Multiplier = Column(Integer, nullable=False, default=1)
    StartedEarly = Column(Boolean, nullable=False, default=False)
    AwardedAt = Column(DateTime, nullable=False, default=chicago_now)


# SQLAlchemy entities for office hours.


class OfficeHoursSession(db.Model):
    """One class-level office-hours window started by an admin or instructor."""

    __tablename__ = "OfficeHoursSessions"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )
    StartedAt = Column(DateTime, nullable=False, default=chicago_now)
    EndsAt = Column(DateTime, nullable=False)
    # Link this record to the referenced table through a foreign key.
    StartedByUserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="SET NULL"),
    )


class OfficeHoursQueueEntry(db.Model):
    """One office-hours visit, retained after the visit ends for history."""

    __tablename__ = "OfficeHoursQueueEntries"

    # Use this column as the unique identifier for each database record.
    Id = Column(Integer, primary_key=True, autoincrement=True)
    # Link this record to the referenced table through a foreign key.
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Link this record to the referenced table through a foreign key.
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Link this record to the referenced table through a foreign key.
    ModuleId = Column(
        Integer,
        ForeignKey("Modules.Id", ondelete="CASCADE"),
        nullable=False,
    )
    JoinedAt = Column(DateTime, nullable=False, default=chicago_now)
    SelectedAt = Column(DateTime)
    # Link this record to the referenced table through a foreign key.
    SelectedByUserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="SET NULL"),
    )
    CooldownExemptUntil = Column(DateTime)
    CompletedAt = Column(DateTime)


# ORM model registry. Importing this package registers every mapped table.


__all__ = [
    "Schools",
    "Users",
    "Classes",
    "Labs",
    "LectureSections",
    "ClassAssignments",
    "LoginAttempts",
    "Modules",
    "Assignments",
    "Projects",
    "Checkpoints",
    "DefaultContentImports",
    "StudentHiddenModules",
    "Submissions",
    "Testcases",
    "Grades",
    "MainAssignmentGrades",
    "CheckpointGrades",
    "StudentSuggestions",
    "SubmissionAnnotations",
    "StudentUploadState",
    "OfficeHoursSession",
    "OfficeHoursQueueEntry",
    "StudentCheckpointSkips",
    "StudentCooldownSkips",
    "StudentTestcaseInputPurchases",
    "StudentStarAwards",
]
