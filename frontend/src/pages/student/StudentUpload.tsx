import React, { CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import MenuComponent from "../components/MenuComponent";
import StarSpendingInfo from "../components/StarSpendingInfo";
import ErrorMessage from "../components/ErrorMessage";
import LoadingAnimation from "../components/LoadingAnimation";
import { Helmet } from "react-helmet";
import { useLocation, useParams } from "react-router-dom";
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs";
import DiffView from "../components/CodeDiffView";
import PythonIDE, {
  PythonIdeRunRequest,
  PythonIdeRunResult,
} from "../components/PythonIDE";
import "../../styling/StudentUpload.scss";
import "../../styling/FileUploadCommon.scss";

import {
  FaAlignJustify,
  FaArrowRight,
  FaBan,
  FaBolt,
  FaCloudUploadAlt,
  FaCompressAlt,
  FaCode,
  FaClock,
  FaExchangeAlt,
  FaExpandAlt,
  FaExternalLinkAlt,
  FaFileWord,
  FaRegFile,
  FaTimesCircle,
  FaCheckCircle,
  FaEye,
  FaFilePowerpoint,
  FaForward,
  FaInfoCircle,
  FaLock,
  FaStar,
  FaUsers,
} from "react-icons/fa";

type CheckpointLite = {
  id: number;
  number?: number;
  name?: string;
  enabled?: boolean;
  solved?: boolean;
  rewarded?: boolean;
};

type AssignedClassLite = {
  id: number;
  school_id?: number;
};

type ModuleObjectLite = {
  Id: number;
  ClassId: number;
  Name: string;
  Start: string;
  End: string;
  MainProjectId?: number;
  MainProjectName?: string;
  HasPresentation?: boolean;
  PresentationFileName?: string;
};

type PastSubmissionMain = {
  submissionId?: number;
  submission_id?: number;
  id?: number;
  Id?: number;
  passed?: boolean;
  time?: string;
};

type PastSubmissionCheckpoint = {
  checkpointId?: number;
  checkpoint_id?: number;
  practiceProblemId?: number;
  practice_problem_id?: number;
  id?: number;
  Id?: number;
  number?: number;
  name?: string;
  submissionId?: number;
  submission_id?: number;
  passed?: boolean;
  time?: string;
};

type ApiPastSubmissionsProject = {
  projectId?: number;
  project_id?: number;
  id?: number;
  Id?: number;
  projectName?: string;
  project_name?: string;
  main?: PastSubmissionMain | null;
  checkpoints?: PastSubmissionCheckpoint[];
  practices?: PastSubmissionCheckpoint[];
};

type LatestPastSubmission = {
  submissionId: number | string;
  passed: boolean;
};

type IncentiveState = {
  stars?: number;
  star_balance?: number;
  cooldown_skip_cost?: number;
  checkpoint_cooldown_skip_cost?: number;
  main_project_cooldown_skip_cost?: number;
  submission_cooldown_seconds?: number;
  cooldown_remaining_seconds?: number;
  office_hours_cooldown_exempt?: boolean;
  office_hours_cooldown_exempt_until?: string | null;
  submission_attempt_count?: number;
  next_attempt_number?: number;
  checkpoint_completion_stars?: number;
  main_project_completion_stars?: number;
  early_start_multiplier?: number;
  early_start_deadline?: string | null;
  early_start_remaining_seconds?: number;
  early_start_window_open?: boolean;
  reward_base_stars?: number;
  reward_multiplier?: number;
  reward_total_stars?: number;
  reward_already_awarded?: boolean;
  reward_started_early?: boolean;
};

type OfficeHoursStatus = {
  status?: "not_queued" | "waiting" | "being_helped";
  office_hours_active?: boolean;
  session_ends_at?: string | null;
  cooldown_exempt?: boolean;
  cooldown_exempt_until?: string | null;
  help_remaining_seconds?: number;
  queue_position?: number | null;
};

type SubmissionMethod = "editor" | "upload";

type StudentUploadSection = "instructions" | "submission" | "testcases";

type StudentUploadProps = {
  initialSection?: StudentUploadSection;
};

type AssignmentPreview = {
  url: string;
  contentType: string;
  filename: string;
};

const CHECKPOINT_SCHEDULE = [
  { attempt: 1, label: "Attempt 1", value: "No wait" },
  { attempt: 2, label: "Attempt 2", value: "1 minute" },
  { attempt: 3, label: "Attempt 3", value: "2 minutes" },
  { attempt: 4, label: "Attempt 4+", value: "5 minutes" },
];

const MAIN_SCHEDULE = [
  { attempt: 1, label: "Attempt 1", value: "No wait" },
  { attempt: 2, label: "Attempt 2", value: "2 minutes" },
  { attempt: 3, label: "Attempt 3", value: "5 minutes" },
  { attempt: 4, label: "Attempt 4", value: "10 minutes" },
  { attempt: 5, label: "Attempt 5+", value: "20 minutes" },
];

const authHeader = () => ({
  Authorization: `Bearer ${localStorage.getItem("AUTOTA_AUTH_TOKEN")}`,
});

const parsePositiveInt = (value: string | undefined): number | null => {
  if (value === undefined || !/^\d+$/.test(value)) return null;

  const parsed = parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

const normalizeMaybeJson = <T,>(item: unknown): T => {
  if (typeof item === "string") {
    return JSON.parse(item) as T;
  }

  return item as T;
};

const normalizePositiveSubmissionId = (
  value: number | string | null | undefined,
): number | string | null => {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  const n = Number(value);
  if (Number.isFinite(n) && n > 0) {
    return Number.isInteger(n) ? n : String(value);
  }

  return null;
};

const getPayloadSubmissionId = (payload: any): number | string | null => {
  const candidates = [
    payload?.submission_id,
    payload?.SubmissionId,
    payload?.submissionId,
    payload?.sid,
    payload?.Sid,
    payload?.id,
    payload?.Id,
    payload?.latest_submission_id,
    payload?.LatestSubmissionId,
    payload?.submission?.id,
    payload?.submission?.Id,
  ];

  const found = candidates.find(
    (candidate) => candidate !== undefined && candidate !== null && candidate !== "",
  );

  return normalizePositiveSubmissionId(found);
};

const isPassedValue = (value: unknown): boolean => {
  if (value === true || value === 1) return true;
  if (typeof value !== "string") return false;

  return ["true", "1", "pass", "passed", "ok"].includes(
    value.trim().toLowerCase(),
  );
};

const isPassedTestcase = (result: any): boolean => {
  return isPassedValue(
    result?.passed ??
    result?.ok ??
    result?.State ??
    result?.state ??
    result?.status,
  );
};

const getPastProjectId = (row: ApiPastSubmissionsProject): number => {
  return Number(row?.projectId ?? row?.project_id ?? row?.id ?? row?.Id ?? 0);
};

const getPastMainSubmission = (
  row: ApiPastSubmissionsProject,
): LatestPastSubmission | null => {
  const main = row?.main;
  if (!main) return null;

  const submissionId = normalizePositiveSubmissionId(
    main.submissionId ?? main.submission_id ?? main.id ?? main.Id,
  );

  return submissionId === null
    ? null
    : {
      submissionId,
      passed: isPassedValue(main.passed),
    };
};

const getPastCheckpointId = (row: PastSubmissionCheckpoint): number => {
  return Number(
    row?.checkpointId ??
    row?.checkpoint_id ??
    row?.practiceProblemId ??
    row?.practice_problem_id ??
    row?.id ??
    row?.Id ??
    0,
  );
};

const getPastCheckpointSubmission = (
  row: PastSubmissionCheckpoint,
): LatestPastSubmission | null => {
  const submissionId = normalizePositiveSubmissionId(
    row?.submissionId ?? row?.submission_id,
  );

  return submissionId === null
    ? null
    : {
      submissionId,
      passed: isPassedValue(row?.passed),
    };
};

const submissionIdAsNumber = (
  value: number | string | null | undefined,
): number | null => {
  const normalized = normalizePositiveSubmissionId(value);
  if (normalized === null) return null;

  const parsed = Number(normalized);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
};

const StudentUpload = ({ initialSection }: StudentUploadProps = {}) => {
  const {
    id,
    school_id,
    class_id,
    module_id,
    project_id: route_project_id,
    checkpoint_id,
  } = useParams<{
    id?: string;
    school_id?: string;
    class_id?: string;
    module_id?: string;
    project_id?: string;
    checkpoint_id?: string;
  }>();

  const location = useLocation();
  const cid = parsePositiveInt(class_id) ?? -1;
  const schoolId = parsePositiveInt(school_id);
  const moduleId = parsePositiveInt(module_id);
  const routeProjectId = parsePositiveInt(route_project_id);
  const routeSubmissionId = parsePositiveInt(id);

  const checkpointId = parsePositiveInt(checkpoint_id);
  const isCheckpoint = checkpointId !== null;

  const hasModuleRoute =
    schoolId !== null && moduleId !== null && routeProjectId !== null;
  const isAdminPreview = location.pathname.includes("/student-preview");
  const adminPreviewBasePath =
    schoolId !== null
      ? `/admin/school/${schoolId}/class/${cid}/student-preview`
      : "";

  const [files, setFiles] = useState<File[]>([]);
  const [mainJavaFileName, setMainJavaFileName] = useState<string>("");
  const [submissionMethod, setSubmissionMethod] =
    useState<SubmissionMethod>("upload");
  const [pythonIdeEnabled, setPythonIdeEnabled] = useState<boolean>(false);
  const [pythonFilename, setPythonFilename] = useState<string>("main.py");
  const [pythonSource, setPythonSource] = useState<string>(
    "# Write your Python program here\n\n",
  );

  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error_message, setError_Message] = useState<string>("");
  const [isErrorMessageHidden, setIsErrorMessageHidden] =
    useState<boolean>(true);

  const [project_id, setProject_id] = useState<number>(routeProjectId ?? 0);
  const is_allowed_to_submit = !isAdminPreview;

  const [suggestions, setSuggestions] = useState<string>("");
  const feedbackRef = useRef<HTMLTextAreaElement | null>(null);

  const [project_name, setProject_name] = useState<string>("");
  const [dueDate, setDueDate] = useState<string>("");

  const [passedAllTests, setPassedAllTests] = useState<boolean>(false);
  const [checkedPassedAll, setCheckedPassedAll] = useState<boolean>(false);
  const [testcasesPassedCount, setTestcasesPassedCount] = useState<number>(0);
  const [testcasesTotalCount, setTestcasesTotalCount] = useState<number>(0);
  const [previousSubmissionId, setPreviousSubmissionId] = useState<number | string | null>(null);
  const [cooldownLiftedAtMs, setCooldownLiftedAtMs] = useState<number>(0);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [incentives, setIncentives] = useState<IncentiveState | null>(null);
  const [isSkippingCooldown, setIsSkippingCooldown] = useState<boolean>(false);
  const [isSkipConfirmationOpen, setIsSkipConfirmationOpen] =
    useState<boolean>(false);
  const [isCooldownStateLoading, setIsCooldownStateLoading] = useState<boolean>(true);

  const [moduleName, setModuleName] = useState<string>("");
  const [officeHoursModuleId, setOfficeHoursModuleId] = useState<number | null>(moduleId);
  const [officeHoursStatus, setOfficeHoursStatus] =
    useState<OfficeHoursStatus | null>(null);
  const [hasModulePresentation, setHasModulePresentation] =
    useState<boolean>(false);
  const [modulePresentationFileName, setModulePresentationFileName] =
    useState<string>("");
  const [checkpointLabel, setCheckpointLabel] = useState<string>("");
  const [hideClassSelectionCrumb, setHideClassSelectionCrumb] =
    useState<boolean>(false);
  const [assignmentPreview, setAssignmentPreview] =
    useState<AssignmentPreview | null>(null);
  const [isAssignmentPreviewLoading, setIsAssignmentPreviewLoading] =
    useState<boolean>(false);
  const [assignmentPreviewError, setAssignmentPreviewError] =
    useState<string>("");
  const [presentationPreview, setPresentationPreview] =
    useState<AssignmentPreview | null>(null);
  const [isPresentationPreviewLoading, setIsPresentationPreviewLoading] =
    useState<boolean>(false);
  const [presentationPreviewError, setPresentationPreviewError] =
    useState<string>("");
  const [activeWorkspaceSection, setActiveWorkspaceSection] =
    useState<StudentUploadSection>("instructions");
  const [isAssignmentPreviewExpanded, setIsAssignmentPreviewExpanded] =
    useState<boolean>(false);
  const [isPresentationPreviewExpanded, setIsPresentationPreviewExpanded] =
    useState<boolean>(false);
  const [activeSubmissionId, setActiveSubmissionId] =
    useState<number | null>(routeSubmissionId);
  const resultsRef = useRef<HTMLElement | null>(null);
  const initialSectionAppliedRef = useRef<boolean>(false);

  const scrollToSection = useCallback((section: StudentUploadSection) => {
    setActiveWorkspaceSection(section);
    window.requestAnimationFrame(() => {
      document.getElementById("student-workspace")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }, []);

  const openAssignmentPreview = useCallback(() => {
    if (!assignmentPreview?.url) return;

    const previewWindow = window.open(
      assignmentPreview.url,
      "_blank",
      "noopener,noreferrer",
    );

    if (previewWindow) previewWindow.opener = null;
  }, [assignmentPreview]);

  const openPresentationPreview = useCallback(() => {
    if (!presentationPreview?.url) return;

    const previewWindow = window.open(
      presentationPreview.url,
      "_blank",
      "noopener,noreferrer",
    );

    if (previewWindow) previewWindow.opener = null;
  }, [presentationPreview]);

  const showSubmissionResults = useCallback(
    (submissionId: number | string | null | undefined) => {
      const nextSubmissionId = submissionIdAsNumber(submissionId);
      if (nextSubmissionId === null) return;

      setActiveSubmissionId(nextSubmissionId);
      window.requestAnimationFrame(() => scrollToSection("testcases"));
    },
    [scrollToSection],
  );

  const autoGrowTextarea = (el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };

  const loadModulePresentationStatus = useCallback(
    (targetProjectId: number) => {
      if (!Number.isFinite(cid) || cid <= 0 || targetProjectId <= 0) {
        setHasModulePresentation(false);
        setModulePresentationFileName("");
        setOfficeHoursModuleId(moduleId);
        return;
      }

      setHasModulePresentation(false);
      setModulePresentationFileName("");

      axios
        .get(
          `${import.meta.env.VITE_API_URL}/projects/get_modules_by_class_id_student?id=${cid}`,
          {
            headers: authHeader(),
          },
        )
        .then((res) => {
          const modules: ModuleObjectLite[] = Array.isArray(res.data)
            ? res.data.map((item: unknown) =>
              normalizeMaybeJson<ModuleObjectLite>(item),
            )
            : [];
          const selectedModule =
            (moduleId
              ? modules.find((item) => Number(item.Id) === Number(moduleId))
              : null) ||
            modules.find(
              (item) => Number(item.MainProjectId) === Number(targetProjectId),
            ) ||
            null;

          setModuleName(selectedModule?.Name || "");
          if (
            isAdminPreview &&
            Number(selectedModule?.MainProjectId || 0) === Number(targetProjectId) &&
            selectedModule?.MainProjectName
          ) {
            setProject_name(selectedModule.MainProjectName);
          }
          setOfficeHoursModuleId(
            selectedModule?.Id ? Number(selectedModule.Id) : moduleId,
          );
          setHasModulePresentation(Boolean(selectedModule?.HasPresentation));
          setModulePresentationFileName(
            selectedModule?.PresentationFileName || "",
          );
        })
        .catch(() => {
          setHasModulePresentation(false);
          setModulePresentationFileName("");
          if (hasModuleRoute) {
            setModuleName("");
          }
        });
    },
    [cid, hasModuleRoute, moduleId, isAdminPreview],
  );

  const testcaseProgress = useMemo(() => {
    const total = Math.max(0, testcasesTotalCount);
    const passed = Math.max(0, Math.min(testcasesPassedCount, total));
    const failed = Math.max(0, total - passed);
    const pct = total === 0 ? 0 : Math.round((passed / total) * 100);
    return { total, passed, failed, pct };
  }, [testcasesPassedCount, testcasesTotalCount]);

  const hasNonPassingSubmission =
    checkedPassedAll &&
    testcaseProgress.total > 0 &&
    !passedAllTests &&
    (activeSubmissionId !== null || previousSubmissionId !== null);

  const cooldownRemainingSeconds = useMemo(() => {
    return Math.max(0, Math.ceil((cooldownLiftedAtMs - nowMs) / 1000));
  }, [cooldownLiftedAtMs, nowMs]);

  const officeHoursExemptUntil =
    officeHoursStatus?.cooldown_exempt_until ??
    incentives?.office_hours_cooldown_exempt_until;
  const officeHoursExemptUntilMs = officeHoursExemptUntil
    ? Date.parse(officeHoursExemptUntil)
    : 0;
  const officeHoursRemainingSeconds =
    Number.isFinite(officeHoursExemptUntilMs) && officeHoursExemptUntilMs > 0
      ? Math.max(0, Math.ceil((officeHoursExemptUntilMs - nowMs) / 1000))
      : Math.max(0, Number(officeHoursStatus?.help_remaining_seconds ?? 0));
  const isOfficeHoursExempt =
    Boolean(
      officeHoursStatus?.cooldown_exempt ||
      incentives?.office_hours_cooldown_exempt,
    ) &&
    officeHoursRemainingSeconds > 0;
  const officeHoursCooldownDisabled = isOfficeHoursExempt && !passedAllTests;
  const isCoolingDown = cooldownRemainingSeconds > 0 && !isOfficeHoursExempt;
  const officeHoursSessionEndsMs = officeHoursStatus?.session_ends_at
    ? Date.parse(officeHoursStatus.session_ends_at)
    : 0;
  const isOfficeHoursSessionActive =
    Boolean(officeHoursStatus?.office_hours_active) &&
    (!Number.isFinite(officeHoursSessionEndsMs) ||
      officeHoursSessionEndsMs === 0 ||
      officeHoursSessionEndsMs > nowMs);
  const isWaitingForOfficeHours =
    isOfficeHoursSessionActive && officeHoursStatus?.status === "waiting";
  const showOfficeHoursAvailable =
    isOfficeHoursSessionActive && officeHoursStatus?.status === "not_queued";
  const starBalance = Number(incentives?.star_balance ?? incentives?.stars ?? 0);
  const cooldownSkipCost = Math.max(
    0,
    Number(
      incentives?.cooldown_skip_cost ??
      (isCheckpoint
        ? incentives?.checkpoint_cooldown_skip_cost ?? 1
        : incentives?.main_project_cooldown_skip_cost ?? 2),
    ),
  );
  const cooldownSkipStarLabel = cooldownSkipCost === 1 ? "star" : "stars";
  const submissionAttemptCount = Math.max(
    0,
    Number(incentives?.submission_attempt_count ?? 0),
  );
  const nextAttemptNumber = Math.max(
    1,
    Number(incentives?.next_attempt_number ?? submissionAttemptCount + 1),
  );
  const submissionTypeLabel = isCheckpoint
    ? "Checkpoint submission"
    : "Main submission";
  const submissionTypeShortLabel = isCheckpoint ? "Checkpoint" : "Main";
  const canSkipCooldown =
    !isAdminPreview && isCoolingDown && cooldownSkipCost > 0 && starBalance >= cooldownSkipCost;
  const hasSelectedProgram =
    submissionMethod === "editor"
      ? pythonIdeEnabled && Boolean(pythonSource.trim())
      : files.length > 0;
  const canSubmit =
    !isAdminPreview &&
    !passedAllTests &&
    !isCoolingDown &&
    !isCooldownStateLoading &&
    hasSelectedProgram;

  const formatCooldown = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const formatDuration = (seconds: number) => {
    const safeSeconds = Math.max(0, Math.ceil(seconds));
    const days = Math.floor(safeSeconds / 86400);
    const hours = Math.floor((safeSeconds % 86400) / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    const secs = safeSeconds % 60;

    if (days > 0) {
      return `${days}d ${hours}h ${minutes}m ${secs}s`;
    }

    if (hours > 0) {
      return `${hours}h ${minutes}m ${secs}s`;
    }

    return `${minutes}:${secs.toString().padStart(2, "0")}`;
  };

  const formatStarValue = (value: number): string => {
    if (!Number.isFinite(value)) return "0";
    return Number.isInteger(value) ? `${value}` : value.toFixed(1);
  };

  const formatStarCount = (value: number): string => {
    return `${formatStarValue(value)} ${value === 1 ? "star" : "stars"}`;
  };

  const earlyStartDeadlineMs = useMemo(() => {
    const rawDeadline = incentives?.early_start_deadline;
    if (!rawDeadline) return 0;

    const parsed = Date.parse(rawDeadline);
    return Number.isFinite(parsed) ? parsed : 0;
  }, [incentives?.early_start_deadline]);

  const earlyBonusRemainingSeconds = earlyStartDeadlineMs
    ? Math.max(0, Math.ceil((earlyStartDeadlineMs - nowMs) / 1000))
    : Math.max(0, Number(incentives?.early_start_remaining_seconds ?? 0));
  const earlyStartMultiplier = Math.max(1, Number(incentives?.early_start_multiplier ?? 2));
  const earlyBonusWindowOpen = earlyStartDeadlineMs
    ? nowMs <= earlyStartDeadlineMs
    : Boolean(incentives?.early_start_window_open && earlyBonusRemainingSeconds > 0);
  const rewardBaseStars = Math.max(
    0,
    Number(
      incentives?.reward_base_stars ??
      (isCheckpoint
        ? incentives?.checkpoint_completion_stars ?? 1
        : incentives?.main_project_completion_stars ?? 3),
    ),
  );
  const rewardAlreadyAwarded = Boolean(incentives?.reward_already_awarded || passedAllTests);
  const rewardStartedEarly = Boolean(incentives?.reward_started_early);
  const rewardMultiplier = rewardAlreadyAwarded
    ? Math.max(1, Number(incentives?.reward_multiplier ?? 1))
    : earlyBonusWindowOpen || rewardStartedEarly
      ? earlyStartMultiplier
      : 1;
  const rewardTotalStars = rewardAlreadyAwarded
    ? 0
    : rewardBaseStars * rewardMultiplier;
  const rewardIsDoubled = !rewardAlreadyAwarded && rewardMultiplier > 1;
  const rewardSummaryText = rewardAlreadyAwarded
    ? "Reward already earned for this assignment."
    : rewardIsDoubled
      ? `Earn ${formatStarValue(rewardTotalStars)} stars when all tests pass.`
      : `Earn ${formatStarValue(rewardTotalStars)} stars when all tests pass.`;
  const earlyBonusTimerText = earlyStartDeadlineMs
    ? earlyBonusWindowOpen
      ? `${formatDuration(earlyBonusRemainingSeconds)} until early bonus ends.`
      : rewardStartedEarly
        ? `${earlyStartMultiplier}x early bonus is locked in for this assignment.`
        : "Early bonus window closed."
    : "Early bonus timer unavailable.";

  const cooldownLiftedAtToMs = (
    cooldownLiftedAt: unknown,
    remainingSeconds: unknown,
  ): number => {
    if (typeof cooldownLiftedAt === "string" && cooldownLiftedAt.trim()) {
      const parsed = Date.parse(cooldownLiftedAt);
      if (Number.isFinite(parsed) && parsed > Date.now()) {
        return parsed;
      }
    }

    const fallbackSeconds = Number(remainingSeconds);
    if (Number.isFinite(fallbackSeconds) && fallbackSeconds > 0) {
      return Date.now() + Math.ceil(fallbackSeconds) * 1000;
    }

    return 0;
  };

  const buildUploadStateScope = useCallback(() => {
    if (!Number.isFinite(cid) || cid <= 0 || !project_id || project_id <= 0 || project_id === -1) {
      return null;
    }

    return {
      class_id: cid,
      project_id,
      checkpoint: Boolean(isCheckpoint),
      checkpoint_id: isCheckpoint && checkpointId ? checkpointId : null,
    };
  }, [cid, project_id, isCheckpoint, checkpointId]);

  const loadStudentUploadState = useCallback(() => {
    if (isAdminPreview) {
      setPreviousSubmissionId(null);
      setCooldownLiftedAtMs(0);
      return;
    }

    const scope = buildUploadStateScope();
    const token = localStorage.getItem("AUTOTA_AUTH_TOKEN");

    if (!scope || !token) {
      setCooldownLiftedAtMs(0);
      return;
    }

    axios
      .get(`${import.meta.env.VITE_API_URL}/submissions/student-upload-state`, {
        headers: { Authorization: `Bearer ${token}` },
        params: scope,
      })
      .then((res) => {
        const latestSubmission = normalizePositiveSubmissionId(
          res?.data?.last_submission_id ?? res?.data?.previous_submission_id,
        );
        const cooldownUntil = cooldownLiftedAtToMs(
          res?.data?.cooldown_lifted_at,
          res?.data?.cooldown_remaining_seconds,
        );

        setPreviousSubmissionId(latestSubmission);
        setCooldownLiftedAtMs(cooldownUntil);
        setIncentives((previous) => ({
          ...(previous || {}),
          submission_attempt_count: Number(res?.data?.submission_attempt_count ?? 0),
          next_attempt_number: Number(res?.data?.next_attempt_number ?? 1),
          submission_cooldown_seconds: Number(res?.data?.submission_cooldown_seconds ?? 0),
          cooldown_remaining_seconds: Number(res?.data?.cooldown_remaining_seconds ?? 0),
          office_hours_cooldown_exempt: Boolean(
            res?.data?.office_hours_cooldown_exempt,
          ),
          office_hours_cooldown_exempt_until:
            res?.data?.office_hours_cooldown_exempt_until ?? null,
        }));
      })
      .catch(() => {
        // incentive-state independently loads the authoritative cooldown timer.
      });
  }, [buildUploadStateScope, isAdminPreview]);


  const loadIncentiveState = useCallback((showLoading = false) => {
    const scope = buildUploadStateScope();
    const token = localStorage.getItem("AUTOTA_AUTH_TOKEN");

    if (!scope || !token) {
      setIncentives(null);
      setCooldownLiftedAtMs(0);
      setIsCooldownStateLoading(false);
      return;
    }

    if (showLoading) {
      setCooldownLiftedAtMs(0);
      setIsCooldownStateLoading(true);
    }

    axios
      .get(`${import.meta.env.VITE_API_URL}/submissions/incentive-state`, {
        headers: { Authorization: `Bearer ${token}` },
        params: scope,
      })
      .then((res) => {
        const state = res.data || null;

        if (isAdminPreview) {
          setIncentives({
            ...(state || {}),
            stars: 0,
            star_balance: 0,
            submission_attempt_count: 0,
            next_attempt_number: 1,
            submission_cooldown_seconds: 0,
            cooldown_remaining_seconds: 0,
            office_hours_cooldown_exempt: false,
            office_hours_cooldown_exempt_until: null,
          });
          setCooldownLiftedAtMs(0);
          return;
        }

        setIncentives(state);
        setCooldownLiftedAtMs(
          cooldownLiftedAtToMs(
            state?.cooldown_lifted_at,
            state?.cooldown_remaining_seconds,
          ),
        );
      })
      .catch(() => {
        if (showLoading) {
          setIncentives(null);
          setCooldownLiftedAtMs(0);
        }
      })
      .finally(() => {
        if (showLoading) setIsCooldownStateLoading(false);
      });
  }, [buildUploadStateScope, isAdminPreview]);

  const loadOfficeHoursStatus = useCallback(() => {
    if (isAdminPreview) {
      setOfficeHoursStatus(null);
      return;
    }

    const token = localStorage.getItem("AUTOTA_AUTH_TOKEN");

    if (!token || cid <= 0 || !officeHoursModuleId) {
      setOfficeHoursStatus(null);
      return;
    }

    axios
      .get(`${import.meta.env.VITE_API_URL}/submissions/office-hours/status`, {
        headers: { Authorization: `Bearer ${token}` },
        params: {
          class_id: cid,
          module_id: officeHoursModuleId,
        },
      })
      .then((res) => setOfficeHoursStatus(res.data || null))
      .catch(() => {
        // Keep the last confirmed status during a transient polling failure.
      });
  }, [cid, officeHoursModuleId, isAdminPreview]);

  const skipSubmissionCooldown = () => {
    if (isAdminPreview) {
      setIsSkipConfirmationOpen(false);
      return;
    }

    const scope = buildUploadStateScope();
    const token = localStorage.getItem("AUTOTA_AUTH_TOKEN");

    if (!scope || !token || !isCoolingDown || isSkippingCooldown) {
      setIsSkipConfirmationOpen(false);
      return;
    }

    setIsSkippingCooldown(true);
    setIsErrorMessageHidden(true);
    setError_Message("");

    axios
      .post(
        `${import.meta.env.VITE_API_URL}/submissions/skip-submission-cooldown`,
        scope,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      .then((res) => {
        setCooldownLiftedAtMs(0);
        setNowMs(Date.now());
        setIncentives(res.data || null);
      })
      .catch((err) => {
        setError_Message(
          err.response?.data?.message ||
          "Could not skip the submission timer. Please try again.",
        );
        setIsErrorMessageHidden(false);
      })
      .finally(() => {
        setIsSkippingCooldown(false);
        setIsSkipConfirmationOpen(false);
      });
  };

  const startSubmissionCooldown = (seconds: number) => {
    const safeSeconds = Math.max(0, Math.ceil(seconds));

    setCooldownLiftedAtMs(
      safeSeconds > 0 ? Date.now() + safeSeconds * 1000 : 0,
    );
    setNowMs(Date.now());
  };

  const ALLOWED_EXTS = [".py", ".java", ".c", ".rkt"];
  const isJavaFile = (f: File) => f.name.toLowerCase().endsWith(".java");
  const isJavaFileName = (n: string) => /\.java$/i.test(n);

  const isAllowedFileName = (name: string) => {
    const ext = "." + (name.split(".").pop() || "").toLowerCase();
    return ALLOWED_EXTS.includes(ext);
  };

  const rememberPreviousSubmissionId = (submissionId: number | string | null) => {
    const normalized = normalizePositiveSubmissionId(submissionId);

    if (normalized === null) {
      return;
    }

    setPreviousSubmissionId(normalized);
  };

  const clearPreviousSubmissionId = () => {
    setPreviousSubmissionId(null);
  };

  const resolveLatestSubmissionFromPastSubmissions = (
    rows: ApiPastSubmissionsProject[],
  ): LatestPastSubmission | null => {
    if (!project_id || project_id <= 0 || project_id === -1) {
      return null;
    }

    const projectRow = rows.find((row) => getPastProjectId(row) === project_id);

    if (!projectRow) {
      return null;
    }

    if (isCheckpoint) {
      if (!checkpointId) {
        return null;
      }

      const checkpointRows = [
        ...(Array.isArray(projectRow.checkpoints) ? projectRow.checkpoints : []),
        ...(Array.isArray(projectRow.practices) ? projectRow.practices : []),
      ];

      const checkpointRow = checkpointRows.find(
        (row) => getPastCheckpointId(row) === checkpointId,
      );

      return checkpointRow
        ? getPastCheckpointSubmission(checkpointRow)
        : null;
    }

    return getPastMainSubmission(projectRow);
  };

  const fetchLatestSubmissionFromPastSubmissions = (): Promise<LatestPastSubmission | null> => {
    if (isAdminPreview) {
      clearPreviousSubmissionId();
      setPassedAllTests(false);
      setTestcasesPassedCount(0);
      setTestcasesTotalCount(0);
      setCheckedPassedAll(true);
      return Promise.resolve(null);
    }

    const token = localStorage.getItem("AUTOTA_AUTH_TOKEN");

    if (
      !token ||
      !Number.isFinite(cid) ||
      cid <= 0 ||
      !project_id ||
      project_id <= 0 ||
      project_id === -1
    ) {
      clearPreviousSubmissionId();
      return Promise.resolve(null);
    }

    return axios
      .get(`${import.meta.env.VITE_API_URL}/projects/past-submissions`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      .then((res) => {
        const rows: ApiPastSubmissionsProject[] =
          typeof res.data === "string" ? JSON.parse(res.data) : (res.data ?? []);

        const latestSubmission = resolveLatestSubmissionFromPastSubmissions(rows);

        if (latestSubmission !== null) {
          rememberPreviousSubmissionId(latestSubmission.submissionId);
          setPassedAllTests(latestSubmission.passed);
          return latestSubmission;
        }

        clearPreviousSubmissionId();
        setPassedAllTests(false);
        setTestcasesPassedCount(0);
        setTestcasesTotalCount(0);
        setCheckedPassedAll(true);
        return null;
      })
      .catch(() => {
        clearPreviousSubmissionId();
        setTestcasesPassedCount(0);
        setTestcasesTotalCount(0);
        setCheckedPassedAll(true);
        return null;
      });
  };

  const JAVA_MAIN_RE = /\bpublic\s+static\s+void\s+main\s*\(/;

  function pickMainJavaFile(
    allJavaNames: string[],
    namesWithMain: string[],
  ): string {
    if (namesWithMain.length === 1) return namesWithMain[0];

    const mainDotJava = allJavaNames.find(
      (n) => n.toLowerCase() === "main.java",
    );
    if (mainDotJava) return mainDotJava;

    return namesWithMain[0] || "";
  }

  async function computeMainJavaFromLocal(localFiles: File[]) {
    const javaFiles = localFiles.filter((f) => isJavaFileName(f.name));

    if (javaFiles.length <= 1) {
      setMainJavaFileName("");
      return;
    }

    const withMain: string[] = [];

    for (const f of javaFiles) {
      try {
        const txt = await f.text();
        if (JAVA_MAIN_RE.test(txt)) withMain.push(f.name);
      } catch {
        // Ignore read failures.
      }
    }

    setMainJavaFileName(
      pickMainJavaFile(
        javaFiles.map((f) => f.name),
        withMain,
      ),
    );
  }

  useEffect(() => {
    let cancelled = false;

    (async () => {
      if (!(files.length > 1 && files.every(isJavaFile))) {
        if (!cancelled) setMainJavaFileName("");
        return;
      }

      await computeMainJavaFromLocal(files);
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files]);

  useEffect(() => {
    autoGrowTextarea(feedbackRef.current);
  }, [suggestions]);

  useEffect(() => {
    loadStudentUploadState();
  }, [loadStudentUploadState]);

  useEffect(() => {
    loadIncentiveState(true);
  }, [loadIncentiveState]);

  useEffect(() => {
    loadOfficeHoursStatus();
  }, [loadOfficeHoursStatus]);

  useEffect(() => {
    const refreshCooldownState = () => {
      if (document.visibilityState !== "visible") return;
      loadStudentUploadState();
      loadIncentiveState();
      loadOfficeHoursStatus();
    };

    window.addEventListener("focus", refreshCooldownState);
    document.addEventListener("visibilitychange", refreshCooldownState);

    return () => {
      window.removeEventListener("focus", refreshCooldownState);
      document.removeEventListener("visibilitychange", refreshCooldownState);
    };
  }, [loadStudentUploadState, loadIncentiveState, loadOfficeHoursStatus]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      loadStudentUploadState();
      loadIncentiveState();
      loadOfficeHoursStatus();
    }, 15_000);

    return () => window.clearInterval(interval);
  }, [loadStudentUploadState, loadIncentiveState, loadOfficeHoursStatus]);

  useEffect(() => {
    if (!isCoolingDown && !earlyBonusWindowOpen && !isOfficeHoursExempt) return;

    const timer = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);

    return () => window.clearInterval(timer);
  }, [isCoolingDown, earlyBonusWindowOpen, isOfficeHoursExempt]);

  useEffect(() => {
    if (isCoolingDown) {
      setFiles([]);
    } else {
      setIsSkipConfirmationOpen(false);
    }
  }, [isCoolingDown]);

  useEffect(() => {
    const token = localStorage.getItem("AUTOTA_AUTH_TOKEN");

    if (!token || !Number.isFinite(cid) || cid <= 0) {
      setHideClassSelectionCrumb(false);
      return;
    }

    axios
      .get(`${import.meta.env.VITE_API_URL}/class/all?filter=true`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      .then((res) => {
        const rows = Array.isArray(res.data)
          ? (res.data as AssignedClassLite[])
          : [];
        const uniqueSchoolIds = new Set(
          rows
            .map((row) => Number(row.school_id))
            .filter(
              (rowSchoolId) => Number.isFinite(rowSchoolId) && rowSchoolId > 0,
            ),
        );

        setHideClassSelectionCrumb(
          rows.length === 1 &&
          uniqueSchoolIds.size === 1 &&
          Number(rows[0]?.id) === cid,
        );
      })
      .catch(() => {
        setHideClassSelectionCrumb(false);
      });
  }, [cid]);

  useEffect(() => {
    getSubmissionDetails();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid, routeProjectId, moduleId]);

  useEffect(() => {
    let cancelled = false;
    let previewUrl = "";

    setAssignmentPreview(null);
    setAssignmentPreviewError("");
    setIsAssignmentPreviewExpanded(false);

    if (!project_id || project_id <= 0 || project_id === -1) {
      setIsAssignmentPreviewLoading(false);
      return;
    }

    setIsAssignmentPreviewLoading(true);

    axios
      .get(
        `${import.meta.env.VITE_API_URL}/projects/getAssignmentDescription`,
        {
          headers: authHeader(),
          params: {
            project_id,
            checkpoint_id: isCheckpoint ? checkpointId : undefined,
            preview: 1,
          },
          responseType: "blob",
        },
      )
      .then((res) => {
        if (cancelled) return;

        const contentType =
          String((res.headers as any)["content-type"] || res.data?.type || "") ||
          "application/octet-stream";
        const blob =
          res.data instanceof Blob
            ? res.data
            : new Blob([res.data], { type: contentType });

        previewUrl = URL.createObjectURL(blob);
        setAssignmentPreview({
          url: previewUrl,
          contentType,
          filename:
            String((res.headers as any)["x-original-filename"] || "").trim() ||
            String((res.headers as any)["x-filename"] || "").trim() ||
            "Assignment instructions",
        });
      })
      .catch((err) => {
        if (cancelled) return;

        setAssignmentPreviewError(
          err.response?.data?.message ||
          "The assignment instructions could not be previewed.",
        );
      })
      .finally(() => {
        if (!cancelled) setIsAssignmentPreviewLoading(false);
      });

    return () => {
      cancelled = true;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [project_id, isCheckpoint, checkpointId]);

  useEffect(() => {
    let cancelled = false;
    let previewUrl = "";

    setPresentationPreview(null);
    setPresentationPreviewError("");
    setIsPresentationPreviewExpanded(false);

    if (
      !hasModulePresentation ||
      ((!moduleId || moduleId <= 0) && (!project_id || project_id <= 0 || project_id === -1))
    ) {
      setIsPresentationPreviewLoading(false);
      return;
    }

    setIsPresentationPreviewLoading(true);

    const params = moduleId
      ? { module_id: moduleId, preview: 1 }
      : { project_id, preview: 1 };

    axios
      .get(`${import.meta.env.VITE_API_URL}/projects/module_presentation`, {
        headers: authHeader(),
        params,
        responseType: "blob",
      })
      .then((res) => {
        if (cancelled) return;

        const contentType =
          String((res.headers as any)["content-type"] || res.data?.type || "") ||
          "application/octet-stream";
        const blob =
          res.data instanceof Blob
            ? res.data
            : new Blob([res.data], { type: contentType });

        previewUrl = URL.createObjectURL(blob);
        setPresentationPreview({
          url: previewUrl,
          contentType,
          filename:
            String((res.headers as any)["x-original-filename"] || "").trim() ||
            String((res.headers as any)["x-filename"] || "").trim() ||
            modulePresentationFileName ||
            "Module presentation",
        });
      })
      .catch((err) => {
        if (cancelled) return;

        setPresentationPreviewError(
          err.response?.data?.message ||
          "The module presentation could not be previewed.",
        );
      })
      .finally(() => {
        if (!cancelled) setIsPresentationPreviewLoading(false);
      });

    return () => {
      cancelled = true;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [
    hasModulePresentation,
    moduleId,
    project_id,
    modulePresentationFileName,
  ]);

  useEffect(() => {
    setActiveSubmissionId(routeSubmissionId);
  }, [routeSubmissionId, project_id, isCheckpoint, checkpointId]);

  useEffect(() => {
    if (activeSubmissionId !== null || previousSubmissionId === null) return;

    setActiveSubmissionId(submissionIdAsNumber(previousSubmissionId));
  }, [activeSubmissionId, previousSubmissionId]);

  useEffect(() => {
    if (!initialSection || initialSectionAppliedRef.current) return;

    initialSectionAppliedRef.current = true;
    window.requestAnimationFrame(() => scrollToSection(initialSection));
  }, [initialSection, scrollToSection]);

  useEffect(() => {
    if (!isAssignmentPreviewExpanded && !isPresentationPreviewExpanded) return;

    const originalOverflow = document.body.style.overflow;
    const closeExpandedPreview = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setIsAssignmentPreviewExpanded(false);
      setIsPresentationPreviewExpanded(false);
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeExpandedPreview);

    return () => {
      document.body.style.overflow = originalOverflow;
      window.removeEventListener("keydown", closeExpandedPreview);
    };
  }, [isAssignmentPreviewExpanded, isPresentationPreviewExpanded]);

  useEffect(() => {
    if (!Number.isFinite(cid) || cid <= 0 || project_id <= 0) {
      setPythonIdeEnabled(false);
      setSubmissionMethod("upload");
      return;
    }

    let cancelled = false;

    axios
      .get(`${import.meta.env.VITE_API_URL}/upload/ide-context`, {
        headers: authHeader(),
        params: {
          class_id: cid,
          project_id,
          checkpoint_id: isCheckpoint ? checkpointId : undefined,
        },
      })
      .then((res) => {
        if (cancelled) return;

        const enabled = Boolean(res?.data?.python_ide_enabled);
        setPythonIdeEnabled(enabled);

        const defaultFilename = String(res?.data?.default_filename || "").trim();
        if (enabled && defaultFilename.toLowerCase().endsWith(".py")) {
          setPythonFilename(defaultFilename);
        }

        if (!enabled) {
          setSubmissionMethod("upload");
        }
      })
      .catch(() => {
        if (cancelled) return;
        setPythonIdeEnabled(false);
        setSubmissionMethod("upload");
      });

    return () => {
      cancelled = true;
    };
  }, [cid, project_id, isCheckpoint, checkpointId]);

  useEffect(() => {
    if (!project_id || project_id <= 0 || project_id === -1) {
      setPassedAllTests(false);
      setCheckedPassedAll(true);
      setTestcasesPassedCount(0);
      setTestcasesTotalCount(0);
      clearPreviousSubmissionId();
      return;
    }

    if (previousSubmissionId === null) {
      setCheckedPassedAll(false);
      setTestcasesPassedCount(0);
      setTestcasesTotalCount(0);
      return;
    }

    let cancelled = false;
    setCheckedPassedAll(false);

    const qs =
      isCheckpoint && checkpointId
        ? `&checkpoint=1&checkpoint_id=${checkpointId}`
        : "";

    axios
      .get(
        `${import.meta.env.VITE_API_URL}/submissions/testcaseerrors?class_id=${cid}&id=${encodeURIComponent(String(previousSubmissionId))}${qs}`,
        { headers: authHeader() },
      )
      .then((res) => {
        if (cancelled) return;

        let payload: any = res?.data;

        if (typeof payload === "string") {
          try {
            payload = JSON.parse(payload);
          } catch {
            payload = {};
          }
        }

        const results = Array.isArray(payload?.results) ? payload.results : [];
        const passedCount = results.filter(isPassedTestcase).length;
        const allPassed = results.length > 0 && passedCount === results.length;

        setTestcasesPassedCount(passedCount);
        setTestcasesTotalCount(results.length);
        setPassedAllTests((current) => current || allPassed);
        setCheckedPassedAll(true);

        const payloadSubmissionId = getPayloadSubmissionId(payload);
        if (payloadSubmissionId !== null) {
          rememberPreviousSubmissionId(payloadSubmissionId);
        }
      })
      .catch(() => {
        if (cancelled) return;

        setTestcasesPassedCount(0);
        setTestcasesTotalCount(0);
        setCheckedPassedAll(true);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previousSubmissionId, project_id, isCheckpoint, checkpointId, cid]);

  useEffect(() => {
    fetchLatestSubmissionFromPastSubmissions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project_id, isCheckpoint, checkpointId, cid]);

  useEffect(() => {
    if (passedAllTests) {
      setFiles([]);
      setIsErrorMessageHidden(true);
      setError_Message("");
    }
  }, [passedAllTests]);

  useEffect(() => {
    if (!isCheckpoint || !checkpointId || !project_id || project_id <= 0) {
      setCheckpointLabel("");
      return;
    }

    axios
      .get(
        `${import.meta.env.VITE_API_URL}/projects/list_checkpoints_student?project_id=${project_id}`,
        { headers: authHeader() },
      )
      .then((res) => {
        const probs = (res?.data?.problems ?? []) as CheckpointLite[];
        const found = Array.isArray(probs)
          ? probs.find((p) => Number(p?.id) === checkpointId)
          : undefined;

        const n = Number(found?.number ?? checkpointId);
        const left = n ? `Checkpoint ${n}` : "Checkpoint";
        const name = String(
          found?.name || (n ? `Checkpoint ${n}` : "Checkpoint"),
        );

        setCheckpointLabel(`${left}: ${name}`);
      })
      .catch(() => {
        setCheckpointLabel(
          `Checkpoint ${checkpointId}: Checkpoint ${checkpointId}`,
        );
      });
  }, [isCheckpoint, checkpointId, project_id]);

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    if (passedAllTests) {
      setFiles([]);
      setError_Message("");
      setIsErrorMessageHidden(true);
      event.currentTarget.value = "";
      return;
    }

    const selected = event.target.files ? Array.from(event.target.files) : [];
    const valid = selected.filter((f) => isAllowedFileName(f.name));

    if (selected.length && valid.length === 0) {
      setError_Message("Only .py, .java, .c, or .rkt files are allowed.");
      setIsErrorMessageHidden(false);
      event.currentTarget.value = "";
      return;
    }

    if (selected.length !== valid.length) {
      setError_Message("Only .py, .java, .c, or .rkt files are allowed.");
      setIsErrorMessageHidden(false);
      event.currentTarget.value = "";
      return;
    }

    if (valid.length > 1 && !valid.every(isJavaFile)) {
      setFiles([]);
      setError_Message(
        "Multi-file upload is only available for Java (.java) files.",
      );
      setIsErrorMessageHidden(false);
      event.currentTarget.value = "";
      return;
    }

    setIsErrorMessageHidden(true);
    setSubmissionMethod("upload");
    setFiles(valid);
  }

  function getSubmissionDetails() {
    if (!Number.isFinite(cid) || cid <= 0) {
      setProject_name("");
      setProject_id(-1);
      setHasModulePresentation(false);
      setModulePresentationFileName("");
      return;
    }

    if (hasModuleRoute && routeProjectId) {
      setProject_id(routeProjectId);
      loadModulePresentationStatus(routeProjectId);

      if (isAdminPreview) {
        setDueDate("");
        return;
      }

      axios
        .get(
          `${import.meta.env.VITE_API_URL}/submissions/GetSubmissionDetails?class_id=${cid}`,
          {
            headers: authHeader(),
          },
        )
        .then((res) => {
          const activeProjectId = Number(res.data?.[5] || 0);

          if (activeProjectId === routeProjectId) {
            setProject_name(res.data?.[3] || "");
            setDueDate(res.data?.[4] || "");
            return;
          }

          setProject_name("");
          setDueDate("");
        })
        .catch(() => {
          setProject_name("");
          setDueDate("");
        });

      return;
    }

    axios
      .get(
        `${import.meta.env.VITE_API_URL}/submissions/GetSubmissionDetails?class_id=${cid}`,
        {
          headers: authHeader(),
        },
      )
      .then((res) => {
        const activeProjectId = Number(res.data[5] || 0);
        setProject_name(res.data[3]);
        setDueDate(res.data[4]);
        setProject_id(activeProjectId);
        loadModulePresentationStatus(activeProjectId);
      })
      .catch(() => {
        setProject_name("");
        setProject_id(-1);
        setHasModulePresentation(false);
        setModulePresentationFileName("");
      });
  }

  function submitSuggestions() {
    if (isAdminPreview) {
      alert("Feedback submission is disabled in admin preview.");
      return;
    }

    axios
      .post(
        `${import.meta.env.VITE_API_URL}/submissions/submit_suggestion`,
        { suggestion: suggestions },
        { headers: authHeader() },
      )
      .then(
        () => {
          alert(
            "Thank you for your constructive feedback. If you have any other suggestions, please submit them.",
          );
        },
        () => {
          alert(
            "There was an error submitting your feedback. Please try again later.",
          );
        },
      );
  }

  async function runPythonProgram(
    runRequest: PythonIdeRunRequest,
  ): Promise<PythonIdeRunResult> {
    if (!pythonIdeEnabled) {
      throw new Error("The Python IDE is not available for this assignment.");
    }

    const response = await axios.post(
      `${import.meta.env.VITE_API_URL}/upload/run-python`,
      {
        class_id: cid,
        project_id,
        checkpoint_id: isCheckpoint ? checkpointId : null,
        filename: runRequest.filename,
        source: runRequest.source,
        stdin: runRequest.stdin,
      },
      { headers: authHeader() },
    );

    return response.data as PythonIdeRunResult;
  }

  function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault();

    if (isAdminPreview) {
      setError_Message("Program submission is disabled in admin student preview.");
      setIsErrorMessageHidden(false);
      return;
    }

    if (passedAllTests) return;

    if (isCoolingDown) {
      setError_Message(
        canSkipCooldown
          ? `Please wait ${formatCooldown(cooldownRemainingSeconds)} or skip the timer for ${cooldownSkipCost} stars. Test your code in your local deployment before submitting again.`
          : `Please wait ${formatCooldown(cooldownRemainingSeconds)} before submitting again. Test your code in your local deployment first.`,
      );
      setIsErrorMessageHidden(false);
      return;
    }

    if (!Number.isFinite(cid) || cid <= 0) {
      setError_Message("Missing class id.");
      setIsErrorMessageHidden(false);
      return;
    }

    if (!project_id || project_id <= 0 || project_id === -1) {
      setError_Message("No active project was found for this upload.");
      setIsErrorMessageHidden(false);
      return;
    }

    if (submissionMethod === "editor" && !pythonIdeEnabled) {
      setError_Message("The Python IDE is not available for this assignment.");
      setIsErrorMessageHidden(false);
      return;
    }

    const editorFilename = pythonFilename.trim();

    if (
      submissionMethod === "editor" &&
      (!editorFilename || !editorFilename.toLowerCase().endsWith(".py"))
    ) {
      setError_Message("The Python program file name must end in .py.");
      setIsErrorMessageHidden(false);
      return;
    }

    if (submissionMethod === "editor" && !pythonSource.trim()) {
      setError_Message("Enter Python code before submitting the program.");
      setIsErrorMessageHidden(false);
      return;
    }

    const submissionFiles =
      submissionMethod === "editor"
        ? [new File([pythonSource], editorFilename, { type: "text/x-python" })]
        : files;

    if (submissionFiles.length === 0) {
      setError_Message("Please select a file to upload.");
      setIsErrorMessageHidden(false);
      return;
    }

    if (submissionFiles.length > 1 && !submissionFiles.every(isJavaFile)) {
      setError_Message(
        "Multi-file upload is only available for Java (.java) files.",
      );
      setIsErrorMessageHidden(false);
      return;
    }

    if (submissionFiles.some((f) => !isAllowedFileName(f.name))) {
      setError_Message("Only .py, .java, .c, or .rkt files are allowed.");
      setIsErrorMessageHidden(false);
      return;
    }

    setIsErrorMessageHidden(true);
    setIsLoading(true);

    const formData = new FormData();

    submissionFiles.forEach((f) => formData.append("files", f, f.name));
    formData.append("class_id", cid.toString());
    formData.append("project_id", project_id.toString());
    formData.append("submission_method", submissionMethod);

    if (hasModuleRoute && moduleId) {
      formData.append("module_id", moduleId.toString());
    }

    if (isCheckpoint) {
      if (!checkpointId) {
        setError_Message("Missing checkpoint id.");
        setIsErrorMessageHidden(false);
        setIsLoading(false);
        return;
      }

      formData.append("checkpoint", "true");
      formData.append("checkpoint_id", String(checkpointId));
    }

    axios
      .post(`${import.meta.env.VITE_API_URL}/upload/`, formData, {
        headers: authHeader(),
      })
      .then((res) => {
        startSubmissionCooldown(Number(res?.data?.cooldown_seconds ?? 0));
        setIncentives((previous) => ({
          ...(previous || {}),
          stars: Number(res?.data?.stars ?? previous?.stars ?? 0),
          star_balance: Number(res?.data?.stars ?? previous?.star_balance ?? 0),
          submission_attempt_count: Number(
            res?.data?.submission_attempt_count ??
            previous?.submission_attempt_count ??
            0,
          ),
          next_attempt_number: Number(
            res?.data?.next_attempt_number ?? previous?.next_attempt_number ?? 1,
          ),
          submission_cooldown_seconds: Number(res?.data?.cooldown_seconds ?? 0),
          office_hours_cooldown_exempt: Boolean(
            res?.data?.office_hours_cooldown_exempt,
          ),
          office_hours_cooldown_exempt_until:
            res?.data?.office_hours_cooldown_exempt_until ?? null,
        }));

        const sid = getPayloadSubmissionId(res?.data);

        if (sid !== null) {
          rememberPreviousSubmissionId(sid);
          setFiles([]);
          setCheckedPassedAll(false);
          setIsLoading(false);
          showSubmissionResults(sid);
          return;
        }

        fetchLatestSubmissionFromPastSubmissions().then((latestSubmission) => {
          setFiles([]);
          setIsLoading(false);

          if (latestSubmission !== null) {
            showSubmissionResults(latestSubmission.submissionId);
            return;
          }

          setError_Message(
            "Your program was submitted, but the testcase results could not be located. Refresh the page to try again.",
          );
          setIsErrorMessageHidden(false);
        });
      })
      .catch((err) => {
        const retryAfterSeconds = Number(
          err.response?.data?.retry_after_seconds ?? err.response?.headers?.["retry-after"],
        );

        if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
          startSubmissionCooldown(retryAfterSeconds);
          setIncentives((previous) => ({
            ...(previous || {}),
            submission_attempt_count: Number(
              err.response?.data?.submission_attempt_count ??
              previous?.submission_attempt_count ??
              0,
            ),
            next_attempt_number: Number(
              err.response?.data?.next_attempt_number ??
              previous?.next_attempt_number ??
              1,
            ),
            submission_cooldown_seconds: Number(
              err.response?.data?.cooldown_seconds ??
              previous?.submission_cooldown_seconds ??
              retryAfterSeconds,
            ),
          }));
        }

        setError_Message(err.response?.data?.message || "Upload failed.");
        setIsErrorMessageHidden(false);
        setIsLoading(false);
      });
  }

  const CODE_ICON_RE = /\.(py|java|c|h|rkt|scm|cpp)$/i;
  const TEXT_ICON_RE = /\.(txt|md|pdf|doc|docx)$/i;

  const getFileIcon = (filename: string) => {
    if (CODE_ICON_RE.test(filename))
      return <FaCode className="file-language-icon" aria-hidden="true" />;
    if (TEXT_ICON_RE.test(filename)) {
      return (
        <FaAlignJustify className="file-language-icon" aria-hidden="true" />
      );
    }

    return <FaTimesCircle className="file-language-icon" aria-hidden="true" />;
  };

  const pageTitle = useMemo(() => {
    if (isCheckpoint) {
      return (
        checkpointLabel ||
        (checkpointId
          ? `Checkpoint ${checkpointId}: Checkpoint ${checkpointId}`
          : "")
      );
    }

    if (project_name) return `Main Project: ${project_name.replace(/_/g, " ")}`;
    if (moduleName) return `Main Project: ${moduleName}`;

    return "";
  }, [isCheckpoint, checkpointLabel, checkpointId, project_name, moduleName]);

  const breadcrumbsItems = useMemo(() => {
    const titleLabel =
      pageTitle || (isCheckpoint ? "Checkpoint Upload" : "Project Upload");

    if (hasModuleRoute && schoolId && moduleId) {
      if (isAdminPreview) {
        return [
          { label: "School Selection", to: "/schools" },
          { label: "Class Selection", to: `/admin/school/${schoolId}/classes` },
          {
            label: "Admin Menu",
            to: `/admin/school/${schoolId}/class/${cid}/menu`,
          },
          { label: "Student Preview", to: adminPreviewBasePath },
          {
            label: "Module Details",
            to: `${adminPreviewBasePath}/module/${moduleId}`,
          },
          { label: "Student Upload" },
        ];
      }

      return [
        { label: "School Selection", to: "/schools" },
        { label: "Class Selection", to: `/student/school/${schoolId}/classes` },
        {
          label: "Module List",
          to: `/student/school/${schoolId}/class/${cid}/modules`,
        },
        {
          label: "Module Details",
          to: `/student/school/${schoolId}/class/${cid}/module/${moduleId}`,
        },
        { label: "Student Upload" },
      ];
    }

    if (isCheckpoint) {
      return hideClassSelectionCrumb
        ? [
          { label: "Project Upload", to: `/student/${class_id}/upload` },
          {
            label: "Checkpoint Select",
            to: `/student/${class_id}/checkpoint`,
          },
          { label: titleLabel },
        ]
        : [
          { label: "Class Selection", to: "/student/classes" },
          { label: "Project Upload", to: `/student/${class_id}/upload` },
          {
            label: "Checkpoint Select",
            to: `/student/${class_id}/checkpoint`,
          },
          { label: titleLabel },
        ];
    }

    return hideClassSelectionCrumb
      ? [{ label: titleLabel }]
      : [
        { label: "Class Selection", to: "/student/classes" },
        { label: titleLabel },
      ];
  }, [
    pageTitle,
    hasModuleRoute,
    schoolId,
    moduleId,
    cid,
    isCheckpoint,
    hideClassSelectionCrumb,
    class_id,
    isAdminPreview,
    adminPreviewBasePath,
  ]);

  const workspaceViews = [
    {
      id: "instructions" as StudentUploadSection,
      label: "Instructions",
      detail: "View assignment details",
      icon: <FaInfoCircle aria-hidden="true" />,
    },
    {
      id: "submission" as StudentUploadSection,
      label: "Program",
      detail: "Write or upload code",
      icon: <FaCode aria-hidden="true" />,
    },
    {
      id: "testcases" as StudentUploadSection,
      label: "Results",
      detail: "View testcase results",
      icon: <FaCheckCircle aria-hidden="true" />,
    },
  ];

  return (
    <div className="student-upload-page">
      <LoadingAnimation
        show={isLoading}
        message={submissionMethod === "editor" ? "Submitting..." : "Uploading..."}
      />

      <Helmet>
        <title>MAAT</title>
      </Helmet>

      <MenuComponent
        showAdminUpload={false}
        showUpload={false}
        showHelp={false}
        showCreate={false}
        showLast={true}
        showReviewButton={false}
      />

      <DirectoryBreadcrumbs items={breadcrumbsItems} />

      <div className="pageTitle">Student Upload</div>

      {isAdminPreview ? (
        <div className="pageMessage" role="status">
          Admin preview: this assignment is read-only. You can inspect instructions,
          use the program workspace, and review the layout, but you cannot submit.
        </div>
      ) : null}

      <div className="student-upload-shell">
        <section
          className="panel panel-upload"
          aria-label={
            passedAllTests
              ? "Assignment Complete"
              : hasNonPassingSubmission
                ? "Submission Not Passing"
                : "Upload Assignment"
          }
        >
          <header className="panel-header assignment-quest-hero">
            <div className="assignment-quest-copy">
              <h1 className="panel-title panel-title--project">
                {pageTitle || "No Active Project"}
              </h1>
            </div>

            <div className="assignment-quest-side">
              <div
                className={`assignment-quest-progress-card ${passedAllTests
                  ? "is-passed"
                  : hasNonPassingSubmission
                    ? "is-failed"
                    : ""
                  }`}
              >
                <div
                  className="assignment-quest-progress-ring"
                  style={
                    {
                      "--progress-percent": `${testcaseProgress.pct}%`,
                    } as CSSProperties
                  }
                >
                  <span>{testcaseProgress.pct}%</span>
                </div>

                <div className="assignment-quest-progress-title">
                  {checkedPassedAll
                    ? `${testcaseProgress.passed} / ${testcaseProgress.total} testcases passed`
                    : "Checking testcases..."}
                </div>

                {hasNonPassingSubmission ? (
                  <div
                    className="assignment-quest-progress-status is-failed"
                    role="status"
                    aria-live="polite"
                  >
                    <FaTimesCircle aria-hidden="true" />
                    <span>Not passing</span>
                  </div>
                ) : passedAllTests ? (
                  <div
                    className="assignment-quest-progress-status is-passed"
                    role="status"
                    aria-live="polite"
                  >
                    <FaCheckCircle aria-hidden="true" />
                    <span>Passed</span>
                  </div>
                ) : null}
              </div>

              <div className="student-stars-card" aria-label="Student stars and possible reward">
                <FaStar aria-hidden="true" />
                <div className="student-stars-card__body">
                  <div className="student-stars-card__topline">
                    <div>
                      <div className="student-stars-card__count">
                        {formatStarValue(starBalance)}
                      </div>
                      <div className="student-stars-card__label">Stars available</div>
                    </div>
                    <StarSpendingInfo />
                  </div>

                  <div className="student-stars-card__reward">
                    {rewardSummaryText}
                  </div>
                  <div className="student-stars-card__timer">
                    {earlyBonusTimerText}
                  </div>
                </div>
              </div>

            </div>
          </header>

          <nav
            id="student-workspace"
            className="student-workspace-nav"
            aria-label="Assignment workspace"
          >
            <ol role="tablist" aria-label="Assignment workspace views">
              {workspaceViews.map((view, viewIndex) => {
                const isActive = activeWorkspaceSection === view.id;

                return (
                  <li
                    key={view.id}
                    className={`student-workspace-nav__view ${isActive ? "is-active" : ""}`}
                  >
                    <button
                      id={`student-workspace-tab-${view.id}`}
                      type="button"
                      role="tab"
                      onClick={() => scrollToSection(view.id)}
                      onKeyDown={(event) => {
                        let nextIndex = viewIndex;

                        if (event.key === "ArrowRight") {
                          nextIndex = (viewIndex + 1) % workspaceViews.length;
                        } else if (event.key === "ArrowLeft") {
                          nextIndex = (viewIndex - 1 + workspaceViews.length) % workspaceViews.length;
                        } else if (event.key === "Home") {
                          nextIndex = 0;
                        } else if (event.key === "End") {
                          nextIndex = workspaceViews.length - 1;
                        } else {
                          return;
                        }

                        event.preventDefault();
                        const nextView = workspaceViews[nextIndex];
                        if (!nextView) return;
                        scrollToSection(nextView.id);
                        window.requestAnimationFrame(() => {
                          document.getElementById(`student-workspace-tab-${nextView.id}`)?.focus();
                        });
                      }}
                      aria-selected={isActive}
                      aria-controls={`student-${view.id === "submission" ? "submit" : view.id}-section`}
                      aria-label={`${view.label}. ${view.detail}. Open`}
                      tabIndex={isActive ? 0 : -1}
                    >
                      <span className="student-workspace-view__icon" aria-hidden="true">
                        {view.icon}
                      </span>
                      <span className="student-workspace-view__copy">
                        <strong>{view.label}</strong>
                        <small>{view.detail}</small>
                      </span>
                      <span className="student-workspace-view__status">
                        Open
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>

          <div className="student-workflow">

            <section
              id="student-instructions-section"
              className={`assignment-instructions-panel student-workflow-step ${activeWorkspaceSection === "instructions" ? "is-active" : ""}`}
              role="tabpanel"
              aria-labelledby="student-workspace-tab-instructions assignment-instructions-title"
              hidden={activeWorkspaceSection !== "instructions"}
            >
              <header className="assignment-instructions-panel__header">
                <div>
                  <p className="assignment-instructions-panel__eyebrow">
                    Instructions
                  </p>
                  <h2 id="assignment-instructions-title">
                    Assignment Instructions
                  </h2>
                </div>
                <span
                  className={`student-workflow-status ${assignmentPreview ? "is-ready" : ""}`}
                >
                  {isAssignmentPreviewLoading
                    ? "Loading"
                    : assignmentPreview
                      ? "Ready"
                      : "Unavailable"}
                </span>
              </header>

              <div
                className={`assignment-document-viewer ${isAssignmentPreviewExpanded ? "is-expanded" : ""}`}
                role={isAssignmentPreviewExpanded ? "dialog" : undefined}
                aria-modal={isAssignmentPreviewExpanded ? "true" : undefined}
                aria-label={isAssignmentPreviewExpanded ? "Expanded assignment instructions" : undefined}
              >
                <div className="assignment-document-viewer__toolbar">
                  <div className="assignment-document-viewer__identity">
                    <span className="assignment-document-viewer__file-icon" aria-hidden="true">
                      <FaFileWord />
                    </span>
                    <span>
                      <strong>{assignmentPreview?.filename || "Assignment instructions"}</strong>
                      <small>
                        {assignmentPreview?.contentType.toLowerCase().includes("pdf")
                          ? "Word document · print-quality PDF preview"
                          : assignmentPreview?.contentType.toLowerCase().includes("html")
                            ? "Word document · accessible web preview"
                            : "Assignment document"}
                      </small>
                    </span>
                  </div>

                  <div className="assignment-document-viewer__actions">
                    <button
                      type="button"
                      onClick={openAssignmentPreview}
                      disabled={!assignmentPreview}
                      title="Open the preview in a new tab"
                    >
                      <FaExternalLinkAlt aria-hidden="true" />
                      <span>Open</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsAssignmentPreviewExpanded((current) => !current)}
                      disabled={!assignmentPreview}
                      title={isAssignmentPreviewExpanded ? "Exit expanded view" : "Expand the preview"}
                    >
                      {isAssignmentPreviewExpanded ? (
                        <FaCompressAlt aria-hidden="true" />
                      ) : (
                        <FaExpandAlt aria-hidden="true" />
                      )}
                      <span>{isAssignmentPreviewExpanded ? "Close" : "Expand"}</span>
                    </button>
                  </div>
                </div>

                <div className="assignment-instructions-panel__preview">
                  {isAssignmentPreviewLoading ? (
                    <div className="assignment-preview-state" role="status" aria-live="polite">
                      <FaClock aria-hidden="true" />
                      <strong>Loading assignment instructions…</strong>
                      <span>Preparing the best available browser preview.</span>
                    </div>
                  ) : assignmentPreview ? (
                    <iframe
                      key={assignmentPreview.url}
                      src={assignmentPreview.url}
                      className="assignment-instructions-frame"
                      title={`${assignmentPreview.filename} preview`}
                      sandbox={
                        assignmentPreview.contentType.toLowerCase().includes("text/html")
                          ? ""
                          : undefined
                      }
                    />
                  ) : (
                    <div className="assignment-preview-state is-error" role="alert">
                      <FaInfoCircle aria-hidden="true" />
                      <strong>
                        {assignmentPreviewError || "No assignment preview is available."}
                      </strong>
                      <span>Ask your instructor for a browser-compatible copy if needed.</span>
                    </div>
                  )}
                </div>
              </div>

              {hasModulePresentation ? (
                <div
                  className={`assignment-document-viewer presentation-document-viewer ${isPresentationPreviewExpanded ? "is-expanded" : ""}`}
                  role={isPresentationPreviewExpanded ? "dialog" : undefined}
                  aria-modal={isPresentationPreviewExpanded ? "true" : undefined}
                  aria-label={isPresentationPreviewExpanded ? "Expanded module presentation" : undefined}
                >
                  <div className="assignment-document-viewer__toolbar">
                    <div className="assignment-document-viewer__identity">
                      <span
                        className="assignment-document-viewer__file-icon presentation-document-viewer__file-icon"
                        aria-hidden="true"
                      >
                        <FaFilePowerpoint />
                      </span>
                      <span>
                        <strong>
                          {presentationPreview?.filename ||
                            modulePresentationFileName ||
                            "Module presentation"}
                        </strong>
                        <small>Module presentation</small>
                      </span>
                    </div>

                    <div className="assignment-document-viewer__actions">
                      <button
                        type="button"
                        onClick={openPresentationPreview}
                        disabled={!presentationPreview}
                        title="Open the presentation preview in a new tab"
                      >
                        <FaExternalLinkAlt aria-hidden="true" />
                        <span>Open</span>
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setIsPresentationPreviewExpanded((current) => !current)
                        }
                        disabled={!presentationPreview}
                        title={
                          isPresentationPreviewExpanded
                            ? "Exit expanded view"
                            : "Expand the presentation preview"
                        }
                      >
                        {isPresentationPreviewExpanded ? (
                          <FaCompressAlt aria-hidden="true" />
                        ) : (
                          <FaExpandAlt aria-hidden="true" />
                        )}
                        <span>
                          {isPresentationPreviewExpanded ? "Close" : "Expand"}
                        </span>
                      </button>
                    </div>
                  </div>

                  <div className="assignment-instructions-panel__preview">
                    {isPresentationPreviewLoading ? (
                      <div className="assignment-preview-state" role="status" aria-live="polite">
                        <FaClock aria-hidden="true" />
                        <strong>Loading module presentation…</strong>
                        <span>Preparing the presentation for browser viewing.</span>
                      </div>
                    ) : presentationPreview ? (
                      <iframe
                        key={presentationPreview.url}
                        src={presentationPreview.url}
                        className="assignment-instructions-frame"
                        title={`${presentationPreview.filename} preview`}
                        sandbox={
                          presentationPreview.contentType.toLowerCase().includes("text/html")
                            ? ""
                            : undefined
                        }
                      />
                    ) : (
                      <div className="assignment-preview-state is-error" role="alert">
                        <FaInfoCircle aria-hidden="true" />
                        <strong>
                          {presentationPreviewError ||
                            "The module presentation could not be previewed."}
                        </strong>
                        <span>The presentation is optional for this module.</span>
                      </div>
                    )}
                  </div>
                </div>
              ) : null}

              <footer className="student-workflow-actions">
                <div className="student-workflow-actions__group">
                  {activeSubmissionId ? (
                    <button
                      type="button"
                      className="student-workflow-actions__secondary"
                      onClick={() => scrollToSection("testcases")}
                    >
                      Open latest results
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="student-workflow-actions__primary"
                    onClick={() => scrollToSection("submission")}
                  >
                    Open program workspace
                    <FaArrowRight aria-hidden="true" />
                  </button>
                </div>
              </footer>
            </section>

            <section
              id="student-submit-section"
              className={`student-submit-section student-workflow-step ${activeWorkspaceSection === "submission" ? "is-active" : ""}`}
              role="tabpanel"
              aria-labelledby="student-workspace-tab-submission student-submit-title"
              hidden={activeWorkspaceSection !== "submission"}
            >
              <header className="student-submit-section__header">
                <div>
                  <p className="student-submit-section__eyebrow">Program workspace</p>
                  <h2 id="student-submit-title">Prepare and Submit Your Program</h2>
                </div>
                <span
                  className={`student-workflow-status ${passedAllTests || activeSubmissionId ? "is-complete" : hasSelectedProgram ? "is-ready" : ""}`}
                >
                  {passedAllTests
                    ? "Completed"
                    : isCoolingDown
                      ? `Ready in ${formatCooldown(cooldownRemainingSeconds)}`
                      : hasSelectedProgram
                        ? "Ready to submit"
                        : "Program needed"}
                </span>
              </header>

              {officeHoursCooldownDisabled ? (
                <div className="office-hours-cooldown-banner" role="status" aria-live="polite">
                  <span className="office-hours-cooldown-banner__icon" aria-hidden="true">
                    <FaUsers />
                  </span>
                  <div>
                    <strong>Office hours: cooldown disabled</strong>
                    <span>
                      You may submit as often as needed for the next {formatCooldown(officeHoursRemainingSeconds)}.
                    </span>
                  </div>
                </div>
              ) : !passedAllTests && isWaitingForOfficeHours ? (
                <div className="office-hours-cooldown-banner is-waiting" role="status" aria-live="polite">
                  <span className="office-hours-cooldown-banner__icon" aria-hidden="true">
                    <FaUsers />
                  </span>
                  <div>
                    <strong>Office hours: waiting for an admin</strong>
                    <span>
                      You are in the queue{officeHoursStatus?.queue_position ? ` at position ${officeHoursStatus.queue_position}` : ""}. Cooldowns remain active until an admin starts helping you.
                    </span>
                  </div>
                </div>
              ) : !passedAllTests && showOfficeHoursAvailable ? (
                <div className="office-hours-cooldown-banner is-available" role="status" aria-live="polite">
                  <span className="office-hours-cooldown-banner__icon" aria-hidden="true">
                    <FaUsers />
                  </span>
                  <div>
                    <strong>Office hours are active</strong>
                    <span>
                      Join the queue from Module Details to request cooldown-free help.
                    </span>
                  </div>
                </div>
              ) : null}

              {passedAllTests ? (
                <section
                  className="assignment-complete-screen"
                  role="status"
                  aria-live="polite"
                  aria-labelledby="assignment-complete-title"
                >
                  <div className="assignment-complete-screen__content">
                    <span
                      className="assignment-complete-screen__icon"
                      aria-hidden="true"
                    >
                      <FaCheckCircle />
                    </span>
                    <p className="assignment-complete-screen__eyebrow">
                      Submission complete
                    </p>
                    <h2 id="assignment-complete-title">
                      You passed all testcases!
                    </h2>
                    <p>
                      You&apos;re finished
                      {isCheckpoint
                        ? " with this checkpoint"
                        : " with this assignment"}
                      . Additional submissions are disabled.
                    </p>

                    {previousSubmissionId ? (
                      <button
                        type="button"
                        className="assignment-complete-screen__link"
                        onClick={() => showSubmissionResults(previousSubmissionId)}
                      >
                        <FaEye aria-hidden="true" />
                        <span>View testcase results</span>
                      </button>
                    ) : (
                      <span className="assignment-complete-screen__link is-disabled">
                        Previous testcases unavailable
                      </span>
                    )}
                  </div>
                </section>
              ) : (
                <form
                  className={`upload-form ${isLoading ? "is-loading" : ""}`}
                  onSubmit={handleSubmit}
                >
                  {pythonIdeEnabled ? (
                    <fieldset className="student-upload-program-source">
                      <legend className="student-upload-program-source__legend">
                        Choose how to provide your program
                      </legend>
                      <div className="student-upload-program-source__options">
                        <button
                          type="button"
                          className={`student-upload-program-source__option ${submissionMethod === "upload" ? "is-active" : ""}`}
                          aria-pressed={submissionMethod === "upload"}
                          onClick={() => {
                            setSubmissionMethod("upload");
                            setIsErrorMessageHidden(true);
                          }}
                        >
                          Upload program
                        </button>
                        <button
                          type="button"
                          className={`student-upload-program-source__option ${submissionMethod === "editor" ? "is-active" : ""}`}
                          aria-pressed={submissionMethod === "editor"}
                          onClick={() => {
                            setSubmissionMethod("editor");
                            setIsErrorMessageHidden(true);
                          }}
                        >
                          Write Python
                        </button>
                      </div>
                    </fieldset>
                  ) : null}

                  <div className="dropzone">
                    {submissionMethod === "editor" && pythonIdeEnabled ? (
                      <PythonIDE
                        filename={pythonFilename}
                        source={pythonSource}
                        disabled={project_id <= 0}
                        onFilenameChange={setPythonFilename}
                        onSourceChange={setPythonSource}
                        onRun={runPythonProgram}
                      />
                    ) : (
                      <div
                        className={`file-drop-area ${isCoolingDown ? "is-cooldown-locked" : ""}`}
                        aria-disabled={isCoolingDown}
                        onDragOver={(e) => {
                          e.preventDefault();
                          if (isCoolingDown) e.dataTransfer.dropEffect = "none";
                        }}
                        onDrop={(e) => {
                          e.preventDefault();

                          if (passedAllTests || isCoolingDown) return;

                          const dropped = Array.from(e.dataTransfer.files || []);
                          const valid = dropped.filter((f) =>
                            isAllowedFileName(f.name),
                          );

                          if (dropped.length && valid.length === 0) {
                            setError_Message(
                              "Only .py, .java, .c, or .rkt files are allowed.",
                            );
                            setIsErrorMessageHidden(false);
                            return;
                          }

                          if (dropped.length !== valid.length) {
                            setError_Message(
                              "Only .py, .java, .c, or .rkt files are allowed.",
                            );
                            setIsErrorMessageHidden(false);
                            return;
                          }

                          if (valid.length > 1 && !valid.every(isJavaFile)) {
                            setFiles([]);
                            setError_Message(
                              "Multi-file upload is only available for Java (.java) files.",
                            );
                            setIsErrorMessageHidden(false);
                            return;
                          }

                          setIsErrorMessageHidden(true);
                          setSubmissionMethod("upload");
                          setFiles(valid);
                        }}
                      >
                        {!files.length ? (
                          <>
                            <input
                              type="file"
                              className="file-input"
                              accept=".py,.java,.c,.rkt"
                              multiple
                              disabled={passedAllTests || isCoolingDown}
                              onChange={handleFileChange}
                            />

                            <div className="file-drop-message">
                              <FaCloudUploadAlt
                                className="file-drop-icon"
                                aria-hidden="true"
                              />
                              <p>
                                Drag &amp; drop your file(s) here or{" "}
                                <span className="browse-text">browse</span>
                              </p>
                              <p className="file-drop-hint">
                                Multi-file upload is supported for Java only.
                              </p>
                            </div>
                          </>
                        ) : (
                          <div className="file-preview">
                            <button
                              type="button"
                              className="exchange-icon"
                              aria-label="Clear selected files"
                              title="Clear selected files"
                              onClick={() => setFiles([])}
                              disabled={isCoolingDown}
                            >
                              <FaExchangeAlt aria-hidden="true" />
                            </button>

                            <div className="file-preview-list" title="Selected files">
                              {files.map((f) => (
                                <div
                                  key={f.name}
                                  className="file-preview-row solution-file-card"
                                >
                                  <div className="file-icon-wrapper" aria-hidden="true">
                                    <FaRegFile
                                      className="file-outline-icon"
                                      aria-hidden="true"
                                    />
                                    {getFileIcon(f.name)}
                                  </div>

                                  <span className="file-name">
                                    {f.name}
                                    {files.length > 1 &&
                                      files.every(isJavaFile) &&
                                      mainJavaFileName &&
                                      f.name === mainJavaFileName && (
                                        <span className="main-indicator">Main</span>
                                      )}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {submissionMethod === "upload" && isCooldownStateLoading && !passedAllTests ? (
                      <div
                        className="submission-cooldown-lock is-checking"
                        role="status"
                        aria-live="polite"
                      >
                        <div className="submission-cooldown-lock__content">
                          <FaClock aria-hidden="true" />
                          <h2>Checking submission cooldown</h2>
                          <p>Verifying whether this project is ready for another submission.</p>
                        </div>
                      </div>
                    ) : submissionMethod === "upload" && isCoolingDown && !passedAllTests ? (
                      <div
                        className="submission-cooldown-lock"
                        role="status"
                        aria-live="polite"
                      >
                        <div className="submission-cooldown-lock__content">
                          <FaLock aria-hidden="true" />
                          <h2>{submissionTypeLabel} cooldown active</h2>
                          <p className="submission-cooldown-lock__timer">
                            Attempt {nextAttemptNumber} unlocks in{" "}
                            {formatCooldown(cooldownRemainingSeconds)}
                          </p>
                          <p>
                            Test your code in your local deployment before submitting it again.
                          </p>
                          <button
                            type="button"
                            className="submission-cooldown-view-button"
                            aria-label="View latest submission"
                            disabled={!previousSubmissionId}
                            onClick={() => showSubmissionResults(previousSubmissionId)}
                          >
                            <FaEye aria-hidden="true" />
                            <span>View Testcases</span>
                          </button>
                          <button
                            type="button"
                            className={`skip-cooldown-button ${!canSkipCooldown || isSkippingCooldown ? "disabled" : ""}`}
                            disabled={!canSkipCooldown || isSkippingCooldown}
                            onClick={() => setIsSkipConfirmationOpen(true)}
                            title={
                              canSkipCooldown
                                ? `Spend ${cooldownSkipCost} ${cooldownSkipStarLabel} to skip the timer`
                                : `You need ${cooldownSkipCost} ${cooldownSkipStarLabel} to skip the timer`
                            }
                          >
                            <FaForward aria-hidden="true" />
                            {isSkippingCooldown
                              ? "Skipping..."
                              : `Skip Timer (${cooldownSkipCost} ${cooldownSkipStarLabel})`}
                          </button>
                        </div>
                      </div>
                    ) : null}

                    {project_id === -1 && (
                      <div
                        className="no-active-project-overlay"
                        role="alert"
                        aria-live="assertive"
                      >
                        <div className="no-active-project-content">
                          <FaBan
                            className="no-active-project-icon"
                            aria-hidden="true"
                          />
                          <h2 className="no-active-project-title">
                            No active project
                          </h2>
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="actions">
                    <button
                      type="submit"
                      disabled={!is_allowed_to_submit || !canSubmit || passedAllTests || isLoading}
                      className={[
                        "primary",
                        "student-upload-submit",
                        !is_allowed_to_submit || !canSubmit || isLoading
                          ? "disabled"
                          : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                    >
                      <span
                        className="student-upload-submit__icon"
                        aria-hidden="true"
                      >
                        {isCooldownStateLoading || isCoolingDown ? (
                          <FaClock />
                        ) : (
                          <FaCheckCircle />
                        )}
                      </span>
                      <span>
                        {isAdminPreview
                          ? "Submission Disabled in Preview"
                          : isCooldownStateLoading
                            ? "Checking Cooldown"
                            : isCoolingDown
                              ? "Cooling Down"
                              : "Submit program"}
                      </span>
                    </button>
                  </div>

                  <section
                    className={`submission-cooldown-policy ${officeHoursCooldownDisabled ? "is-office-hours" : ""}`}
                    aria-labelledby="submission-cooldown-title"
                  >
                    <div className="submission-cooldown-policy__header">
                      <div className="submission-cooldown-policy__heading">
                        <span className="submission-cooldown-policy__icon" aria-hidden="true">
                          <FaClock />
                        </span>
                        <div>
                          <h2 id="submission-cooldown-title">
                            {isCheckpoint ? "Checkpoint Cooldowns" : "Main Project Cooldowns"}
                          </h2>
                          <p>
                            {officeHoursCooldownDisabled
                              ? "Cooldowns are disabled while your office-hours help session is active."
                              : "After each attempt, wait the time shown before submitting again."}
                          </p>
                        </div>
                      </div>
                      <div
                        className={[
                          "submission-cooldown-policy__current",
                          isCooldownStateLoading
                            ? "is-loading"
                            : officeHoursCooldownDisabled
                              ? "is-office-hours"
                              : isCoolingDown
                                ? "is-active"
                                : "is-ready",
                        ].join(" ")}
                        role="status"
                        aria-live="polite"
                      >
                        {isCooldownStateLoading ? (
                          <>
                            <FaClock aria-hidden="true" />
                            <span>Checking cooldown</span>
                          </>
                        ) : officeHoursCooldownDisabled ? (
                          <>
                            <FaUsers aria-hidden="true" />
                            <span>Office hours</span>
                            <strong>{formatCooldown(officeHoursRemainingSeconds)}</strong>
                          </>
                        ) : isCoolingDown ? (
                          <>
                            <FaClock aria-hidden="true" />
                            <span>Cooldown ends in</span>
                            <strong>{formatCooldown(cooldownRemainingSeconds)}</strong>
                          </>
                        ) : (
                          <>
                            <FaCheckCircle aria-hidden="true" />
                            <span>Ready</span>
                            <strong>Attempt {nextAttemptNumber}</strong>
                          </>
                        )}
                      </div>
                    </div>

                    <ol
                      className={`submission-cooldown-policy__grid ${isCheckpoint ? "is-checkpoint" : "is-main"} ${officeHoursCooldownDisabled ? "is-office-hours" : ""}`}
                      aria-label={`${submissionTypeShortLabel} submission cooldown schedule${officeHoursCooldownDisabled ? "; cooldowns disabled during office hours" : ""}`}
                    >
                      {(isCheckpoint ? CHECKPOINT_SCHEDULE : MAIN_SCHEDULE).map((item, index, arr) => {
                        const isLast = index === arr.length - 1;
                        const isCurrent =
                          !officeHoursCooldownDisabled &&
                          isCoolingDown &&
                          (
                            submissionAttemptCount === item.attempt ||
                            (isLast && submissionAttemptCount >= item.attempt)
                          );
                        const isNext =
                          !officeHoursCooldownDisabled &&
                          !isCoolingDown &&
                          (
                            nextAttemptNumber === item.attempt ||
                            (isLast && nextAttemptNumber >= item.attempt)
                          );

                        return (
                          <li
                            className={[
                              "submission-cooldown-policy__step",
                              isCurrent ? "is-current" : "",
                              isNext ? "is-next" : "",
                            ].filter(Boolean).join(" ")}
                            key={item.attempt}
                            aria-current={isCurrent ? "step" : undefined}
                            aria-label={`${item.label}: ${officeHoursCooldownDisabled ? "cooldown disabled during office hours" : `${item.value} cooldown afterward${isCurrent ? ", active cooldown" : isNext ? ", next cooldown" : ""}`}`}
                          >
                            <div className="submission-cooldown-policy__step-header">
                              <span className="submission-cooldown-policy__attempt">
                                {item.label}
                              </span>
                            </div>
                            <span
                              className="submission-cooldown-policy__connector"
                              aria-hidden="true"
                            >
                              <span>After this attempt</span>
                              <FaArrowRight aria-hidden="true" />
                            </span>
                            <div className="submission-cooldown-policy__value">
                              {officeHoursCooldownDisabled ? (
                                <span className="submission-cooldown-policy__state is-office-hours">
                                  Disabled
                                </span>
                              ) : isCurrent ? (
                                <span className="submission-cooldown-policy__state is-current">
                                  Active
                                </span>
                              ) : isNext ? (
                                <span className="submission-cooldown-policy__state">
                                  Next cooldown
                                </span>
                              ) : null}
                              <span className="submission-cooldown-policy__value-icon" aria-hidden="true">
                                {officeHoursCooldownDisabled ? <FaUsers /> : <FaClock />}
                              </span>
                              <span className="submission-cooldown-policy__value-copy">
                                <span>{officeHoursCooldownDisabled ? "Office hours" : "Cooldown"}</span>
                                <strong>{officeHoursCooldownDisabled ? "No wait" : item.value}</strong>
                              </span>
                            </div>
                          </li>
                        );
                      })}
                    </ol>
                  </section>
                </form>
              )}

              <div className="below-upload">
                <ErrorMessage
                  message={error_message}
                  isHidden={isErrorMessageHidden}
                />
              </div>

              <footer className="student-workflow-actions student-workflow-actions--split">
                <button
                  type="button"
                  className="student-workflow-actions__secondary"
                  onClick={() => scrollToSection("instructions")}
                >
                  Open instructions
                </button>
                {activeSubmissionId ? (
                  <button
                    type="button"
                    className="student-workflow-actions__primary"
                    onClick={() => scrollToSection("testcases")}
                  >
                    Open latest results
                    <FaArrowRight aria-hidden="true" />
                  </button>
                ) : (
                  <span>Submitting automatically opens the testcase results.</span>
                )}
              </footer>
            </section>

            <section
              id="student-testcases-section"
              ref={resultsRef}
              className={[
                "student-inline-results",
                "student-workflow-step",
                activeWorkspaceSection === "testcases" ? "is-active" : "",
                hasNonPassingSubmission ? "has-failed-submission" : "",
              ].filter(Boolean).join(" ")}
              role="tabpanel"
              aria-labelledby="student-workspace-tab-testcases student-testcases-title"
              hidden={activeWorkspaceSection !== "testcases"}
            >
              <header className="student-inline-results__header">
                <div>
                  <p className="student-inline-results__eyebrow">
                    Results workspace
                  </p>
                  <h2 id="student-testcases-title">Testcase Results</h2>
                </div>
                {activeSubmissionId ? (
                  <span
                    className={[
                      "student-inline-results__submission",
                      passedAllTests
                        ? "is-passed"
                        : hasNonPassingSubmission
                          ? "is-failed"
                          : "",
                    ].filter(Boolean).join(" ")}
                  >
                    {passedAllTests
                      ? "All testcases passed"
                      : hasNonPassingSubmission
                        ? "Not passing"
                        : `Submission #${activeSubmissionId}`}
                  </span>
                ) : (
                  <span className="student-workflow-status">Waiting for submission</span>
                )}
              </header>

              {activeSubmissionId !== null ? (
                <div className="student-inline-results__content">
                  {hasNonPassingSubmission ? (
                    <div
                      className="submission-result-alert submission-result-alert--failed"
                      role="alert"
                      aria-live="assertive"
                    >
                      <span
                        className="submission-result-alert__icon"
                        aria-hidden="true"
                      >
                        <FaTimesCircle />
                      </span>
                      <div className="submission-result-alert__copy">
                        <p className="submission-result-alert__eyebrow">
                          Submission did not pass
                        </p>
                        <h3>This program is not correct yet.</h3>
                        <p>
                          <strong>
                            {testcaseProgress.passed} of {testcaseProgress.total} testcases passed;{" "}
                            {testcaseProgress.failed} failed.
                          </strong>{" "}
                          Review the failed testcase{testcaseProgress.failed === 1 ? "" : "s"}
                          {" "}below, update your program, and submit again.
                        </p>
                      </div>
                    </div>
                  ) : null}

                  <DiffView
                    key={`${activeSubmissionId}:${cid}:${isCheckpoint ? checkpointId : "main"}`}
                    submissionId={activeSubmissionId}
                    classId={cid}
                    disableCopy
                    isPractice={isCheckpoint}
                    practiceProblemId={checkpointId}
                    allowTestcaseInputPurchases={!isAdminPreview}
                  />
                </div>
              ) : (
                <div className="student-inline-results__empty">
                  <FaRegFile aria-hidden="true" />
                  <h3>No testcase results yet</h3>
                  <p>
                    Open the Program view and submit your code. The latest testcase
                    results will stay available in this view.
                  </p>
                  <button type="button" onClick={() => scrollToSection("submission")}>
                    Open program workspace
                  </button>
                </div>
              )}

              <footer className="student-workflow-actions student-workflow-actions--split">
                <button
                  type="button"
                  className="student-workflow-actions__secondary"
                  onClick={() => scrollToSection("instructions")}
                >
                  Open instructions
                </button>
                <button
                  type="button"
                  className="student-workflow-actions__primary"
                  onClick={() => scrollToSection("submission")}
                >
                  Open program workspace
                  <FaArrowRight aria-hidden="true" />
                </button>
              </footer>
            </section>
          </div>
        </section>
      </div>

      {isSkipConfirmationOpen ? (
        <div
          className="skip-cooldown-confirmation"
          onMouseDown={(event) => {
            if (
              event.target === event.currentTarget &&
              !isSkippingCooldown
            ) {
              setIsSkipConfirmationOpen(false);
            }
          }}
        >
          <div
            className="skip-cooldown-confirmation__dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="skip-cooldown-confirmation-title"
            aria-describedby="skip-cooldown-confirmation-description"
            onKeyDown={(event) => {
              if (event.key === "Escape" && !isSkippingCooldown) {
                setIsSkipConfirmationOpen(false);
              }
            }}
          >
            <span
              className="skip-cooldown-confirmation__icon"
              aria-hidden="true"
            >
              <FaStar />
            </span>
            <h2 id="skip-cooldown-confirmation-title">
              Spend {cooldownSkipCost} {cooldownSkipStarLabel}?
            </h2>
            <p id="skip-cooldown-confirmation-description">
              This will immediately end the {submissionTypeShortLabel.toLowerCase()}{" "}
              submission cooldown. This purchase cannot be undone.
            </p>

            <div className="skip-cooldown-confirmation__balance">
              <span>
                Current balance
                <strong>{formatStarCount(starBalance)}</strong>
              </span>
              <FaArrowRight aria-hidden="true" />
              <span>
                Balance after
                <strong>
                  {formatStarCount(Math.max(0, starBalance - cooldownSkipCost))}
                </strong>
              </span>
            </div>

            <div className="skip-cooldown-confirmation__actions">
              <button
                type="button"
                className="skip-cooldown-confirmation__cancel"
                disabled={isSkippingCooldown}
                onClick={() => setIsSkipConfirmationOpen(false)}
                autoFocus
              >
                Cancel
              </button>
              <button
                type="button"
                className="skip-cooldown-confirmation__confirm"
                disabled={isSkippingCooldown}
                onClick={skipSubmissionCooldown}
              >
                <FaForward aria-hidden="true" />
                {isSkippingCooldown
                  ? "Spending..."
                  : `Confirm and spend ${cooldownSkipCost} ${cooldownSkipStarLabel}`}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default StudentUpload;
