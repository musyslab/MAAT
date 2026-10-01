// AdminAnalyticsDashboard.tsx: Builds the class progress dashboard, including filters and module visibility controls.
import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { Helmet } from "react-helmet";
import { Link, useParams } from "react-router-dom";
import {
    FaCheckCircle,
    FaClipboardCheck,
    FaExclamationTriangle,
    FaEye,
    FaEyeSlash,
    FaFilter,
    FaFolderOpen,
    FaSearch,
    FaSortAlphaDown,
    FaTimesCircle,
    FaUsers,
} from "react-icons/fa";

import MenuComponent from "../components/MenuComponent";
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs";
import LoadingAnimation from "../components/LoadingAnimation";
import "../../styling/Selection.scss";
import "../../styling/AdminAnalyticsDashboard.scss";

// Uses the API base URL configured for this deployment.
const API_URL = import.meta.env.VITE_API_URL;
const TEST_USER_ID = -1;

// Describes the route params data expected by this file.
type RouteParams = {
    school_id: string;
    class_id: string;
};

// Describes the class access response data expected by this file.
type ClassAccessResponse = {
    id?: number;
    name?: string;
    school_id?: number;
    school_name?: string;
};

// Describes the raw module data expected by this file.
type RawModule = {
    Id: number;
    ClassId: number;
    Name: string;
    Start?: string;
    End?: string;
    MainProjectId?: number | null;
    MainProjectName?: string;
};

// Describes the raw project data expected by this file.
type RawProject = {
    Id: number;
    Name: string;
    Start?: string;
    End?: string;
    TotalSubmissions?: number;
    CheckpointTotalSubmissions?: number;
    ModuleId?: number | null;
};

// Describes the raw checkpoint data expected by this file.
type RawCheckpoint = {
    id?: number;
    number?: number;
    name?: string;
    enabled?: boolean;
};

// Describes the dashboard item kind data expected by this file.
type DashboardItemKind = "main" | "checkpoint";

// Describes the dashboard item data expected by this file.
type DashboardItem = {
    id: string;
    kind: DashboardItemKind;
    projectId: number;
    projectName: string;
    moduleId: number;
    moduleName: string;
    isFirstInModule: boolean;
    checkpointId?: number;
    checkpointNumber?: number;
    checkpointName?: string;
    label: string;
    shortLabel: string;
};

// Describes the student summary data expected by this file.
type StudentSummary = {
    userId: number;
    firstName: string;
    lastName: string;
    fullName: string;
    studentNumber: string;
    lecture: string;
    lab: string;
    isLocked: boolean;
};

// Describes the progress cell data expected by this file.
type ProgressCell = {
    studentUserId: number;
    itemId: string;
    item: DashboardItem;
    attempts: number;
    lastSubmitted: string;
    passed: boolean | null;
    submissionId: number | null;
    grade: string;
    skipped: boolean;
};

// Describes the student progress row data expected by this file.
type StudentProgressRow = StudentSummary & {
    cells: Record<string, ProgressCell>;
    completed: number;
    attempted: number;
    total: number;
    percentComplete: number;
};

// Describes the sort mode data expected by this file.
type SortMode = "last-asc" | "last-desc";

// Describes the module visibility option data expected by this file.
type ModuleVisibilityOption = {
    moduleId: number;
    moduleName: string;
    itemCount: number;
};

// Describes the analytics dashboard payload data expected by this file.
type AnalyticsDashboardPayload = {
    modules: RawModule[];
    projects: RawProject[];
    checkpointsByProjectId: Record<string, RawCheckpoint[]>;
    submissionsByItemId: Record<string, Record<string, unknown>>;
    hiddenModulesByStudentId?: Record<string, unknown>;
};

// Builds the authorization headers, preserving an existing Bearer prefix.
function authHeaders() {
    const rawToken = localStorage.getItem("AUTOTA_AUTH_TOKEN") || "";
    const token = rawToken.trim();

    return {
        Authorization: token.startsWith("Bearer ")
            ? token
            : `Bearer ${token}`,
    };
}

// Converts an unknown value to a finite number, using the fallback when conversion fails.
function asNumber(value: unknown, fallback = 0): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

// Converts a present value to text and uses the fallback for null or undefined.
function asString(value: unknown, fallback = ""): string {
    if (value === null || value === undefined) {
        return fallback;
    }

    return String(value);
}

// Accepts only finite, positive submission IDs before building submission links.
function isRealSubmissionId(value: unknown): boolean {
    const parsed = Number(value);
    return Number.isSafeInteger(parsed) && parsed > 0;
}

// Normalizes boolean, numeric, and string pass values; unknown values remain unset.
function isPassingValue(value: unknown): boolean | null {
    if (
        value === true ||
        value === "true" ||
        value === "True" ||
        value === 1 ||
        value === "1"
    ) {
        return true;
    }

    if (
        value === false ||
        value === "false" ||
        value === "False" ||
        value === 0 ||
        value === "0"
    ) {
        return false;
    }

    return null;
}

// Formats date time for this view.
function formatDateTime(value: string): string {
    if (!value || value === "N/A") {
        return "";
    }

    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
        return value;
    }

    return parsed.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

// Treats missing attempt counts as zero before calculating progress.
function normalizeAttempts(value: unknown): number {
    if (value === "N/A" || value === null || value === undefined) {
        return 0;
    }

    return asNumber(value, 0);
}

// Builds checkpoint identifiers and labels, skipping entries without a valid ID.
function normalizeCheckpoint(
    row: RawCheckpoint,
    index: number,
): {
    checkpointId: number;
    checkpointNumber: number;
    checkpointName: string;
} | null {
    const checkpointId = asNumber(
        row.id,
        0,
    );

    if (checkpointId <= 0) {
        return null;
    }

    const checkpointNumber = asNumber(row.number, index + 1);
    const checkpointName = asString(
        row.name,
        `Checkpoint ${checkpointNumber}`,
    );

    return {
        checkpointId,
        checkpointNumber,
        checkpointName,
    };
}

// Converts positional API rows, including the legacy extra unsubmitted column.
function normalizeStudentAndCell(
    userIdRaw: string,
    rawRow: unknown,
    item: DashboardItem,
): {
    student: StudentSummary;
    cell: ProgressCell;
} | null {
    if (!Array.isArray(rawRow)) {
        return null;
    }

    const userId = asNumber(userIdRaw, 0);
    if (userId <= 0 && userId !== TEST_USER_ID) {
        return null;
    }

    const lastName = asString(rawRow[0]);
    const firstName = asString(rawRow[1]);
    const lecture = asString(rawRow[2]);
    const lab = asString(rawRow[3]);
    const attempts = normalizeAttempts(rawRow[4]);
    const lastSubmitted = asString(rawRow[5]);
    const passed = isPassingValue(rawRow[6]);
    const submissionId = isRealSubmissionId(rawRow[7]) ? asNumber(rawRow[7]) : null;

    const offset = rawRow[7] === "N/A" ? 1 : 0;
    const grade = asString(rawRow[9 + offset], "0");
    const studentNumber = asString(rawRow[10 + offset]);
    const isLocked = isPassingValue(rawRow[11 + offset]) === true;
    const skipped = isPassingValue(rawRow[12 + offset]) === true;

    return {
        student: {
            userId,
            firstName,
            lastName,
            fullName: `${firstName} ${lastName}`.trim() || `Student ${userId}`,
            studentNumber,
            lecture,
            lab,
            isLocked,
        },
        cell: {
            studentUserId: userId,
            itemId: item.id,
            item,
            attempts,
            lastSubmitted,
            passed,
            submissionId,
            grade,
            skipped,
        },
    };
}

// Classifies assignment progress as skipped, complete, attempted, or not started.
function cellStatus(
    cell: ProgressCell | undefined,
): "complete" | "skipped" | "in-progress" | "not-started" {
    if (!cell) {
        return "not-started";
    }

    if (cell.skipped) {
        return "skipped";
    }

    if (cell.attempts <= 0 || !cell.submissionId) {
        return "not-started";
    }

    if (cell.passed === true) {
        return "complete";
    }

    return "in-progress";
}

// Helper for status label used by this component.
function statusLabel(status: ReturnType<typeof cellStatus>): string {
    if (status === "complete") {
        return "Complete";
    }

    if (status === "skipped") {
        return "Skipped";
    }

    if (status === "in-progress") {
        return "Attempted";
    }

    return "Not started";
}

// Helper for status icon used by this component.
function statusIcon(status: ReturnType<typeof cellStatus>) {
    if (status === "complete" || status === "skipped") {
        // Renders the interface using the current data and interaction state.
        return <FaCheckCircle aria-hidden="true" />;
    }

    if (status === "in-progress") {
        // Renders the interface using the current data and interaction state.
        return <FaExclamationTriangle aria-hidden="true" />;
    }

    // Renders the interface using the current data and interaction state.
    return <FaTimesCircle aria-hidden="true" />;
}

// Sorts students by last name, then first name, in the selected direction.
function compareStudents(a: StudentProgressRow, b: StudentProgressRow, sortMode: SortMode) {
    if (a.userId === TEST_USER_ID || b.userId === TEST_USER_ID) {
        return a.userId === b.userId ? 0 : a.userId === TEST_USER_ID ? -1 : 1;
    }
    const lastCompare = a.lastName.localeCompare(b.lastName);
    const firstCompare = a.firstName.localeCompare(b.firstName);

    if (sortMode === "last-desc") {
        return lastCompare !== 0 ? -lastCompare : -firstCompare;
    }

    return lastCompare !== 0 ? lastCompare : firstCompare;
}

// Converts the hidden-module map to numeric IDs and removes duplicates.
function normalizeHiddenModuleMap(value: Record<string, unknown> | undefined): Record<number, number[]> {
    const normalized: Record<number, number[]> = {};

    Object.entries(value || {}).forEach(([userIdRaw, moduleIdsRaw]) => {
        const userId = asNumber(userIdRaw, 0);

        if (userId <= 0 || !Array.isArray(moduleIdsRaw)) {
            return;
        }

        const moduleIds = moduleIdsRaw
            .map((moduleId) => asNumber(moduleId, 0))
            .filter((moduleId) => moduleId > 0);

        if (moduleIds.length > 0) {
            normalized[userId] = Array.from(new Set(moduleIds));
        }
    });

    return normalized;
}

// Builds the class progress dashboard, including filters and module visibility controls.
export default function AdminAnalyticsDashboard() {
    // Reads the school, class, or assignment identifiers from the current route.
    const { school_id, class_id } = useParams<RouteParams>();

    const schoolId = school_id || "";
    const classId = class_id || "";

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [className, setClassName] = useState("");
    const [items, setItems] = useState<DashboardItem[]>([]);
    const [students, setStudents] = useState<StudentProgressRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [searchText, setSearchText] = useState("");
    const [lectureFilter, setLectureFilter] = useState("all");
    const [labFilter, setLabFilter] = useState("all");
    const [sortMode, setSortMode] = useState<SortMode>("last-asc");
    const [hoveredStudentId, setHoveredStudentId] = useState<number | null>(null);
    const [hoveredItemId, setHoveredItemId] = useState<string | null>(null);
    const [studentHiddenModuleIds, setStudentHiddenModuleIds] = useState<Record<number, number[]>>({});
    // Keeps table scroll ref available across renders without triggering a state update.
    const tableScrollRef = useRef<HTMLDivElement | null>(null);
    // Keeps bottom scroll ref available across renders without triggering a state update.
    const bottomScrollRef = useRef<HTMLDivElement | null>(null);
    // Keeps the values that drive this component’s display and user interactions in React state.
    const [bottomScrollWidth, setBottomScrollWidth] = useState(0);

    // Loads or refreshes view data when the dependencies below change.
    useEffect(() => {
        let cancelled = false;

        // Loads class name for this view.
        async function loadClassName() {
            const token = localStorage.getItem("AUTOTA_AUTH_TOKEN");

            if (!schoolId || !classId || !token) {
                setClassName("");
                return;
            }

            try {
                // Fetches the server data needed for this operation.
                const classResponse = await axios.get<ClassAccessResponse>(
                    `${API_URL}/classes/validate_class_access/${classId}`,
                    {
                        headers: authHeaders(),
                        params: {
                            school_id: schoolId,
                            role_context: "admin",
                        },
                    },
                );

                if (!cancelled) {
                    setClassName(classResponse.data?.name || "");
                }
            } catch (err) {
                console.error(err);

                if (!cancelled) {
                    setClassName("");
                }
            }
        }

        // Loads dashboard for this view.
        async function loadDashboard() {
            setLoading(true);
            setError("");

            const token = localStorage.getItem("AUTOTA_AUTH_TOKEN");

            if (!classId || !token) {
                setItems([]);
                setStudents([]);
                setStudentHiddenModuleIds({});
                setLoading(false);
                setError("You must be logged in to view the analytics dashboard.");
                return;
            }

            try {
                // Fetches the server data needed for this operation.
                const dashboardResponse = await axios.get(
                    `${API_URL}/analytics-dashboard/analytics_dashboard`,
                    {
                        headers: authHeaders(),
                        params: { class_id: classId, include_test_user: true },
                    },
                );

                const payload = dashboardResponse.data as AnalyticsDashboardPayload;

                const modules = Array.isArray(payload.modules) ? payload.modules : [];
                const projects = Array.isArray(payload.projects) ? payload.projects : [];
                const checkpointsByProjectId = payload.checkpointsByProjectId || {};
                const submissionsByItemId = payload.submissionsByItemId || {};
                const hiddenModulesByStudentId = normalizeHiddenModuleMap(
                    payload.hiddenModulesByStudentId,
                );

                const moduleById = new Map<number, RawModule>();
                modules.forEach((module) => {
                    if (Number(module.Id) > 0) {
                        moduleById.set(Number(module.Id), module);
                    }
                });

                const orderedProjects = [...projects].sort((a, b) => {
                    const moduleA = asNumber(a.ModuleId, 0);
                    const moduleB = asNumber(b.ModuleId, 0);

                    if (moduleA !== moduleB) {
                        return moduleA - moduleB;
                    }

                    return asNumber(a.Id) - asNumber(b.Id);
                });

                const dashboardItems: DashboardItem[] = [];
                let lastModuleId: number | null = null;

                orderedProjects.forEach((project) => {
                    const projectId = asNumber(project.Id, 0);

                    if (projectId <= 0) {
                        return;
                    }

                    const moduleId = asNumber(project.ModuleId, 0);
                    const module = moduleById.get(moduleId);
                    const moduleName = module?.Name || "Unassigned Module";
                    const projectName = project.Name || "Assignment";
                    const isFirstProjectInModule = lastModuleId !== moduleId;
                    const checkpointRows = checkpointsByProjectId[String(projectId)] || [];

                    const checkpoints = checkpointRows
                        .map((row, index) => normalizeCheckpoint(row, index))
                        .filter((row): row is NonNullable<typeof row> => row !== null);

                    checkpoints.forEach((checkpoint, index) => {
                        dashboardItems.push({
                            id: `checkpoint-${projectId}-${checkpoint.checkpointId}`,
                            kind: "checkpoint",
                            projectId,
                            projectName,
                            moduleId,
                            moduleName,
                            isFirstInModule: isFirstProjectInModule && index === 0,
                            checkpointId: checkpoint.checkpointId,
                            checkpointNumber: checkpoint.checkpointNumber,
                            checkpointName: checkpoint.checkpointName,
                            label: `${moduleName}: ${checkpoint.checkpointName}`,
                            shortLabel: `CP ${checkpoint.checkpointNumber}`,
                        });
                    });

                    dashboardItems.push({
                        id: `main-${projectId}`,
                        kind: "main",
                        projectId,
                        projectName,
                        moduleId,
                        moduleName,
                        isFirstInModule: isFirstProjectInModule && checkpoints.length === 0,
                        label: `${moduleName}: Main Program`,
                        shortLabel: "Main Program",
                    });

                    lastModuleId = moduleId;
                });

                const studentMap = new Map<number, StudentSummary>();
                const cellMap = new Map<number, Record<string, ProgressCell>>();

                dashboardItems.forEach((item) => {
                    const rows = submissionsByItemId[item.id] || {};

                    Object.entries(rows).forEach(([userIdRaw, rawRow]) => {
                        const normalized = normalizeStudentAndCell(
                            userIdRaw,
                            rawRow,
                            item,
                        );

                        if (!normalized) {
                            return;
                        }

                        const { student, cell } = normalized;

                        studentMap.set(student.userId, {
                            ...studentMap.get(student.userId),
                            ...student,
                        });

                        const existingCells = cellMap.get(student.userId) || {};
                        existingCells[item.id] = cell;
                        cellMap.set(student.userId, existingCells);
                    });
                });

                const rows: StudentProgressRow[] = Array.from(studentMap.values())
                    .sort((a, b) => {
                        const lastCompare = a.lastName.localeCompare(b.lastName);

                        if (lastCompare !== 0) {
                            return lastCompare;
                        }

                        return a.firstName.localeCompare(b.firstName);
                    })
                    .map((student) => {
                        const cells = cellMap.get(student.userId) || {};
                        let completed = 0;
                        let attempted = 0;
                        for (const item of dashboardItems) {
                            const status = cellStatus(cells[item.id]);
                            if (status === "complete" || status === "skipped") completed++;
                            if (status !== "not-started") attempted++;
                        }
                        const total = dashboardItems.length;
                        const percentComplete = total > 0
                            ? Math.round((completed / total) * 100)
                            : 0;

                        return {
                            ...student,
                            cells,
                            completed,
                            attempted,
                            total,
                            percentComplete,
                        };
                    });

                if (!cancelled) {
                    setItems(dashboardItems);
                    setStudents(rows);
                    setStudentHiddenModuleIds(hiddenModulesByStudentId);
                }
            } catch (err) {
                if (!cancelled) {
                    console.error(err);
                    setError("Could not load the analytics dashboard.");
                }
            } finally {
                if (!cancelled) {
                    setLoading(false);
                }
            }
        }

        loadClassName();
        loadDashboard();

        return () => {
            cancelled = true;
        };
    }, [schoolId, classId]);

    const realStudents = useMemo(() => students.filter((student) => student.userId !== TEST_USER_ID), [students]);

    // Recomputes lecture options only when its dependencies change.
    const lectureOptions = useMemo(() => {
        return Array.from(
            new Set(realStudents.map((student) => student.lecture).filter(Boolean)),
        ).sort((a, b) => a.localeCompare(b));
    }, [realStudents]);

    // Recomputes lab options only when its dependencies change.
    const labOptions = useMemo(() => {
        return Array.from(
            new Set(realStudents.map((student) => student.lab).filter(Boolean)),
        ).sort((a, b) => a.localeCompare(b));
    }, [realStudents]);

    // Recomputes filtered students only when its dependencies change.
    const filteredStudents = useMemo(() => {
        const normalizedSearch = searchText.trim().toLowerCase();

        return students
            .filter((student) => {
                const matchesSearch =
                    !normalizedSearch ||
                    student.fullName.toLowerCase().includes(normalizedSearch);

                if (!matchesSearch) {
                    return false;
                }

                if (student.userId === TEST_USER_ID) return true;

                if (lectureFilter !== "all" && student.lecture !== lectureFilter) {
                    return false;
                }

                if (labFilter !== "all" && student.lab !== labFilter) {
                    return false;
                }

                return true;
            })
            .sort((a, b) => compareStudents(a, b, sortMode));
    }, [students, searchText, lectureFilter, labFilter, sortMode]);

    // Recomputes modules for header only when its dependencies change.
    const modulesForHeader = useMemo(() => {
        const groups: {
            moduleId: number;
            moduleName: string;
            span: number;
        }[] = [];

        items.forEach((item) => {
            const existing = groups[groups.length - 1];

            if (existing && existing.moduleId === item.moduleId) {
                existing.span += 1;
            } else {
                groups.push({
                    moduleId: item.moduleId,
                    moduleName: item.moduleName,
                    span: 1,
                });
            }
        });

        return groups;
    }, [items]);

    // Recomputes module visibility options only when its dependencies change.
    const moduleVisibilityOptions = useMemo<ModuleVisibilityOption[]>(() => {
        return modulesForHeader.map((module) => ({
            moduleId: module.moduleId,
            moduleName: module.moduleName,
            itemCount: module.span,
        }));
    }, [modulesForHeader]);

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        const validModuleIds = new Set(
            moduleVisibilityOptions.map((module) => module.moduleId),
        );
        const validStudentIds = new Set(realStudents.map((student) => student.userId));

        setStudentHiddenModuleIds((current) => {
            const next: Record<number, number[]> = {};

            Object.entries(current).forEach(([userIdRaw, moduleIds]) => {
                const userId = Number(userIdRaw);

                if (!validStudentIds.has(userId)) {
                    return;
                }

                const validHiddenModules = moduleIds.filter((moduleId) => (
                    validModuleIds.has(moduleId)
                ));

                if (validHiddenModules.length > 0) {
                    next[userId] = validHiddenModules;
                }
            });

            return next;
        });
    }, [moduleVisibilityOptions, realStudents]);

    // Registers browser listeners and cleans them up when this effect reruns or the component unmounts.
    useEffect(() => {
        // Measures the table width so the bottom scrollbar tracks the same content.
        function updateBottomScrollWidth() {
            if (!tableScrollRef.current) {
                setBottomScrollWidth(0);
                return;
            }

            setBottomScrollWidth(tableScrollRef.current.scrollWidth);
        }

        updateBottomScrollWidth();

        window.addEventListener("resize", updateBottomScrollWidth);

        const resizeObserver = new ResizeObserver(updateBottomScrollWidth);

        if (tableScrollRef.current) {
            resizeObserver.observe(tableScrollRef.current);
        }

        return () => {
            window.removeEventListener("resize", updateBottomScrollWidth);
            resizeObserver.disconnect();
        };
    }, [items, filteredStudents.length, studentHiddenModuleIds]);

    // Synchronizes table scroll for this view.
    function syncTableScroll() {
        const table = tableScrollRef.current;
        const bottom = bottomScrollRef.current;

        if (!table || !bottom) {
            return;
        }

        if (bottom.scrollLeft !== table.scrollLeft) {
            bottom.scrollLeft = table.scrollLeft;
        }
    }

    // Synchronizes bottom scroll for this view.
    function syncBottomScroll() {
        if (!tableScrollRef.current || !bottomScrollRef.current) {
            return;
        }

        if (tableScrollRef.current.scrollLeft !== bottomScrollRef.current.scrollLeft) {
            tableScrollRef.current.scrollLeft = bottomScrollRef.current.scrollLeft;
        }
    }

    // Helper for grade path used by this component.
    function gradePath(cell: ProgressCell): string {
        const sourceQuery = "?from=analytics";

        if (cell.item.kind === "checkpoint") {
            return `/admin/school/${schoolId}/class/${classId}/module/${cell.item.moduleId}/project/${cell.item.projectId}/checkpoint/${cell.item.checkpointId}/grade/${cell.submissionId}${sourceQuery}`;
        }

        return `/admin/school/${schoolId}/class/${classId}/module/${cell.item.moduleId}/project/${cell.item.projectId}/grade/${cell.submissionId}${sourceQuery}`;
    }

    // Helper for view path used by this component.
    function viewPath(cell: ProgressCell): string {
        const sourceQuery = "?from=analytics";

        if (cell.item.kind === "checkpoint") {
            return `/admin/school/${schoolId}/class/${classId}/module/${cell.item.moduleId}/project/${cell.item.projectId}/checkpoint/${cell.item.checkpointId}/codeview/${cell.submissionId}${sourceQuery}`;
        }

        return `/admin/school/${schoolId}/class/${classId}/module/${cell.item.moduleId}/project/${cell.item.projectId}/codeview/${cell.submissionId}${sourceQuery}`;
    }

    // Helper for submissions path used by this component.
    function submissionsPath(item: DashboardItem): string {
        if (item.kind === "checkpoint") {
            return `/admin/school/${schoolId}/class/${classId}/module/${item.moduleId}/project/${item.projectId}/checkpoint/${item.checkpointId}/submissions`;
        }

        return `/admin/school/${schoolId}/class/${classId}/module/${item.moduleId}/project/${item.projectId}/submissions`;
    }

    // Checks grade for this view.
    function hasGrade(cell: ProgressCell | undefined): boolean {
        const grade = cell?.grade?.trim();

        return Boolean(
            grade &&
            grade !== "0" &&
            grade.toUpperCase() !== "N/A",
        );
    }

    // Checks student module hidden for this view.
    function isStudentModuleHidden(userId: number, moduleId: number): boolean {
        if (userId === TEST_USER_ID) return false;
        return studentHiddenModuleIds[userId]?.includes(moduleId) ?? false;
    }

    // Checks module hidden for everyone for this view.
    function isModuleHiddenForEveryone(moduleId: number): boolean {
        return realStudents.length > 0 && realStudents.every((student) => (
            isStudentModuleHidden(student.userId, moduleId)
        ));
    }

    // Confirms visibility change for this view.
    function confirmVisibilityChange(message: string): boolean {
        return window.confirm(message);
    }

    // Toggles student module visibility for this view.
    async function toggleStudentModuleVisibility(
        userId: number,
        moduleId: number,
        studentName: string,
        moduleName: string,
    ) {
        if (userId === TEST_USER_ID) return;
        const currentlyHidden = isStudentModuleHidden(userId, moduleId);
        const nextHidden = !currentlyHidden;
        const action = currentlyHidden ? "show" : "hide";
        const confirmed = confirmVisibilityChange(
            [
                "Change module visibility?",
                `This will ${action} ${moduleName} for ${studentName}.`,
            ].join("\n"),
        );

        if (!confirmed) {
            return;
        }

        try {
            await axios.post(
                `${API_URL}/assignment_permissions/set_student_module_visibility`,
                {
                    class_id: classId,
                    student_id: userId,
                    module_id: moduleId,
                    hidden: nextHidden,
                },
                { headers: authHeaders() },
            );

            setStudentHiddenModuleIds((current) => {
                const existingHiddenModules = current[userId] || [];
                const nextHiddenModules = nextHidden
                    ? Array.from(new Set([...existingHiddenModules, moduleId]))
                    : existingHiddenModules.filter((id) => id !== moduleId);

                const next = { ...current };

                if (nextHiddenModules.length > 0) {
                    next[userId] = nextHiddenModules;
                } else {
                    delete next[userId];
                }

                return next;
            });
        } catch (err) {
            console.error(err);
            setError("Could not update module visibility.");
        }
    }

    // Updates module visibility for all for this view.
    async function setModuleVisibilityForAll(
        moduleId: number,
        hidden: boolean,
        moduleName: string,
    ) {
        const action = hidden ? "hide" : "show";
        const confirmed = confirmVisibilityChange(
            [
                "Change module visibility?",
                `This will ${action} ${moduleName} for every student.`,
            ].join("\n"),
        );

        if (!confirmed) {
            return;
        }

        try {
            // Sends this operation and its payload to the server.
            const response = await axios.post(
                `${API_URL}/assignment_permissions/set_module_visibility_for_all`,
                {
                    class_id: classId,
                    module_id: moduleId,
                    hidden,
                },
                { headers: authHeaders() },
            );
            const responseStudentIds = Array.isArray(response.data?.studentIds)
                ? response.data.studentIds
                    .map((value: unknown) => asNumber(value, 0))
                    .filter((value: number) => value > 0)
                : [];
            const affectedStudentIds = responseStudentIds.length > 0
                ? responseStudentIds
                : realStudents.map((student) => student.userId);

            setStudentHiddenModuleIds((current) => {
                const next: Record<number, number[]> = { ...current };

                affectedStudentIds.forEach((studentId) => {
                    const existingHiddenModules = next[studentId] || [];
                    const alreadyHidden = existingHiddenModules.includes(moduleId);

                    if (hidden && !alreadyHidden) {
                        next[studentId] = [...existingHiddenModules, moduleId];
                    }

                    if (!hidden && alreadyHidden) {
                        const nextHiddenModules = existingHiddenModules.filter((id) => id !== moduleId);

                        if (nextHiddenModules.length > 0) {
                            next[studentId] = nextHiddenModules;
                        } else {
                            delete next[studentId];
                        }
                    }
                });

                return next;
            });
        } catch (err) {
            console.error(err);
            setError("Could not update module visibility.");
        }
    }

    // Helper for visible module count for student used by this component.
    function visibleModuleCountForStudent(student: StudentProgressRow): number {
        return moduleVisibilityOptions.filter((module) => (
            !isStudentModuleHidden(student.userId, module.moduleId)
        )).length;
    }

    // Recomputes modules hidden for everyone count only when its dependencies change.
    const modulesHiddenForEveryoneCount = useMemo(() => {
        return moduleVisibilityOptions.filter((module) => (
            realStudents.length > 0 && realStudents.every((student) => (
                studentHiddenModuleIds[student.userId]?.includes(module.moduleId) ?? false
            ))
        )).length;
    }, [moduleVisibilityOptions, realStudents, studentHiddenModuleIds]);

    // Renders student visibility controls for this view.
    function renderStudentVisibilityControls(student: StudentProgressRow) {
        if (student.userId === TEST_USER_ID) return null;
        const visibleCount = visibleModuleCountForStudent(student);

        if (moduleVisibilityOptions.length === 0) {
            return null;
        }

        // Renders the interface using the current data and interaction state.
        return (
            <details className="analytics-student-visibility">
                <summary className="analytics-student-visibility-summary">
                    <span className="analytics-student-visibility-label">
                        Module access
                    </span>

                    <span className="analytics-visibility-count">
                        {visibleCount}/{moduleVisibilityOptions.length} visible
                    </span>
                </summary>

                <div className="analytics-student-visibility-panel">
                    <div
                        className="analytics-student-module-list"
                        role="group"
                        aria-label={`Module access for ${student.fullName}`}
                    >
                        {moduleVisibilityOptions.map((module) => {
                            const hidden = isStudentModuleHidden(
                                student.userId,
                                module.moduleId,
                            );

                            // Renders the interface using the current data and interaction state.
                            return (
                                <button
                                    type="button"
                                    className={[
                                        "analytics-module-access-toggle",
                                        hidden ? "analytics-module-access-toggle-hidden" : "",
                                    ]
                                        .join(" ")
                                        .trim()}
                                    key={`${student.userId}-${module.moduleId}`}
                                    onClick={() => toggleStudentModuleVisibility(
                                        student.userId,
                                        module.moduleId,
                                        student.fullName,
                                        module.moduleName,
                                    )}
                                    aria-pressed={!hidden}
                                    title={
                                        hidden
                                            ? "Click to show this module for this student."
                                            : "Click to hide this module for this student."
                                    }
                                >
                                    <span className="analytics-module-access-icon">
                                        {hidden ? (
                                            <FaEyeSlash aria-hidden="true" />
                                        ) : (
                                            <FaEye aria-hidden="true" />
                                        )}
                                    </span>

                                    <span className="analytics-module-access-name">
                                        {module.moduleName}
                                    </span>

                                    <span className="analytics-module-access-state">
                                        {hidden ? "Hidden" : "Visible"}
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                </div>
            </details>
        );
    }

    // Renders progress cell for this view.
    function renderProgressCell(student: StudentProgressRow, item: DashboardItem) {
        const cell = student.cells[item.id];
        const status = cellStatus(cell);
        const isHoveredRow = hoveredStudentId === student.userId;
        const isHoveredColumn = hoveredItemId === item.id;
        const isHoveredIntersection = isHoveredRow && isHoveredColumn;
        const cellHasGrade = hasGrade(cell);
        const isHidden = isStudentModuleHidden(student.userId, item.moduleId);

        // Renders the interface using the current data and interaction state.
        return (
            <td
                className={[
                    "analytics-cell",
                    `analytics-cell-${status}`,
                    status === "skipped" ? "analytics-cell-complete" : "",
                    item.isFirstInModule ? "analytics-module-start" : "",
                    isHoveredColumn ? "analytics-column-highlight" : "",
                    isHoveredIntersection ? "analytics-intersection-highlight" : "",
                    isHidden ? "analytics-cell-module-hidden" : "",
                ]
                    .join(" ")
                    .trim()}
                key={item.id}
                onMouseEnter={() => {
                    setHoveredStudentId(student.userId);
                    setHoveredItemId(item.id);
                }}
            >
                <div className="analytics-cell-card">
                    {isHidden ? (
                        <div className="analytics-cell-visibility-note">
                            <FaEyeSlash aria-hidden="true" />
                            <span>Hidden</span>
                        </div>
                    ) : null}

                    <div className="analytics-cell-status">
                        {statusIcon(status)}
                        <span>{statusLabel(status)}</span>
                    </div>

                    <div className="analytics-cell-meta">
                        {status === "skipped" ? (
                            <span>Skipped with stars</span>
                        ) : cell?.attempts ? (
                            <span>
                                {cell.attempts} attempt{cell.attempts === 1 ? "" : "s"}
                            </span>
                        ) : (
                            <span>No attempts</span>
                        )}

                        {status === "skipped" && cell?.attempts ? (
                            <span>
                                {cell.attempts} attempt{cell.attempts === 1 ? "" : "s"} before skip
                            </span>
                        ) : null}

                        {cell?.lastSubmitted && cell.lastSubmitted !== "N/A" ? (
                            <span>{formatDateTime(cell.lastSubmitted)}</span>
                        ) : null}

                        {status !== "skipped" && cellHasGrade ? (
                            <span>Grade: {cell?.grade}</span>
                        ) : status !== "skipped" && cell?.submissionId ? (
                            <span>No grade yet</span>
                        ) : null}
                    </div>

                    <div className="analytics-cell-actions">
                        {cell?.submissionId ? (
                            <>
                                <Link to={viewPath(cell)} title="View submission">
                                    <FaEye aria-hidden="true" />
                                    <span>View</span>
                                </Link>

                                <Link
                                    to={gradePath(cell)}
                                    title={cellHasGrade ? "Open regrading" : "Open grading"}
                                >
                                    <FaClipboardCheck aria-hidden="true" />
                                    <span>{cellHasGrade ? "Regrade" : "Grade"}</span>
                                </Link>
                            </>
                        ) : status !== "not-started" && status !== "skipped" ? (
                            <Link to={submissionsPath(item)} title="Open submissions">
                                <FaFolderOpen aria-hidden="true" />
                                <span>Submissions</span>
                            </Link>
                        ) : null}
                    </div>
                </div>
            </td>
        );
    }

    // Renders the interface using the current data and interaction state.
    return (
        <div className="projects-page admin-analytics-page">
            {/* Sets the page title and document metadata. */}
            <Helmet>
                <title>[Admin] MAAT</title>
            </Helmet>

            {/* Shows progress while this view is waiting for data. */}
            <LoadingAnimation
                show={loading}
                message="Loading analytics dashboard..."
            />

            {/* Displays the navigation and actions available on this page. */}
            <MenuComponent
            />

            {/* Shows the current location and links back to parent pages. */}
            <DirectoryBreadcrumbs
                items={[
                    { label: "School Selection", to: "/schools" },
                    {
                        label: "Class Selection",
                        to: schoolId
                            ? `/admin/school/${schoolId}/classes`
                            : "/schools",
                    },
                    {
                        label: "Admin Menu",
                        to: `/admin/school/${schoolId}/class/${classId}/menu`,
                    },
                    { label: "Analytics Dashboard" },
                ]}
                trailingSeparator={true}
            />

            <div className="pageTitle">
                {className ? `${className} Analytics Dashboard` : "Analytics Dashboard"}
            </div>

            <p className="analytics-subtitle">
                View each student's progress across every module checkpoint and main program in this class.
                Use the visibility controls to preview which modules students can see.
            </p>

            {error ? (
                <section className="analytics-error" role="alert">
                    <FaExclamationTriangle aria-hidden="true" />
                    <span>{error}</span>
                </section>
            ) : (
                <>
                    <section className="analytics-toolbar" aria-label="Dashboard filters">
                        <label className="analytics-search">
                            <FaSearch aria-hidden="true" />
                            <input
                                type="search"
                                value={searchText}
                                onChange={(event) => setSearchText(event.target.value)}
                                placeholder="Search student names"
                            />
                        </label>

                        <label className="analytics-filter">
                            <FaFilter aria-hidden="true" />
                            <select
                                value={lectureFilter}
                                onChange={(event) => setLectureFilter(event.target.value)}
                            >
                                <option value="all">All lectures</option>
                                {lectureOptions.map((lecture) => (
                                    <option value={lecture} key={lecture}>
                                        Lecture: {lecture}
                                    </option>
                                ))}
                            </select>
                        </label>

                        <label className="analytics-filter">
                            <FaFilter aria-hidden="true" />
                            <select
                                value={labFilter}
                                onChange={(event) => setLabFilter(event.target.value)}
                            >
                                <option value="all">All labs</option>
                                {labOptions.map((lab) => (
                                    <option value={lab} key={lab}>
                                        Lab: {lab}
                                    </option>
                                ))}
                            </select>
                        </label>

                        <label className="analytics-filter analytics-sort-filter">
                            <FaSortAlphaDown aria-hidden="true" />
                            <select
                                value={sortMode}
                                onChange={(event) => setSortMode(event.target.value as SortMode)}
                            >
                                <option value="last-asc">Last name A-Z</option>
                                <option value="last-desc">Last name Z-A</option>
                            </select>
                        </label>
                    </section>

                    {items.length === 0 && !loading ? (
                        <section className="analytics-empty">
                            No modules, checkpoints, or main programs were found for this class.
                        </section>
                    ) : filteredStudents.length === 0 && !loading ? (
                        <section className="analytics-empty">
                            No students match the current filters.
                        </section>
                    ) : (
                        <section className="analytics-table-shell" aria-label="Student progress table">
                            <div
                                className="analytics-table-scroll"
                                ref={tableScrollRef}
                                onScroll={syncTableScroll}
                            >
                                <table className="analytics-table">
                                    <thead>
                                        <tr className="analytics-module-row">
                                            <th className="analytics-student-heading" rowSpan={2}>
                                                <div className="analytics-student-heading-main">
                                                    Student
                                                </div>

                                                <div className="analytics-global-visibility-summary">
                                                    <FaUsers aria-hidden="true" />
                                                    <span>
                                                        {modulesHiddenForEveryoneCount} hidden for everyone
                                                    </span>
                                                </div>
                                            </th>

                                            {modulesForHeader.map((module) => {
                                                const hiddenForEveryone = isModuleHiddenForEveryone(module.moduleId);

                                                // Renders the interface using the current data and interaction state.
                                                return (
                                                    <th
                                                        className={[
                                                            "analytics-module-heading",
                                                            hiddenForEveryone ? "analytics-module-heading-hidden" : "",
                                                        ]
                                                            .join(" ")
                                                            .trim()}
                                                        colSpan={module.span}
                                                        key={`${module.moduleId}-${module.moduleName}`}
                                                    >
                                                        <div className="analytics-module-heading-inner">
                                                            <span className="analytics-module-heading-name">
                                                                {module.moduleName}
                                                            </span>

                                                            <button
                                                                type="button"
                                                                className={[
                                                                    "analytics-global-module-toggle",
                                                                    hiddenForEveryone ? "analytics-global-module-toggle-hidden" : "",
                                                                ]
                                                                    .join(" ")
                                                                    .trim()}
                                                                onClick={() => setModuleVisibilityForAll(
                                                                    module.moduleId,
                                                                    !hiddenForEveryone,
                                                                    module.moduleName,
                                                                )}
                                                                aria-pressed={!hiddenForEveryone}
                                                                title={
                                                                    hiddenForEveryone
                                                                        ? "Click to show this module for everyone."
                                                                        : "Click to hide this module for everyone."
                                                                }
                                                            >
                                                                {hiddenForEveryone ? (
                                                                    <FaEyeSlash aria-hidden="true" />
                                                                ) : (
                                                                    <FaEye aria-hidden="true" />
                                                                )}
                                                                <span>
                                                                    {hiddenForEveryone ? "Show all" : "Hide all"}
                                                                </span>
                                                            </button>
                                                        </div>
                                                    </th>
                                                );
                                            })}
                                        </tr>

                                        <tr className="analytics-item-row">
                                            {items.map((item) => {
                                                const hiddenForEveryone = isModuleHiddenForEveryone(item.moduleId);

                                                // Renders the interface using the current data and interaction state.
                                                return (
                                                    <th
                                                        className={[
                                                            "analytics-item-heading",
                                                            `analytics-item-heading-${item.kind}`,
                                                            item.isFirstInModule ? "analytics-module-start" : "",
                                                            hoveredItemId === item.id ? "analytics-column-header-highlight" : "",
                                                            hiddenForEveryone ? "analytics-item-heading-module-hidden" : "",
                                                        ]
                                                            .join(" ")
                                                            .trim()}
                                                        key={item.id}
                                                        title={item.label}
                                                        onMouseEnter={() => setHoveredItemId(item.id)}
                                                        onMouseLeave={() => setHoveredItemId(null)}
                                                    >
                                                        <span>{item.shortLabel}</span>

                                                        {hiddenForEveryone ? (
                                                            <span className="analytics-item-hidden-pill">
                                                                Hidden
                                                            </span>
                                                        ) : null}
                                                    </th>
                                                );
                                            })}
                                        </tr>
                                    </thead>

                                    <tbody>
                                        {filteredStudents.map((student) => (
                                            <tr
                                                key={student.userId}
                                                className={hoveredStudentId === student.userId ? "analytics-row-highlight" : ""}
                                                onMouseEnter={() => setHoveredStudentId(student.userId)}
                                                onMouseLeave={() => {
                                                    setHoveredStudentId(null);
                                                    setHoveredItemId(null);
                                                }}
                                            >
                                                <th className="analytics-student-cell" scope="row">
                                                    <div className="analytics-student-name-row">
                                                        <span className="analytics-student-name">
                                                            {student.fullName}{student.userId === TEST_USER_ID ? " (admin testing)" : ""}
                                                        </span>

                                                        {student.isLocked ? (
                                                            <span className="analytics-locked-pill">
                                                                Locked
                                                            </span>
                                                        ) : null}
                                                    </div>

                                                    <div className="analytics-student-meta">
                                                        {student.studentNumber ? (
                                                            <span>ID: {student.studentNumber}</span>
                                                        ) : null}

                                                        {student.lecture ? (
                                                            <span>Lecture: {student.lecture}</span>
                                                        ) : null}

                                                        {student.lab ? (
                                                            <span>Lab: {student.lab}</span>
                                                        ) : null}
                                                    </div>

                                                    {renderStudentVisibilityControls(student)}
                                                </th>

                                                {items.map((item) => renderProgressCell(student, item))}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>

                            <div
                                className="analytics-bottom-scrollbar"
                                ref={bottomScrollRef}
                                onScroll={syncBottomScroll}
                                aria-hidden="true"
                            >
                                <div
                                    className="analytics-bottom-scrollbar-spacer"
                                    style={{ width: `${bottomScrollWidth}px` }}
                                />
                            </div>
                        </section>
                    )}
                </>
            )}
        </div>
    );
}
