SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `OfficeHoursQueueEntries`;
DROP TABLE IF EXISTS `OfficeHoursSessions`;
DROP TABLE IF EXISTS `SubmissionAnnotations`;
DROP TABLE IF EXISTS `StudentStarAwards`;
DROP TABLE IF EXISTS `StudentTestcaseInputPurchases`;
DROP TABLE IF EXISTS `StudentCooldownSkips`;
DROP TABLE IF EXISTS `StudentCheckpointSkips`;
DROP TABLE IF EXISTS `StudentUploadStates`;
DROP TABLE IF EXISTS `StudentHiddenModules`;
DROP TABLE IF EXISTS `StudentSuggestions`;
DROP TABLE IF EXISTS `LoginAttempts`;

DROP TABLE IF EXISTS `Grades`;
DROP TABLE IF EXISTS `Testcases`;
DROP TABLE IF EXISTS `Submissions`;
DROP TABLE IF EXISTS `ClassAssignments`;
DROP TABLE IF EXISTS `DefaultContentImports`;
DROP TABLE IF EXISTS `Assignments`;
DROP TABLE IF EXISTS `Modules`;
DROP TABLE IF EXISTS `LectureSections`;
DROP TABLE IF EXISTS `Labs`;
DROP TABLE IF EXISTS `Classes`;
DROP TABLE IF EXISTS `Users`;
DROP TABLE IF EXISTS `Schools`;

CREATE TABLE `Schools` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `Name` varchar(255) NOT NULL,
  `AuthProvider` varchar(20) NOT NULL,
  `RequiresLabAndLecture` tinyint(1) NOT NULL DEFAULT 1,
  `UseDefaultMaterials` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`Id`),
  UNIQUE KEY `uq_schools_name` (`Name`),
  CONSTRAINT `ck_schools_auth_provider`
    CHECK (`AuthProvider` IN ('google', 'microsoft'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Users` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `Username` varchar(255) NOT NULL,
  `Firstname` varchar(255) NOT NULL,
  `Lastname` varchar(255) NOT NULL,
  `Email` varchar(320) NOT NULL,
  `StudentNumber` varchar(255) NOT NULL,
  `IsLocked` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`Id`),
  UNIQUE KEY `uq_users_username` (`Username`),
  KEY `idx_users_student_number` (`StudentNumber`),
  KEY `idx_users_email` (`Email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Classes` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `Name` varchar(255) NOT NULL,
  `SchoolId` int NOT NULL,
  `DefaultContentInitialized` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`Id`),
  UNIQUE KEY `uq_classes_school_name` (`SchoolId`, `Name`),
  KEY `idx_classes_school` (`SchoolId`),
  CONSTRAINT `fk_classes_school`
    FOREIGN KEY (`SchoolId`) REFERENCES `Schools` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Labs` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `Name` varchar(255) NOT NULL,
  `ClassId` int NOT NULL,
  PRIMARY KEY (`Id`),
  UNIQUE KEY `uq_labs_class_name` (`ClassId`, `Name`),
  KEY `idx_labs_class` (`ClassId`),
  CONSTRAINT `fk_labs_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `LectureSections` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `Name` varchar(255) NOT NULL,
  `ClassId` int NOT NULL,
  PRIMARY KEY (`Id`),
  UNIQUE KEY `uq_lecture_sections_class_name` (`ClassId`, `Name`),
  KEY `idx_lecture_sections_class` (`ClassId`),
  CONSTRAINT `fk_lecture_sections_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Modules` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `ClassId` int NOT NULL,
  `Name` varchar(1000) NOT NULL,
  `FirstName` varchar(1000) DEFAULT NULL,
  `FileTimestamp` varchar(32) DEFAULT NULL,
  `Start` datetime NOT NULL,
  `End` datetime NOT NULL,
  PRIMARY KEY (`Id`),
  KEY `idx_modules_class` (`ClassId`),
  CONSTRAINT `fk_modules_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Assignments` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `AssignmentType` varchar(20) NOT NULL,
  `ParentAssignmentId` int DEFAULT NULL,
  `ClassId` int DEFAULT NULL,
  `ModuleId` int DEFAULT NULL,
  `CheckpointNumber` int DEFAULT NULL,
  `Enabled` tinyint(1) NOT NULL DEFAULT 1,
  `Name` varchar(1000) NOT NULL,
  `FirstName` varchar(1000) DEFAULT NULL,
  `Language` varchar(45) DEFAULT NULL,
  `solutionpath` varchar(1000) DEFAULT NULL,
  `AsnDescriptionPath` varchar(1000) DEFAULT NULL,
  `AdditionalFilePath` longtext,
  `CreatedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`Id`),
  UNIQUE KEY `uq_assignments_parent_checkpoint_number`
    (`ParentAssignmentId`, `CheckpointNumber`),
  KEY `idx_assignments_type_class` (`AssignmentType`, `ClassId`),
  KEY `idx_assignments_module` (`ModuleId`),
  KEY `idx_assignments_parent` (`ParentAssignmentId`),
  CONSTRAINT `fk_assignments_parent`
    FOREIGN KEY (`ParentAssignmentId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_assignments_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_assignments_module`
    FOREIGN KEY (`ModuleId`) REFERENCES `Modules` (`Id`)
    ON DELETE SET NULL,
  CONSTRAINT `ck_assignments_type_parent` CHECK (
    (`AssignmentType` = 'project'
      AND `ParentAssignmentId` IS NULL
      AND `ClassId` IS NOT NULL)
    OR
    (`AssignmentType` = 'checkpoint'
      AND `ParentAssignmentId` IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `ClassAssignments` (
  `UserId` int NOT NULL,
  `ClassId` int NOT NULL,
  `LabId` int DEFAULT NULL,
  `LectureId` int DEFAULT NULL,
  `Role` int NOT NULL DEFAULT 0,
  PRIMARY KEY (`UserId`, `ClassId`),
  KEY `idx_class_assignments_class` (`ClassId`),
  KEY `idx_class_assignments_lab` (`LabId`),
  KEY `idx_class_assignments_lecture` (`LectureId`),
  CONSTRAINT `fk_class_assignments_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_class_assignments_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_class_assignments_lab`
    FOREIGN KEY (`LabId`) REFERENCES `Labs` (`Id`),
  CONSTRAINT `fk_class_assignments_lecture`
    FOREIGN KEY (`LectureId`) REFERENCES `LectureSections` (`Id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `OfficeHoursSessions` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `ClassId` int NOT NULL,
  `StartedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `EndsAt` datetime NOT NULL,
  `StartedByUserId` int DEFAULT NULL,
  PRIMARY KEY (`Id`),
  KEY `idx_office_hours_sessions_class_time` (`ClassId`, `StartedAt`, `EndsAt`),
  KEY `idx_office_hours_sessions_started_by` (`StartedByUserId`),
  CONSTRAINT `fk_office_hours_sessions_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_office_hours_sessions_started_by`
    FOREIGN KEY (`StartedByUserId`) REFERENCES `Users` (`Id`)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `OfficeHoursQueueEntries` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `UserId` int NOT NULL,
  `ClassId` int NOT NULL,
  `ModuleId` int NOT NULL,
  `JoinedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `SelectedAt` datetime DEFAULT NULL,
  `SelectedByUserId` int DEFAULT NULL,
  `CooldownExemptUntil` datetime DEFAULT NULL,
  `CompletedAt` datetime DEFAULT NULL,
  PRIMARY KEY (`Id`),
  KEY `idx_office_hours_student_active`
    (`UserId`, `ClassId`, `CompletedAt`, `JoinedAt`),
  KEY `idx_office_hours_class_queue`
    (`ClassId`, `CompletedAt`, `SelectedAt`, `JoinedAt`),
  KEY `idx_office_hours_module` (`ModuleId`),
  KEY `idx_office_hours_exemption`
    (`UserId`, `ClassId`, `ModuleId`, `CompletedAt`, `CooldownExemptUntil`),
  KEY `idx_office_hours_selected_by` (`SelectedByUserId`),
  CONSTRAINT `fk_office_hours_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_office_hours_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_office_hours_module`
    FOREIGN KEY (`ModuleId`) REFERENCES `Modules` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_office_hours_selected_by`
    FOREIGN KEY (`SelectedByUserId`) REFERENCES `Users` (`Id`)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Submissions` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `OutputFilepath` varchar(1000) NOT NULL,
  `CodeFilepath` varchar(1000) NOT NULL,
  `IsPassing` tinyint(1) NOT NULL DEFAULT 0,
  `IsCheckpoint` tinyint(1) NOT NULL DEFAULT 0,
  `CheckpointId` int DEFAULT NULL,
  `SubmissionMethod` varchar(20) NOT NULL DEFAULT 'unknown',
  `Time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `User` int NOT NULL,
  `Project` int NOT NULL,
  `TestCaseResults` longtext,
  PRIMARY KEY (`Id`),
  KEY `idx_submissions_user_project_time` (`User`, `Project`, `Time`),
  KEY `idx_submissions_project` (`Project`),
  KEY `idx_submissions_checkpoint` (`Project`, `CheckpointId`, `IsCheckpoint`),
  CONSTRAINT `fk_submissions_user`
    FOREIGN KEY (`User`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_submissions_project`
    FOREIGN KEY (`Project`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_submissions_checkpoint`
    FOREIGN KEY (`CheckpointId`) REFERENCES `Assignments` (`Id`)
    ON DELETE SET NULL,
  CONSTRAINT `ck_submissions_submission_method`
    CHECK (`SubmissionMethod` IN ('upload', 'editor', 'unknown'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Testcases` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `ProjectId` int NOT NULL,
  `CheckpointId` int DEFAULT NULL,
  `Name` longtext,
  `input` longtext,
  `Output` longtext,
  `Hidden` tinyint(1) NOT NULL DEFAULT 0,
  `SortOrder` int NOT NULL DEFAULT 0,
  `Checkpoint` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`Id`),
  KEY `idx_testcases_project_checkpoint_order`
    (`ProjectId`, `CheckpointId`, `SortOrder`, `Id`),
  CONSTRAINT `fk_testcases_project`
    FOREIGN KEY (`ProjectId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_testcases_checkpoint`
    FOREIGN KEY (`CheckpointId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `Grades` (
  `SubmissionId` int NOT NULL,
  `GradeScope` varchar(20) NOT NULL,
  `UserId` int NOT NULL,
  `ProjectId` int NOT NULL,
  `Grade` int NOT NULL DEFAULT 0,
  `ScoringMode` varchar(20) DEFAULT NULL,
  `ErrorPointsJson` longtext,
  `ErrorDefsJson` longtext,
  `UpdatedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`SubmissionId`),
  KEY `idx_grades_project_user` (`ProjectId`, `UserId`),
  KEY `idx_grades_scope_project` (`GradeScope`, `ProjectId`),
  CONSTRAINT `fk_grades_submission`
    FOREIGN KEY (`SubmissionId`) REFERENCES `Submissions` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_grades_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_grades_project`
    FOREIGN KEY (`ProjectId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `ck_grades_scope`
    CHECK (`GradeScope` IN ('main', 'checkpoint'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `LoginAttempts` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `Username` varchar(255) NOT NULL,
  `IPAddress` varchar(64) NOT NULL DEFAULT '',
  `AttemptedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`Id`),
  KEY `idx_login_attempts_username` (`Username`),
  KEY `idx_login_attempts_attempted_at` (`AttemptedAt`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `StudentSuggestions` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `UserId` int NOT NULL,
  `Suggestion` longtext NOT NULL,
  `SubmittedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`Id`),
  KEY `idx_student_suggestions_user` (`UserId`),
  KEY `idx_student_suggestions_submitted_at` (`SubmittedAt`),
  CONSTRAINT `fk_student_suggestions_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `StudentHiddenModules` (
  `UserId` int NOT NULL,
  `ModuleId` int NOT NULL,
  `CreatedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`UserId`, `ModuleId`),
  KEY `idx_student_hidden_modules_module` (`ModuleId`),
  CONSTRAINT `fk_student_hidden_modules_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_hidden_modules_module`
    FOREIGN KEY (`ModuleId`) REFERENCES `Modules` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `StudentUploadStates` (
  `UserId` int NOT NULL,
  `ClassId` int NOT NULL,
  `ProjectId` int NOT NULL,
  `CheckpointId` int NOT NULL DEFAULT 0,
  `CooldownLiftedAt` datetime DEFAULT NULL,
  `UpdatedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`UserId`, `ClassId`, `ProjectId`, `CheckpointId`),
  KEY `idx_student_upload_states_project` (`ProjectId`),
  KEY `idx_student_upload_states_class` (`ClassId`),
  CONSTRAINT `fk_student_upload_states_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_upload_states_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_upload_states_project`
    FOREIGN KEY (`ProjectId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `StudentCheckpointSkips` (
  `UserId` int NOT NULL,
  `ClassId` int NOT NULL,
  `ProjectId` int NOT NULL,
  `CheckpointId` int NOT NULL,
  `SpentStars` int NOT NULL,
  `CreatedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`UserId`, `ClassId`, `ProjectId`, `CheckpointId`),
  KEY `idx_student_checkpoint_skips_user_class` (`UserId`, `ClassId`),
  KEY `idx_student_checkpoint_skips_checkpoint` (`CheckpointId`),
  CONSTRAINT `fk_student_checkpoint_skips_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_checkpoint_skips_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_checkpoint_skips_project`
    FOREIGN KEY (`ProjectId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_checkpoint_skips_checkpoint`
    FOREIGN KEY (`CheckpointId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `StudentCooldownSkips` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `UserId` int NOT NULL,
  `ClassId` int NOT NULL,
  `ProjectId` int NOT NULL,
  `CheckpointId` int NOT NULL DEFAULT 0,
  `SpentStars` int NOT NULL,
  `CreatedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `UsedAt` datetime DEFAULT NULL,
  PRIMARY KEY (`Id`),
  KEY `idx_student_cooldown_skips_available`
    (`UserId`, `ClassId`, `ProjectId`, `CheckpointId`, `UsedAt`),
  KEY `idx_student_cooldown_skips_created` (`CreatedAt`),
  CONSTRAINT `fk_student_cooldown_skips_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_cooldown_skips_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_cooldown_skips_project`
    FOREIGN KEY (`ProjectId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `StudentTestcaseInputPurchases` (
  `UserId` int NOT NULL,
  `ClassId` int NOT NULL,
  `ProjectId` int NOT NULL,
  `CheckpointId` int NOT NULL DEFAULT 0,
  `TestcaseId` int NOT NULL,
  `SpentStars` int NOT NULL,
  `CreatedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`UserId`, `ClassId`, `ProjectId`, `CheckpointId`, `TestcaseId`),
  KEY `idx_student_testcase_input_purchases_user_class`
    (`UserId`, `ClassId`),
  KEY `idx_student_testcase_input_purchases_testcase` (`TestcaseId`),
  CONSTRAINT `fk_student_testcase_input_purchases_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_testcase_input_purchases_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_testcase_input_purchases_project`
    FOREIGN KEY (`ProjectId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_testcase_input_purchases_testcase`
    FOREIGN KEY (`TestcaseId`) REFERENCES `Testcases` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `StudentStarAwards` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `UserId` int NOT NULL,
  `ClassId` int NOT NULL,
  `ProjectId` int NOT NULL,
  `CheckpointId` int NOT NULL DEFAULT 0,
  `SubmissionId` int DEFAULT NULL,
  `AwardType` varchar(40) NOT NULL,
  `AwardedStars` int NOT NULL,
  `BaseAwardStars` int NOT NULL,
  `Multiplier` int NOT NULL DEFAULT 1,
  `StartedEarly` tinyint(1) NOT NULL DEFAULT 0,
  `AwardedAt` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`Id`),
  UNIQUE KEY `uq_student_star_awards_scope`
    (`UserId`, `ClassId`, `ProjectId`, `CheckpointId`, `AwardType`),
  KEY `idx_student_star_awards_project` (`ProjectId`),
  KEY `idx_student_star_awards_submission` (`SubmissionId`),
  CONSTRAINT `fk_student_star_awards_user`
    FOREIGN KEY (`UserId`) REFERENCES `Users` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_star_awards_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_star_awards_project`
    FOREIGN KEY (`ProjectId`) REFERENCES `Assignments` (`Id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_student_star_awards_submission`
    FOREIGN KEY (`SubmissionId`) REFERENCES `Submissions` (`Id`)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `SubmissionAnnotations` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `SubmissionId` int NOT NULL,
  `StartLine` int NOT NULL,
  `EndLine` int NOT NULL,
  `ErrorId` varchar(80) NOT NULL,
  `Count` int NOT NULL DEFAULT 1,
  `Note` longtext,
  PRIMARY KEY (`Id`),
  KEY `idx_submission_annotations_submission` (`SubmissionId`),
  CONSTRAINT `fk_submission_annotations_submission`
    FOREIGN KEY (`SubmissionId`) REFERENCES `Submissions` (`Id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `DefaultContentImports` (
  `ClassId` int NOT NULL,
  `SourceKey` varchar(700) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `ModuleId` int NOT NULL,
  `AssignmentId` int NOT NULL,
  PRIMARY KEY (`ClassId`, `SourceKey`),
  KEY `idx_default_content_imports_module` (`ModuleId`),
  KEY `idx_default_content_imports_assignment` (`AssignmentId`),
  CONSTRAINT `fk_default_content_imports_class`
    FOREIGN KEY (`ClassId`) REFERENCES `Classes` (`Id`),
  CONSTRAINT `fk_default_content_imports_module`
    FOREIGN KEY (`ModuleId`) REFERENCES `Modules` (`Id`),
  CONSTRAINT `fk_default_content_imports_assignment`
    FOREIGN KEY (`AssignmentId`) REFERENCES `Assignments` (`Id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO `Schools` (`Id`, `Name`, `AuthProvider`, `RequiresLabAndLecture`)
VALUES
  (1, 'Marquette University', 'microsoft', 1),
  (2, 'MPS Training', 'google', 0);

INSERT INTO `Classes` (`Id`, `Name`, `SchoolId`)
VALUES
  (1, 'COSC 1010', 1),
  (2, '2026-2027', 2);

INSERT INTO `Labs` (`Id`, `Name`, `ClassId`)
VALUES (1, '401', 1);

INSERT INTO `LectureSections` (`Id`, `Name`, `ClassId`)
VALUES (1, '101', 1);

SET FOREIGN_KEY_CHECKS = 1;