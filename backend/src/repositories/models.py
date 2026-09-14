from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from src.repositories.database import db


class Schools(db.Model):
    __tablename__ = "Schools"
    __table_args__ = (
        CheckConstraint(
            "AuthProvider IN ('google', 'microsoft')",
            name="ck_schools_auth_provider",
        ),
    )

    Id = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(255), nullable=False, unique=True)
    AuthProvider = Column(String(20), nullable=False)
    RequiresLabAndLecture = Column(Boolean, nullable=False, default=True)
    UseDefaultMaterials = Column(Boolean, nullable=False, default=False, server_default="0")

    Classes = relationship("Classes", back_populates="School")


class Users(db.Model):
    __tablename__ = "Users"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    Username = Column(String(255), nullable=False, unique=True)
    Firstname = Column(String(255), nullable=False)
    Lastname = Column(String(255), nullable=False)
    Email = Column(String(320), nullable=False)
    StudentNumber = Column(String(255), nullable=False)
    IsLocked = Column(Boolean, nullable=False, default=False)

    Submissions = relationship("Submissions")
    ClassAssignments = relationship("ClassAssignments")


class Classes(db.Model):
    __tablename__ = "Classes"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(255), nullable=False)
    SchoolId = Column(Integer, ForeignKey("Schools.Id"), nullable=False)

    DefaultContentInitialized = Column(Boolean, nullable=False, default=False, server_default="0")

    School = relationship("Schools", back_populates="Classes")

class Labs(db.Model):
    __tablename__ = "Labs"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(255), nullable=False)
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )

    ClassAssignments = relationship(
        "ClassAssignments",
        foreign_keys="ClassAssignments.LabId",
    )

class LectureSections(db.Model):
    __tablename__ = "LectureSections"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(255), nullable=False)
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )

    ClassAssignments = relationship(
        "ClassAssignments",
        foreign_keys="ClassAssignments.LectureId",
    )

class Modules(db.Model):
    __tablename__ = "Modules"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    ClassId = Column(Integer, ForeignKey("Classes.Id"), nullable=False)
    Name = Column(String(1000), nullable=False)
    FirstName = Column(String(1000))
    FileTimestamp = Column(String(32))
    Start = Column(DateTime, nullable=False)
    End = Column(DateTime, nullable=False)

    Projects = relationship(
        "Projects",
        back_populates="Module",
        cascade="all, delete-orphan",
    )


class Assignments(db.Model):

    __tablename__ = "Assignments"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    AssignmentType = Column(String(20), nullable=False)
    ProjectId = Column(
        "ParentAssignmentId",
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=True,
    )
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=True,
    )
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
    CreatedAt = Column(DateTime, nullable=False, default=datetime.utcnow)

    Project = relationship(
        "Assignments",
        remote_side=[Id],
        foreign_keys=[ProjectId],
        back_populates="Checkpoints",
    )
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
    Module = relationship(
        "Modules",
        back_populates="Projects",
        foreign_keys=[Assignments.ModuleId],
    )
    Submissions = relationship(
        "Submissions",
        foreign_keys="Submissions.Project",
    )
    __mapper_args__ = {"polymorphic_identity": "project"}


class Checkpoints(Assignments):
    __mapper_args__ = {"polymorphic_identity": "checkpoint"}


class ClassAssignments(db.Model):
    __tablename__ = "ClassAssignments"

    UserId = Column(Integer, ForeignKey("Users.Id"), primary_key=True)
    ClassId = Column(Integer, ForeignKey("Classes.Id"), primary_key=True)
    LabId = Column(
        Integer,
        ForeignKey("Labs.Id"),
        nullable=True,
    )
    LectureId = Column(
        Integer,
        ForeignKey("LectureSections.Id"),
        nullable=True,
    )
    Role = Column(Integer, nullable=False, default=0)


class OfficeHoursSession(db.Model):
    """One class-level office-hours window started by an admin or instructor."""

    __tablename__ = "OfficeHoursSessions"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )
    StartedAt = Column(DateTime, nullable=False, default=datetime.utcnow)
    EndsAt = Column(DateTime, nullable=False)
    StartedByUserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="SET NULL"),
    )


class OfficeHoursQueueEntry(db.Model):
    """One office-hours visit, retained after the visit ends for history."""

    __tablename__ = "OfficeHoursQueueEntries"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        nullable=False,
    )
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )
    ModuleId = Column(
        Integer,
        ForeignKey("Modules.Id", ondelete="CASCADE"),
        nullable=False,
    )
    JoinedAt = Column(DateTime, nullable=False, default=datetime.utcnow)
    SelectedAt = Column(DateTime)
    SelectedByUserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="SET NULL"),
    )
    CooldownExemptUntil = Column(DateTime)
    CompletedAt = Column(DateTime)

class Submissions(db.Model):
    __tablename__ = "Submissions"
    __table_args__ = (
        CheckConstraint(
            "SubmissionMethod IN ('upload', 'editor', 'unknown')",
            name="ck_submissions_submission_method",
        ),
    )

    Id = Column(Integer, primary_key=True, autoincrement=True)
    OutputFilepath = Column(String(1000), nullable=False)
    CodeFilepath = Column(String(1000), nullable=False)
    IsPassing = Column(Boolean, nullable=False, default=False)
    IsCheckpoint = Column(Boolean, nullable=False, default=False)
    CheckpointId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="SET NULL"),
        nullable=True,
    )
    SubmissionMethod = Column(String(20), nullable=False, default="unknown")
    Time = Column(DateTime, nullable=False, default=datetime.utcnow)
    User = Column(Integer, ForeignKey("Users.Id"), nullable=False)
    Project = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
    TestCaseResults = Column(Text)

class Testcases(db.Model):
    __tablename__ = "Testcases"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
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

    __tablename__ = "Grades"

    SubmissionId = Column(
        Integer,
        ForeignKey("Submissions.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    GradeScope = Column(String(20), nullable=False)
    UserId = Column(Integer, ForeignKey("Users.Id"), nullable=False)
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
    Grade = Column(Integer, nullable=False, default=0)
    ScoringMode = Column(String(20))
    ErrorPointsJson = Column(Text)
    ErrorDefsJson = Column(Text)
    UpdatedAt = Column(DateTime, nullable=False, default=datetime.utcnow)

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


class LoginAttempts(db.Model):
    """Failed or otherwise recorded login attempts."""

    __tablename__ = "LoginAttempts"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    Username = Column(String(255), nullable=False)
    IPAddress = Column(String(64), nullable=False, default="")
    AttemptedAt = Column(DateTime, nullable=False, default=datetime.utcnow)

class StudentSuggestions(db.Model):
    """Suggestions submitted by authenticated students."""

    __tablename__ = "StudentSuggestions"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        nullable=False,
    )
    Suggestion = Column(Text, nullable=False)
    SubmittedAt = Column(DateTime, nullable=False, default=datetime.utcnow)

class StudentHiddenModules(db.Model):
    """Modules hidden for a specific student."""

    __tablename__ = "StudentHiddenModules"

    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    ModuleId = Column(
        Integer,
        ForeignKey("Modules.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    CreatedAt = Column(DateTime, nullable=False, default=datetime.utcnow)

class StudentUploadState(db.Model):
    """Per-assignment upload cooldown state for a student."""

    __tablename__ = "StudentUploadStates"

    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        primary_key=True,
    )
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
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

class StudentCheckpointSkips(db.Model):
    """Checkpoint skips purchased by a student."""

    __tablename__ = "StudentCheckpointSkips"

    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    CheckpointId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    SpentStars = Column(Integer, nullable=False)
    CreatedAt = Column(DateTime, nullable=False, default=datetime.utcnow)

class StudentCooldownSkips(db.Model):
    """One-time submission cooldown skips purchased by a student."""

    __tablename__ = "StudentCooldownSkips"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        nullable=False,
    )
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Main-assignment state uses 0; checkpoint state uses an Assignments.Id.
    CheckpointId = Column(Integer, nullable=False, default=0)
    SpentStars = Column(Integer, nullable=False)
    CreatedAt = Column(DateTime, nullable=False, default=datetime.utcnow)
    UsedAt = Column(DateTime)

class StudentTestcaseInputPurchases(db.Model):
    """Testcase inputs purchased by a student."""

    __tablename__ = "StudentTestcaseInputPurchases"

    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    CheckpointId = Column(Integer, primary_key=True, default=0)
    TestcaseId = Column(
        Integer,
        ForeignKey("Testcases.Id", ondelete="CASCADE"),
        primary_key=True,
    )
    SpentStars = Column(Integer, nullable=False)
    CreatedAt = Column(DateTime, nullable=False, default=datetime.utcnow)

class StudentStarAwards(db.Model):
    """
    Stars awarded for completing a main assignment or checkpoint.

    A student's current balance is derived from these awards minus the
    star-purchase tables. No separate balance or generic spending row is stored.
    """

    __tablename__ = "StudentStarAwards"

    Id = Column(Integer, primary_key=True, autoincrement=True)
    UserId = Column(
        Integer,
        ForeignKey("Users.Id", ondelete="CASCADE"),
        nullable=False,
    )
    ClassId = Column(
        Integer,
        ForeignKey("Classes.Id", ondelete="CASCADE"),
        nullable=False,
    )
    ProjectId = Column(
        Integer,
        ForeignKey("Assignments.Id", ondelete="CASCADE"),
        nullable=False,
    )
    # Main-assignment awards use 0.
    CheckpointId = Column(Integer, nullable=False, default=0)
    SubmissionId = Column(
        Integer,
        ForeignKey("Submissions.Id", ondelete="SET NULL"),
    )
    AwardType = Column(String(40), nullable=False)
    AwardedStars = Column(Integer, nullable=False)
    BaseAwardStars = Column(Integer, nullable=False)
    Multiplier = Column(Integer, nullable=False, default=1)
    StartedEarly = Column(Boolean, nullable=False, default=False)
    AwardedAt = Column(DateTime, nullable=False, default=datetime.utcnow)

class SubmissionAnnotations(db.Model):
    """Line-level manual grading annotations attached to a submission."""

    __tablename__ = "SubmissionAnnotations"

    Id = Column(Integer, primary_key=True, autoincrement=True)
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


class DefaultContentImports(db.Model):
    __tablename__ = "DefaultContentImports"
    ClassId = Column(Integer, ForeignKey("Classes.Id"), primary_key=True)
    SourceKey = Column(String(700, collation="utf8mb4_bin"), primary_key=True)
    ModuleId = Column(Integer, ForeignKey("Modules.Id"), nullable=False)
    AssignmentId = Column(Integer, ForeignKey("Assignments.Id"), nullable=False)
