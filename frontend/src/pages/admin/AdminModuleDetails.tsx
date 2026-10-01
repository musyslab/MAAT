// AdminModuleDetails.tsx: Manages a module overview, its main project, checkpoints, and presentation files.
import {
    useCallback,
    useEffect,
    useLayoutEffect,
    useMemo,
    useRef,
    useState,
} from "react";
import axios from "axios";
import { Helmet } from "react-helmet";
import { Link, useParams } from "react-router-dom";
import {
    FaCheck,
    FaCheckCircle,
    FaDownload,
    FaEdit,
    FaExclamationCircle,
    FaExclamationTriangle,
    FaEye,
    FaFilePowerpoint,
    FaFlagCheckered,
    FaGripVertical,
    FaPlus,
    FaPlay,
    FaSave,
    FaTasks,
    FaTimes,
    FaTrash,
    FaUpload,
    FaWrench,
} from "react-icons/fa";

import MenuComponent from "../components/MenuComponent";
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs";
import "../../styling/AdminModuleDetails.scss";

// Describes the module object data expected by this file.
interface ModuleObject {
    Id: number;
    ClassId: number;
    Name: string;
    Start: string;
    End: string;
    MainProjectId?: number;
    HasPresentation?: boolean;
    PresentationFileName?: string;
}

// Describes the class access response data expected by this file.
interface ClassAccessResponse {
    id?: number;
    name?: string;
    school_id?: number;
    school_name?: string;
}

// Describes the project object data expected by this file.
interface ProjectObject {
    Id: number;
    Name: string;
    Start: string;
    End: string;
    TotalSubmissions: number;
    PracticeTotalSubmissions?: number;
    PracticeProblemsEnabled?: boolean;
    HasSolutionProgram?: boolean;
    HasTestcases?: boolean;
    TestcaseCount?: number;
}

// Describes the practice problem row data expected by this file.
type PracticeProblemRow = {
    id: number;
    number: number;
    name: string;
    enabled: boolean;
    submissions?: number;
    hasSolutionProgram?: boolean;
    hasTestcases?: boolean;
    testcaseCount?: number;
};

// Describes the project setup status data expected by this file.
type ProjectSetupStatus = {
    hasSolutionProgram: boolean;
    hasTestcases: boolean;
    testcaseCount: number;
};

// Describes the module overview response data expected by this file.
type ModuleOverviewResponse = {
    module: ModuleObject;
    project: ProjectObject;
    checkpoints?: PracticeProblemRow[];
    practiceProblems?: PracticeProblemRow[];
};

// Describes the path segment data expected by this file.
type PathSegment = {
    key: string;
    d: string;
    state: "complete" | "missing" | "incomplete";
};

// Describes the path svg state data expected by this file.
type PathSvgState = {
    width: number;
    height: number;
    segments: PathSegment[];
};

// Builds the authorization header used by authenticated API requests.
const authHeader = () => ({
    Authorization: `Bearer ${localStorage.getItem("AUTOTA_AUTH_TOKEN")}`,
});

// Downloads the returned file data using the server filename or a fallback.
const downloadBlobResponse = (res: any, fallbackName: string) => {
    const type =
        (res.headers as any)["content-type"] || "application/octet-stream";
    const blob = new Blob([res.data], { type });
    const name = (res.headers as any)["x-filename"] || fallbackName;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");

    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
};

// Identifies assignment setup items that still need to be supplied.
const getSetupMissingItems = (status: ProjectSetupStatus): string[] => {
    const missingItems: string[] = [];

    if (!status.hasSolutionProgram) {
        missingItems.push("solution");
    }

    if (status.testcaseCount < 1) {
        missingItems.push("test cases");
    }

    return missingItems;
};

// Returns setup state for this view.
const getSetupState = (status: ProjectSetupStatus): "complete" | "missing" =>
    getSetupMissingItems(status).length === 0 ? "complete" : "missing";

// Formats missing setup text for this view.
const formatMissingSetupText = (missingItems: string[]): string => {
    if (missingItems.length === 0) return "";

    return `Missing ${missingItems.join(" + ")}`;
};

// Returns default checkpoint name for this view.
const getDefaultCheckpointName = (number: number): string =>
    `Checkpoint ${number}`;

// Normalizes checkpoint rows for this view.
const normalizeCheckpointRows = (
    rows: PracticeProblemRow[],
): PracticeProblemRow[] =>
    rows.map((pp, index) => {
        const number = index + 1;

        return {
            ...pp,
            number,
            name: (pp.name || "").trim() || getDefaultCheckpointName(number),
        };
    });

// Manages a module overview, its main project, checkpoints, and presentation files.
export default function AdminModuleDetails() {
    // Reads the school, class, or assignment identifiers from the current route.
    const { school_id, class_id, id, module_id } = useParams<{
        school_id: string;
        class_id: string;
        id?: string;
        module_id: string;
    }>();

    const schoolId = school_id || "";
    const classId = class_id || "";
    const routeProjectId = Number(id || 0);
    const routeModuleId = Number(module_id || 0);

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [className, setClassName] = useState("");
    const [module, setModule] = useState<ModuleObject | null>(null);
    const [project, setProject] = useState<ProjectObject | null>(null);
    const [practiceProblems, setPracticeProblems] = useState<
        PracticeProblemRow[]
    >([]);
    const [loading, setLoading] = useState(true);
    const [presentationFile, setPresentationFile] = useState<File | null>(null);
    const [switchingPresentation, setSwitchingPresentation] = useState(false);
    const [savingPresentation, setSavingPresentation] = useState(false);
    const [presentationMessage, setPresentationMessage] = useState("");
    // Keeps presentation input ref available across renders without triggering a state update.
    const presentationInputRef = useRef<HTMLInputElement | null>(null);

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [mainProjectNameDraft, setMainProjectNameDraft] = useState("");
    const [practiceNameDrafts, setPracticeNameDrafts] = useState<
        Record<number, string>
    >({});

    const [editingMainProjectName, setEditingMainProjectName] = useState(false);
    const [editingPracticeNames, setEditingPracticeNames] = useState<
        Record<number, boolean>
    >({});

    const [savingProjectName, setSavingProjectName] = useState(false);
    const [savingPracticeNameId, setSavingPracticeNameId] = useState<
        number | null
    >(null);
    const [pathSvgState, setPathSvgState] = useState<PathSvgState>({
        width: 0,
        height: 0,
        segments: [],
    });
    const [checkpointManagerOpen, setCheckpointManagerOpen] = useState(false);
    const [checkpointDrafts, setCheckpointDrafts] = useState<
        PracticeProblemRow[]
    >([]);
    const [savingCheckpointOrder, setSavingCheckpointOrder] = useState(false);
    const [addingCheckpoint, setAddingCheckpoint] = useState(false);
    const [deletingCheckpointId, setDeletingCheckpointId] = useState<
        number | null
    >(null);
    const [draggedCheckpointId, setDraggedCheckpointId] = useState<number | null>(
        null,
    );
    const [dragOverCheckpointId, setDragOverCheckpointId] = useState<
        number | null
    >(null);

    // Keeps checkpoint path track ref available across renders without triggering a state update.
    const checkpointPathTrackRef = useRef<HTMLDivElement | null>(null);

    // Formats date12h for this view.
    const formatDate12h = (value: string): string => {
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return value;

        return new Intl.DateTimeFormat("en-US", {
            year: "numeric",
            month: "short",
            day: "2-digit",
            hour: "numeric",
            minute: "2-digit",
            hour12: true,
        }).format(d);
    };

    // Returns module status for this view.
    const getModuleStatus = (
        m: ModuleObject,
    ): "active" | "upcoming" | "ended" => {
        const startMs = Date.parse(m.Start);
        const endMs = Date.parse(m.End);
        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "upcoming";

        const now = Date.now();
        if (now >= startMs && now <= endMs) return "active";
        return now < startMs ? "upcoming" : "ended";
    };

    // Helper for hydrate state used by this component.
    const hydrateState = (
        nextModule: ModuleObject | null,
        nextProject: ProjectObject | null,
        nextPractice: PracticeProblemRow[],
    ) => {
        const normalizedPractice = normalizeCheckpointRows(
            [...nextPractice].sort((a, b) => {
                if (a.number !== b.number) return a.number - b.number;
                return a.id - b.id;
            }),
        );

        setModule(nextModule);
        setProject(nextProject);
        setPracticeProblems(normalizedPractice);
        setMainProjectNameDraft(nextProject?.Name || "");
        setPracticeNameDrafts(
            normalizedPractice.reduce<Record<number, string>>((acc, pp) => {
                acc[pp.id] = pp.name;
                return acc;
            }, {}),
        );
        setEditingMainProjectName(false);
        setEditingPracticeNames({});
        setPresentationFile(null);
        setSwitchingPresentation(false);
        setPresentationMessage("");
        if (presentationInputRef.current) {
            presentationInputRef.current.value = "";
        }
    };

    // Loads class name for this view.
    const loadClassName = async (): Promise<void> => {
        if (!schoolId || !classId) {
            setClassName("");
            return;
        }

        try {
            // Fetches the server data needed for this operation.
            const res = await axios.get<ClassAccessResponse>(
                `${import.meta.env.VITE_API_URL}/classes/validate_class_access/${classId}`,
                {
                    headers: authHeader(),
                    params: {
                        school_id: schoolId,
                        role_context: "admin",
                    },
                },
            );

            setClassName(res.data?.name || "");
        } catch (err) {
            console.log(err);
            setClassName("");
        }
    };

    // Loads overview for this view.
    const loadOverview = async (showPageLoading = true): Promise<boolean> => {
        if (!classId || (!routeModuleId && !routeProjectId)) {
            hydrateState(null, null, []);
            setLoading(false);
            return false;
        }

        if (showPageLoading) {
            setLoading(true);
        }

        const url = routeModuleId
            ? `${import.meta.env.VITE_API_URL}/assignment_tracking/get_module_overview?module_id=${routeModuleId}`
            : `${import.meta.env.VITE_API_URL}/assignment_tracking/get_module_overview?project_id=${routeProjectId}`;

        try {
            // Fetches the server data needed for this operation.
            const res = await axios.get(url, { headers: authHeader() });
            const data = res.data as ModuleOverviewResponse;
            const rows = Array.isArray(data.checkpoints)
                ? data.checkpoints
                : Array.isArray(data.practiceProblems)
                    ? data.practiceProblems
                    : [];
            const nextProject = data.project || null;

            hydrateState(
                data.module || null,
                nextProject,
                rows.map((pp) => ({
                    ...pp,
                    hasSolutionProgram: Boolean(pp.hasSolutionProgram),
                    hasTestcases: Boolean(pp.hasTestcases),
                    testcaseCount: Number(pp.testcaseCount || 0),
                })),
            );
            return true;
        } catch (err) {
            console.log(err);
            if (showPageLoading) {
                hydrateState(null, null, []);
            }
            return false;
        } finally {
            if (showPageLoading) {
                setLoading(false);
            }
        }
    };

    // Saves presentation for this view.
    const savePresentation = async (): Promise<void> => {
        if (!module || !presentationFile || savingPresentation) return;

        const formData = new FormData();
        formData.append("module_id", String(module.Id));
        formData.append("presentation", presentationFile, presentationFile.name);

        try {
            setSavingPresentation(true);
            setPresentationMessage("");
            // Sends this operation and its payload to the server.
            const res = await axios.post(
                `${import.meta.env.VITE_API_URL}/assignment_materials/save_module_presentation`,
                formData,
                { headers: authHeader() },
            );
            const savedFileName =
                String(res.data?.file_name || presentationFile.name).trim() ||
                presentationFile.name;

            setModule((current) =>
                current
                    ? {
                        ...current,
                        HasPresentation: true,
                        PresentationFileName: savedFileName,
                    }
                    : current,
            );
            setPresentationFile(null);
            if (presentationInputRef.current) {
                presentationInputRef.current.value = "";
            }
            setSwitchingPresentation(false);
            setPresentationMessage("Presentation saved.");
        } catch (err) {
            const message = axios.isAxiosError<{ message?: string }>(err)
                ? err.response?.data?.message
                : "";
            setPresentationMessage(
                message || "The presentation could not be saved.",
            );
        } finally {
            setSavingPresentation(false);
        }
    };

    // Downloads presentation for this view.
    const downloadPresentation = async (): Promise<void> => {
        if (!module?.HasPresentation) return;

        try {
            // Fetches the server data needed for this operation.
            const res = await axios.get(
                `${import.meta.env.VITE_API_URL}/assignment_materials/get_module_presentation`,
                {
                    headers: authHeader(),
                    params: { module_id: module.Id },
                    responseType: "blob",
                },
            );
            downloadBlobResponse(
                res,
                module.PresentationFileName || "module_presentation",
            );
        } catch {
            setPresentationMessage(
                "The saved presentation could not be downloaded.",
            );
        }
    };

    // Loads or refreshes view data when the dependencies below change.
    useEffect(() => {
        void loadClassName();
        void loadOverview();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [schoolId, classId, routeProjectId, routeModuleId]);

    // Recomputes sorted checkpoints only when its dependencies change.
    const sortedCheckpoints = useMemo(() => {
        return [...practiceProblems].sort((a, b) => {
            if (a.number !== b.number) return a.number - b.number;
            return a.id - b.id;
        });
    }, [practiceProblems]);

    const checkpointOrderChanged =
        checkpointDrafts.length !== sortedCheckpoints.length ||
        checkpointDrafts.some(
            (pp, index) => pp.id !== sortedCheckpoints[index]?.id,
        );

    const canDeleteCheckpoint = sortedCheckpoints.length > 1;

    const checkpointModalBusy =
        savingCheckpointOrder || addingCheckpoint || deletingCheckpointId !== null;

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (checkpointManagerOpen && !checkpointOrderChanged) {
            setCheckpointDrafts(sortedCheckpoints);
        }
    }, [checkpointManagerOpen, checkpointOrderChanged, sortedCheckpoints]);

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (!checkpointManagerOpen) return;

        const previousBodyOverflow = document.body.style.overflow;
        const previousHtmlOverflow = document.documentElement.style.overflow;
        document.body.style.overflow = "hidden";
        document.documentElement.style.overflow = "hidden";
        document.body.classList.add("checkpoint-manager-scroll-lock");
        document.documentElement.classList.add("checkpoint-manager-scroll-lock");

        return () => {
            document.body.style.overflow = previousBodyOverflow;
            document.documentElement.style.overflow = previousHtmlOverflow;
            document.body.classList.remove("checkpoint-manager-scroll-lock");
            document.documentElement.classList.remove(
                "checkpoint-manager-scroll-lock",
            );
        };
    }, [checkpointManagerOpen]);

    // Recomputes main project ready only when its dependencies change.
    const mainProjectReady = useMemo(() => {
        return (
            !!project?.HasSolutionProgram && Number(project?.TestcaseCount || 0) >= 1
        );
    }, [project]);

    // Recomputes path node states only when its dependencies change.
    const pathNodeStates = useMemo<
        ("complete" | "missing" | "incomplete")[]
    >(() => {
        return [
            ...sortedCheckpoints.map((pp) => {
                const status = {
                    hasSolutionProgram: !!pp.hasSolutionProgram,
                    hasTestcases: !!pp.hasTestcases,
                    testcaseCount: Number(pp.testcaseCount || 0),
                };

                if (getSetupState(status) === "missing") return "missing";
                return pp.enabled ? "complete" : "incomplete";
            }),
            getSetupState({
                hasSolutionProgram: !!project?.HasSolutionProgram,
                hasTestcases: !!project?.HasTestcases,
                testcaseCount: Number(project?.TestcaseCount || 0),
            }) === "missing"
                ? "missing"
                : "complete",
        ];
    }, [sortedCheckpoints, project]);

    // Keeps the recalculate path connectors callback stable until its dependencies change.
    const recalculatePathConnectors = useCallback(() => {
        const trackElement = checkpointPathTrackRef.current;

        if (!trackElement) {
            setPathSvgState({ width: 0, height: 0, segments: [] });
            return;
        }

        const nodeElements = Array.from(
            trackElement.querySelectorAll<HTMLElement>(
                "[data-admin-path-node='true']",
            ),
        );

        if (nodeElements.length <= 1) {
            setPathSvgState({
                width: trackElement.offsetWidth,
                height: trackElement.offsetHeight,
                segments: [],
            });
            return;
        }

        const trackRect = trackElement.getBoundingClientRect();
        const nodeRects = nodeElements.map((nodeElement) => {
            const rect = nodeElement.getBoundingClientRect();

            return {
                left: rect.left - trackRect.left,
                right: rect.right - trackRect.left,
                top: rect.top - trackRect.top,
                bottom: rect.bottom - trackRect.top,
                width: rect.width,
                height: rect.height,
                centerX: rect.left - trackRect.left + rect.width / 2,
                centerY: rect.top - trackRect.top + rect.height / 2,
            };
        });

        const segments: PathSegment[] = [];

        for (let i = 0; i < nodeRects.length - 1; i += 1) {
            const current = nodeRects[i];
            const next = nodeRects[i + 1];
            const sameRow =
                Math.abs(current.centerY - next.centerY) <
                Math.min(current.height, next.height) * 0.45;
            let d = "";

            if (sameRow) {
                d = `M ${current.right} ${current.centerY} L ${next.left} ${next.centerY}`;
            } else {
                const routeY =
                    current.bottom + Math.max(12, (next.top - current.bottom) / 2);

                d = [
                    `M ${current.centerX} ${current.bottom}`,
                    `L ${current.centerX} ${routeY}`,
                    `L ${next.centerX} ${routeY}`,
                    `L ${next.centerX} ${next.top}`,
                ].join(" ");
            }

            segments.push({
                key: `admin-checkpoint-connector-${i}`,
                d,
                state: pathNodeStates[i] || "incomplete",
            });
        }

        setPathSvgState({
            width: trackRect.width,
            height: trackRect.height,
            segments,
        });
    }, [pathNodeStates]);

    useLayoutEffect(() => {
        recalculatePathConnectors();

        const trackElement = checkpointPathTrackRef.current;
        if (!trackElement) return;

        const resizeObserver = new ResizeObserver(() => {
            recalculatePathConnectors();
        });

        resizeObserver.observe(trackElement);

        Array.from(
            trackElement.querySelectorAll<HTMLElement>(
                "[data-admin-path-node='true']",
            ),
        ).forEach((nodeElement) => {
            resizeObserver.observe(nodeElement);
        });

        window.addEventListener("resize", recalculatePathConnectors);

        return () => {
            resizeObserver.disconnect();
            window.removeEventListener("resize", recalculatePathConnectors);
        };
    }, [
        recalculatePathConnectors,
        sortedCheckpoints.length,
        editingMainProjectName,
        editingPracticeNames,
        loading,
        module?.Id,
    ]);

    // Saves main project name for this view.
    const saveMainProjectName = async () => {
        if (!project) return;

        const trimmed = mainProjectNameDraft.trim();
        if (!trimmed) {
            window.alert("Please enter a project name.");
            return;
        }

        try {
            setSavingProjectName(true);
            await axios.post(
                `${import.meta.env.VITE_API_URL}/assignment_setup/update_project_name`,
                {
                    project_id: project.Id,
                    name: trimmed,
                },
                { headers: authHeader() },
            );
            setProject((current) =>
                current ? { ...current, Name: trimmed } : current,
            );
            setEditingMainProjectName(false);
        } catch (err) {
            console.log(err);
            window.alert("Could not save the project name.");
        } finally {
            setSavingProjectName(false);
        }
    };

    // Cancels main project edit for this view.
    const cancelMainProjectEdit = () => {
        if (!project) return;
        setMainProjectNameDraft(project.Name);
        setEditingMainProjectName(false);
    };

    // Helper for begin practice name edit used by this component.
    const beginPracticeNameEdit = (
        practiceProblemId: number,
        currentName: string,
    ) => {
        setPracticeNameDrafts((current) => ({
            ...current,
            [practiceProblemId]: current[practiceProblemId] ?? currentName,
        }));
        setEditingPracticeNames((current) => ({
            ...current,
            [practiceProblemId]: true,
        }));
    };

    // Cancels practice name edit for this view.
    const cancelPracticeNameEdit = (
        practiceProblemId: number,
        currentName: string,
    ) => {
        setPracticeNameDrafts((current) => ({
            ...current,
            [practiceProblemId]: currentName,
        }));
        setEditingPracticeNames((current) => ({
            ...current,
            [practiceProblemId]: false,
        }));
    };

    // Saves practice problem name for this view.
    const savePracticeProblemName = async (practiceProblemId: number) => {
        const trimmed = (practiceNameDrafts[practiceProblemId] || "").trim();
        if (!trimmed) {
            window.alert("Please enter a checkpoint name.");
            return;
        }

        try {
            setSavingPracticeNameId(practiceProblemId);
            await axios.post(
                `${import.meta.env.VITE_API_URL}/assignment_setup/update_checkpoint_name`,
                {
                    checkpoint_id: practiceProblemId,
                    name: trimmed,
                },
                { headers: authHeader() },
            );

            setPracticeProblems((current) =>
                current.map((pp) =>
                    pp.id === practiceProblemId ? { ...pp, name: trimmed } : pp,
                ),
            );
            setCheckpointDrafts((current) =>
                current.map((pp) =>
                    pp.id === practiceProblemId ? { ...pp, name: trimmed } : pp,
                ),
            );
            setEditingPracticeNames((current) => ({
                ...current,
                [practiceProblemId]: false,
            }));
        } catch (err) {
            console.log(err);
            window.alert("Could not save the checkpoint name.");
        } finally {
            setSavingPracticeNameId(null);
        }
    };

    // Opens checkpoint manager for this view.
    const openCheckpointManager = () => {
        setCheckpointDrafts(sortedCheckpoints);
        setCheckpointManagerOpen(true);
    };

    // Closes checkpoint manager for this view.
    const closeCheckpointManager = () => {
        if (checkpointModalBusy) return;

        if (
            checkpointOrderChanged &&
            !window.confirm(
                "You have unsaved checkpoint order changes. Close without saving?",
            )
        ) {
            return;
        }

        setCheckpointDrafts(sortedCheckpoints);
        setCheckpointManagerOpen(false);
    };

    // Moves a checkpoint within the draft order before that order is saved.
    const reorderCheckpointDrafts = (draggedId: number, targetId: number) => {
        if (draggedId === targetId || checkpointModalBusy) return;

        setCheckpointDrafts((current) => {
            const draggedIndex = current.findIndex((pp) => pp.id === draggedId);
            const targetIndex = current.findIndex((pp) => pp.id === targetId);

            if (draggedIndex < 0 || targetIndex < 0 || draggedIndex === targetIndex) {
                return current;
            }

            const next = [...current];
            const [draggedItem] = next.splice(draggedIndex, 1);
            next.splice(targetIndex, 0, draggedItem);
            return next;
        });
    };

    // Helper for finish checkpoint drag used by this component.
    const finishCheckpointDrag = () => {
        setDraggedCheckpointId(null);
        setDragOverCheckpointId(null);
    };

    // Persists the reordered checkpoint list to the server.
    const saveCheckpointOrder = async () => {
        if (!project || !checkpointOrderChanged) return;

        try {
            setSavingCheckpointOrder(true);
            const orderedIds = checkpointDrafts.map((pp) => pp.id);

            await axios.post(
                `${import.meta.env.VITE_API_URL}/assignment_setup/reorder_checkpoints`,
                {
                    project_id: project.Id,
                    ordered_ids: orderedIds,
                },
                { headers: authHeader() },
            );

            const reorderedRows = normalizeCheckpointRows(
                orderedIds
                    .map((idValue) => {
                        const existing =
                            checkpointDrafts.find((pp) => pp.id === idValue) ||
                            practiceProblems.find((pp) => pp.id === idValue);
                        return existing || null;
                    })
                    .filter((pp): pp is PracticeProblemRow => !!pp),
            );

            setPracticeProblems(reorderedRows);
            setCheckpointDrafts(reorderedRows);
            setPracticeNameDrafts((current) =>
                reorderedRows.reduce<Record<number, string>>((acc, pp) => {
                    acc[pp.id] = current[pp.id] ?? pp.name;
                    return acc;
                }, {}),
            );
            void loadOverview(false);
        } catch (err) {
            console.log(err);
            window.alert("Could not save the checkpoint order.");
        } finally {
            setSavingCheckpointOrder(false);
        }
    };

    // Adds checkpoint for this view.
    const addCheckpoint = async () => {
        if (!project) return;

        const nextNumber = sortedCheckpoints.length + 1;
        const generatedName = getDefaultCheckpointName(nextNumber);

        try {
            setAddingCheckpoint(true);
            await axios.post(
                `${import.meta.env.VITE_API_URL}/assignment_setup/create_checkpoint`,
                {
                    project_id: project.Id,
                    name: generatedName,
                },
                { headers: authHeader() },
            );

            const refreshed = await loadOverview(false);
            window.alert(
                refreshed
                    ? `"${generatedName}" has been created.`
                    : `"${generatedName}" has been created, but the updated checkpoint list could not be refreshed automatically.`,
            );
            setCheckpointManagerOpen(false);
        } catch (err) {
            console.log(err);
            window.alert("Could not add a checkpoint.");
        } finally {
            setAddingCheckpoint(false);
        }
    };

    // Deletes checkpoint for this view.
    const deleteCheckpoint = async (
        practiceProblemId: number,
        checkpointName: string,
    ) => {
        if (!canDeleteCheckpoint) {
            window.alert("A module must have at least one checkpoint.");
            return;
        }

        if (
            !window.confirm(
                `Delete "${checkpointName}"? Students will no longer see this checkpoint.`,
            )
        ) {
            return;
        }

        try {
            setDeletingCheckpointId(practiceProblemId);
            await axios.post(
                `${import.meta.env.VITE_API_URL}/assignment_setup/delete_checkpoint`,
                {
                    checkpoint_id: practiceProblemId,
                },
                { headers: authHeader() },
            );

            setPracticeProblems((current) =>
                normalizeCheckpointRows(
                    current.filter((pp) => pp.id !== practiceProblemId),
                ),
            );
            setCheckpointDrafts((current) =>
                normalizeCheckpointRows(
                    current.filter((pp) => pp.id !== practiceProblemId),
                ),
            );
            setPracticeNameDrafts((current) => {
                const next = { ...current };
                delete next[practiceProblemId];
                return next;
            });
            loadOverview();
        } catch (err) {
            console.log(err);
            window.alert("Could not delete the checkpoint.");
        } finally {
            setDeletingCheckpointId(null);
        }
    };

    // Formats testcase count for this view.
    const formatTestcaseCount = (count: number): string =>
        `${count} testcase${count === 1 ? "" : "s"}`;

    // Renders setup indicators for this view.
    const renderSetupIndicators = (
        status: ProjectSetupStatus,
        manageUrl: string,
    ) => {
        const testcasesReady = status.testcaseCount >= 1;

        const items = [
            {
                key: "solution",
                label: "Solution Program",
                complete: status.hasSolutionProgram,
                completeText: "Ready",
                missingText: "Missing",
                countBadgeText: undefined,
                to: `${manageUrl}?step=files`,
                icon: status.hasSolutionProgram ? (
                    <FaCheckCircle />
                ) : (
                    <FaExclamationCircle />
                ),
                actionText: status.hasSolutionProgram
                    ? "Modify Program"
                    : "Add Program",
            },
            {
                key: "testcases",
                label: "Test Cases",
                complete: testcasesReady,
                completeText: "Ready",
                missingText: "Missing",
                countBadgeText: formatTestcaseCount(status.testcaseCount),
                to: `${manageUrl}?step=testcases`,
                icon: testcasesReady ? <FaCheckCircle /> : <FaExclamationCircle />,
                actionText: testcasesReady ? "Modify Testcases" : "Add Testcases",
            },
        ];

        // Renders the interface using the current data and interaction state.
        return (
            <div
                className="project-setup-indicators"
                aria-label="Project setup status"
            >
                {items.map((item) => (
                    <Link
                        key={item.key}
                        className={`setup-indicator-card setup-indicator-link is-${item.key}${item.complete ? " is-complete" : " is-missing"}`}
                        to={item.to}
                        aria-label={`${item.actionText} for ${item.label}`}
                    >
                        <span className="setup-indicator-icon" aria-hidden="true">
                            {item.icon}
                        </span>
                        <span className="setup-indicator-copy">
                            <span className="setup-indicator-label-row">
                                <span className="setup-indicator-label">{item.label}</span>
                                {item.countBadgeText && (
                                    <span className="setup-indicator-count-badge">
                                        {item.countBadgeText}
                                    </span>
                                )}
                            </span>
                            <span className="setup-indicator-status">
                                {item.complete ? item.completeText : item.missingText}
                            </span>
                        </span>
                        <span className="setup-indicator-action">
                            <span>{item.actionText}</span>
                            <span className="setup-indicator-action-arrow" aria-hidden="true">
                                →
                            </span>
                        </span>
                    </Link>
                ))}
            </div>
        );
    };

    if (loading) {
        // Renders the interface using the current data and interaction state.
        return (
            <div className="project-detail-page">
                {/* Sets the page title and document metadata. */}
                <Helmet>
                    <title>[Admin] MAAT</title>
                </Helmet>

                {/* Displays the navigation and actions available on this page. */}
                <MenuComponent
                />

                <div className="project-detail-loading">Loading module path...</div>
            </div>
        );
    }

    if (!module || !project) {
        // Renders the interface using the current data and interaction state.
        return (
            <div className="project-detail-page">
                {/* Sets the page title and document metadata. */}
                <Helmet>
                    <title>[Admin] MAAT</title>
                </Helmet>

                {/* Displays the navigation and actions available on this page. */}
                <MenuComponent
                />

                {/* Shows the current location and links back to parent pages. */}
                <DirectoryBreadcrumbs
                    items={[
                        { label: "School Selection", to: "/schools" },
                        { label: "Class Selection", to: `/admin/school/${schoolId}/classes` },
                        { label: "Admin Menu", to: `/admin/school/${schoolId}/class/${classId}/menu` },
                        { label: "Module List", to: `/admin/school/${schoolId}/class/${classId}/modules` },
                        { label: "Module Details" },
                    ]}
                />

                <div className="project-detail-empty">Module not found.</div>
            </div>
        );
    }

    const moduleStatus = getModuleStatus(module);
    const mainProjectNameChanged =
        mainProjectNameDraft.trim() !== project.Name.trim();
    const moduleBaseUrl = `/admin/school/${schoolId}/class/${classId}/module/${module.Id}`;
    const projectBaseUrl = `${moduleBaseUrl}/project/${project.Id}`;
    const mainProjectSetupStatus = {
        hasSolutionProgram: !!project.HasSolutionProgram,
        hasTestcases: !!project.HasTestcases,
        testcaseCount: Number(project.TestcaseCount || 0),
    };
    const mainProjectMissingSetupItems = getSetupMissingItems(
        mainProjectSetupStatus,
    );
    const mainProjectHasMissingSetup = mainProjectMissingSetupItems.length > 0;

    // Renders the interface using the current data and interaction state.
    return (
        <div className="project-detail-page">
            {/* Sets the page title and document metadata. */}
            <Helmet>
                <title>[Admin] MAAT</title>
            </Helmet>

            {/* Displays the navigation and actions available on this page. */}
            <MenuComponent
            />

            {/* Shows the current location and links back to parent pages. */}
            <DirectoryBreadcrumbs
                items={[
                    { label: "School Selection", to: "/schools" },
                    { label: "Class Selection", to: `/admin/school/${schoolId}/classes` },
                    { label: "Admin Menu", to: `/admin/school/${schoolId}/class/${classId}/menu` },
                    { label: "Module List", to: `/admin/school/${schoolId}/class/${classId}/modules` },
                    { label: "Module Details" },
                ]}
            />

            <div className="pageTitle">
                {className ? `${className} Admin Module Details` : "Admin Module Details"}
            </div>

            <div className={`project-detail-hero is-${moduleStatus}`}>
                <div className="project-detail-hero-copy">
                    <div className="project-detail-title-row">
                        <h1 className="project-detail-title">{module.Name}</h1>
                        <span className={`badge-module-status is-${moduleStatus}`}>
                            ● {moduleStatus}
                        </span>
                    </div>

                    <div className="project-detail-dates">
                        {formatDate12h(module.Start)} to {formatDate12h(module.End)}
                    </div>
                </div>

                <section
                    className="module-presentation-card"
                    aria-labelledby="module-presentation-title"
                >
                    <div className="module-presentation-heading">
                        <span className="module-presentation-icon" aria-hidden="true">
                            <FaFilePowerpoint />
                        </span>
                        <div>
                            <span className="module-presentation-eyebrow">
                                Module Resource
                            </span>
                            <h2 id="module-presentation-title">Presentation</h2>
                        </div>
                    </div>

                    <p className="module-presentation-current">
                        {module.HasPresentation
                            ? module.PresentationFileName || "Presentation saved"
                            : "No presentation saved"}
                    </p>

                    {!module.HasPresentation || switchingPresentation ? (
                        <div className="module-presentation-upload">
                            <input
                                ref={presentationInputRef}
                                className="module-presentation-input"
                                type="file"
                                accept=".pdf,.ppt,.pptx"
                                aria-label="Choose a module presentation"
                                onChange={(event) => {
                                    const selectedFile =
                                        event.currentTarget.files?.[0] || null;

                                    if (
                                        selectedFile &&
                                        !/\.(pdf|ppt|pptx)$/i.test(selectedFile.name)
                                    ) {
                                        setPresentationFile(null);
                                        setPresentationMessage(
                                            "Presentations must be PDF, PPT, or PPTX files.",
                                        );
                                        event.currentTarget.value = "";
                                        return;
                                    }

                                    setPresentationFile(selectedFile);
                                    setPresentationMessage("");
                                }}
                                disabled={savingPresentation}
                            />

                            <div className="module-presentation-actions">
                                <button
                                    type="button"
                                    className="project-action project-action-primary"
                                    onClick={savePresentation}
                                    disabled={!presentationFile || savingPresentation}
                                >
                                    <FaUpload aria-hidden="true" />
                                    {savingPresentation
                                        ? "Saving..."
                                        : module.HasPresentation
                                            ? "Save New Presentation"
                                            : "Save Presentation"}
                                </button>

                                {module.HasPresentation ? (
                                    <button
                                        type="button"
                                        className="project-action project-action-secondary"
                                        onClick={() => {
                                            setPresentationFile(null);
                                            setSwitchingPresentation(false);
                                            setPresentationMessage("");
                                            if (presentationInputRef.current) {
                                                presentationInputRef.current.value = "";
                                            }
                                        }}
                                        disabled={savingPresentation}
                                    >
                                        <FaTimes aria-hidden="true" />
                                        Cancel
                                    </button>
                                ) : null}
                            </div>
                        </div>
                    ) : (
                        <div className="module-presentation-actions">
                            <button
                                type="button"
                                className="project-action project-action-primary"
                                onClick={() => {
                                    setSwitchingPresentation(true);
                                    setPresentationMessage("");
                                }}
                                disabled={savingPresentation}
                            >
                                <FaEdit aria-hidden="true" />
                                Edit Presentation
                            </button>

                            <button
                                type="button"
                                className="project-action project-action-secondary"
                                onClick={downloadPresentation}
                                disabled={savingPresentation}
                            >
                                <FaDownload aria-hidden="true" />
                                Download
                            </button>
                        </div>
                    )}

                    {presentationMessage ? (
                        <div className="module-presentation-message" role="status">
                            {presentationMessage}
                        </div>
                    ) : null}
                </section>
            </div>

            <main className="project-workspace">
                <section
                    className="checkpoint-path-section"
                    aria-label="Checkpoint path editor"
                >
                    <div className="checkpoint-path-header">
                        <div>
                            <h2>Checkpoint Path</h2>
                            <p>
                                Students see these as checkpoints that lead into the main
                                project. Use this page to edit names, setup files, test cases,
                                and review submissions.
                            </p>
                        </div>

                        <button
                            type="button"
                            className="project-action project-action-secondary manage-practice-link"
                            onClick={openCheckpointManager}
                        >
                            <FaWrench aria-hidden="true" />
                            Manage Checkpoints
                        </button>
                    </div>

                    <div className="checkpoint-path-track" ref={checkpointPathTrackRef}>
                        <svg
                            className="checkpoint-path-connector-layer"
                            width={pathSvgState.width}
                            height={pathSvgState.height}
                            viewBox={`0 0 ${pathSvgState.width} ${pathSvgState.height}`}
                            aria-hidden="true"
                            focusable="false"
                        >
                            {pathSvgState.segments.map((segment) => (
                                <path
                                    className={[
                                        "checkpoint-path-connector-line",
                                        segment.state === "complete" ? "is-complete" : "",
                                        segment.state === "missing" ? "is-missing" : "",
                                    ]
                                        .join(" ")
                                        .trim()}
                                    d={segment.d}
                                    key={segment.key}
                                />
                            ))}
                        </svg>

                        {sortedCheckpoints.length === 0 ? (
                            <div className="checkpoint-path-empty">
                                No checkpoints are available yet. The main project remains the
                                final path item.
                            </div>
                        ) : null}

                        {sortedCheckpoints.map((pp) => {
                            const submissionCount = pp.submissions ?? 0;
                            const draftName = practiceNameDrafts[pp.id] ?? pp.name;
                            const nameChanged = draftName.trim() !== pp.name.trim();
                            const isEditingPracticeName = !!editingPracticeNames[pp.id];
                            const checkpointSetupStatus = {
                                hasSolutionProgram: !!pp.hasSolutionProgram,
                                hasTestcases: !!pp.hasTestcases,
                                testcaseCount: Number(pp.testcaseCount || 0),
                            };
                            const missingSetupItems = getSetupMissingItems(
                                checkpointSetupStatus,
                            );
                            const hasMissingSetup = missingSetupItems.length > 0;
                            const checkpointReady = pp.enabled && !hasMissingSetup;

                            // Renders the interface using the current data and interaction state.
                            return (
                                <article
                                    className={[
                                        "checkpoint-path-node",
                                        "checkpoint-card",
                                        hasMissingSetup
                                            ? "is-missing-setup"
                                            : checkpointReady
                                                ? "is-complete"
                                                : "is-active",
                                        !pp.enabled ? "is-disabled" : "",
                                    ]
                                        .join(" ")
                                        .trim()}
                                    key={pp.id}
                                    data-admin-path-node="true"
                                >
                                    <div className="checkpoint-node-topline">
                                        <div
                                            className="checkpoint-node-icon practice-number-badge"
                                            aria-hidden="true"
                                        >
                                            {hasMissingSetup ? (
                                                <FaExclamationTriangle />
                                            ) : checkpointReady ? (
                                                <FaCheck />
                                            ) : (
                                                <FaPlay />
                                            )}
                                        </div>

                                        <div className="checkpoint-node-status-stack">
                                            <span className="checkpoint-node-label">
                                                Checkpoint {pp.number}
                                            </span>
                                            {hasMissingSetup ? (
                                                <span className="project-status-badge setup-warning-badge">
                                                    <FaExclamationTriangle aria-hidden="true" />
                                                    {formatMissingSetupText(missingSetupItems)}
                                                </span>
                                            ) : null}
                                            {!pp.enabled ? (
                                                <span className="project-status-badge">Disabled</span>
                                            ) : null}
                                        </div>
                                    </div>

                                    <div className="project-title-editor checkpoint-title-editor">
                                        {!isEditingPracticeName ? (
                                            <div className="read-only-name-row">
                                                <h3 className="project-display-name">{pp.name}</h3>
                                                <button
                                                    type="button"
                                                    className="project-action project-action-secondary compact-edit-button"
                                                    onClick={() => beginPracticeNameEdit(pp.id, pp.name)}
                                                >
                                                    <FaEdit aria-hidden="true" />
                                                    Edit Name
                                                </button>
                                            </div>
                                        ) : (
                                            <>
                                                <input
                                                    className="project-inline-name-input practice-name-input"
                                                    type="text"
                                                    value={draftName}
                                                    onChange={(e) => {
                                                        const next = e.currentTarget.value;
                                                        setPracticeNameDrafts((current) => ({
                                                            ...current,
                                                            [pp.id]: next,
                                                        }));
                                                    }}
                                                />

                                                <div className="name-edit-actions">
                                                    <button
                                                        type="button"
                                                        className="project-action project-action-primary inline-save-button"
                                                        onClick={() => savePracticeProblemName(pp.id)}
                                                        disabled={
                                                            savingPracticeNameId === pp.id || !nameChanged
                                                        }
                                                    >
                                                        <FaSave aria-hidden="true" />
                                                        {savingPracticeNameId === pp.id
                                                            ? "Saving..."
                                                            : "Save Name"}
                                                    </button>

                                                    <button
                                                        type="button"
                                                        className="project-action project-action-secondary inline-save-button"
                                                        onClick={() =>
                                                            cancelPracticeNameEdit(pp.id, pp.name)
                                                        }
                                                        disabled={savingPracticeNameId === pp.id}
                                                    >
                                                        <FaTimes aria-hidden="true" />
                                                        Cancel
                                                    </button>
                                                </div>
                                            </>
                                        )}
                                    </div>

                                    <div className="checkpoint-node-actions">
                                        <div className="project-submission-pill">
                                            <strong>{submissionCount}</strong>
                                            <span>submission{submissionCount === 1 ? "" : "s"}</span>
                                        </div>

                                        <Link
                                            className="review-submissions-action"
                                            to={`${projectBaseUrl}/checkpoint/${pp.id}/submissions`}
                                        >
                                            <FaEye aria-hidden="true" />
                                            Review Submissions
                                        </Link>
                                    </div>

                                    {renderSetupIndicators(
                                        checkpointSetupStatus,
                                        `${projectBaseUrl}/checkpoint/${pp.id}/manage`,
                                    )}
                                </article>
                            );
                        })}

                        <article
                            className={[
                                "checkpoint-path-node",
                                "checkpoint-main-node",
                                mainProjectHasMissingSetup
                                    ? "is-missing-setup"
                                    : mainProjectReady
                                        ? "is-complete"
                                        : "is-active",
                            ]
                                .join(" ")
                                .trim()}
                            data-admin-path-node="true"
                        >
                            <div className="checkpoint-node-topline">
                                <div
                                    className="checkpoint-node-icon main-project-icon"
                                    aria-hidden="true"
                                >
                                    {mainProjectHasMissingSetup ? (
                                        <FaExclamationTriangle />
                                    ) : mainProjectReady ? (
                                        <FaCheck />
                                    ) : (
                                        <FaTasks />
                                    )}
                                </div>

                                <div className="checkpoint-node-status-stack">
                                    <span className="checkpoint-node-label">Main Project</span>
                                    <span className="checkpoint-node-final-badge">
                                        <FaFlagCheckered aria-hidden="true" />
                                        Final submission
                                    </span>
                                    {mainProjectHasMissingSetup ? (
                                        <span className="project-status-badge setup-warning-badge">
                                            <FaExclamationTriangle aria-hidden="true" />
                                            {formatMissingSetupText(mainProjectMissingSetupItems)}
                                        </span>
                                    ) : null}
                                </div>
                            </div>

                            <div className="project-title-editor checkpoint-title-editor">
                                {!editingMainProjectName ? (
                                    <div className="read-only-name-row">
                                        <h3 className="project-display-name">{project.Name}</h3>
                                        <button
                                            type="button"
                                            className="project-action project-action-secondary compact-edit-button"
                                            onClick={() => {
                                                setMainProjectNameDraft(project.Name);
                                                setEditingMainProjectName(true);
                                            }}
                                        >
                                            <FaEdit aria-hidden="true" />
                                            Edit Name
                                        </button>
                                    </div>
                                ) : (
                                    <>
                                        <input
                                            className="project-inline-name-input"
                                            type="text"
                                            value={mainProjectNameDraft}
                                            onChange={(e) =>
                                                setMainProjectNameDraft(e.currentTarget.value)
                                            }
                                        />

                                        <div className="name-edit-actions">
                                            <button
                                                type="button"
                                                className="project-action project-action-primary inline-save-button"
                                                onClick={saveMainProjectName}
                                                disabled={savingProjectName || !mainProjectNameChanged}
                                            >
                                                <FaSave aria-hidden="true" />
                                                {savingProjectName ? "Saving..." : "Save Name"}
                                            </button>

                                            <button
                                                type="button"
                                                className="project-action project-action-secondary inline-save-button"
                                                onClick={cancelMainProjectEdit}
                                                disabled={savingProjectName}
                                            >
                                                <FaTimes aria-hidden="true" />
                                                Cancel
                                            </button>
                                        </div>
                                    </>
                                )}
                            </div>

                            <div className="checkpoint-node-actions">
                                <div className="project-submission-pill">
                                    <strong>{project.TotalSubmissions}</strong>
                                    <span>
                                        submission{project.TotalSubmissions === 1 ? "" : "s"}
                                    </span>
                                </div>

                                <Link
                                    className="review-submissions-action"
                                    to={`${projectBaseUrl}/submissions`}
                                >
                                    <FaEye aria-hidden="true" />
                                    Review Submissions
                                </Link>
                            </div>

                            {renderSetupIndicators(
                                mainProjectSetupStatus,
                                `${projectBaseUrl}/manage`,
                            )}
                        </article>
                    </div>
                </section>
            </main>

            {checkpointManagerOpen ? (
                <div
                    className="checkpoint-manager-modal-backdrop"
                    role="presentation"
                    onMouseDown={(event) => {
                        if (event.target === event.currentTarget) {
                            closeCheckpointManager();
                        }
                    }}
                >
                    <section
                        className="checkpoint-manager-modal"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="checkpoint-manager-title"
                    >
                        <div className="checkpoint-manager-header">
                            <div>
                                <span className="hero-settings-eyebrow">
                                    Checkpoint Manager
                                </span>
                                <h2 id="checkpoint-manager-title">Arrange checkpoints</h2>
                                <p>
                                    Add checkpoints, remove old ones, and set the order students
                                    will follow before the main project.
                                </p>
                                {checkpointOrderChanged ? (
                                    <div
                                        className="checkpoint-manager-unsaved-alert"
                                        role="status"
                                    >
                                        <FaExclamationTriangle aria-hidden="true" />
                                        Unsaved order changes. Click Save Order before closing.
                                    </div>
                                ) : null}
                            </div>

                            <button
                                type="button"
                                className="checkpoint-manager-close"
                                onClick={closeCheckpointManager}
                                aria-label="Close checkpoint manager"
                                disabled={checkpointModalBusy}
                            >
                                <FaTimes aria-hidden="true" />
                            </button>
                        </div>

                        <div className="checkpoint-manager-body">
                            {checkpointDrafts.length === 0 ? (
                                <div className="checkpoint-manager-empty">
                                    No checkpoints have been added yet. Add one to create the
                                    first step before the main project.
                                </div>
                            ) : (
                                <div className="checkpoint-manager-list">
                                    {checkpointDrafts.map((pp, index) => {
                                        const isDragging = draggedCheckpointId === pp.id;
                                        const isDragOver =
                                            dragOverCheckpointId === pp.id &&
                                            draggedCheckpointId !== pp.id;

                                        // Renders the interface using the current data and interaction state.
                                        return (
                                            <article
                                                className={[
                                                    "checkpoint-manager-row",
                                                    isDragging ? "is-dragging" : "",
                                                    isDragOver ? "is-drag-over" : "",
                                                ]
                                                    .join(" ")
                                                    .trim()}
                                                key={pp.id}
                                                draggable={!checkpointModalBusy}
                                                onDragStart={(event) => {
                                                    if (checkpointModalBusy) {
                                                        event.preventDefault();
                                                        return;
                                                    }

                                                    event.dataTransfer.effectAllowed = "move";
                                                    event.dataTransfer.setData(
                                                        "text/plain",
                                                        String(pp.id),
                                                    );
                                                    setDraggedCheckpointId(pp.id);
                                                }}
                                                onDragOver={(event) => {
                                                    if (
                                                        checkpointModalBusy ||
                                                        draggedCheckpointId === null
                                                    )
                                                        return;

                                                    event.preventDefault();
                                                    event.dataTransfer.dropEffect = "move";
                                                    setDragOverCheckpointId(pp.id);
                                                }}
                                                onDragLeave={(event) => {
                                                    if (
                                                        event.currentTarget.contains(
                                                            event.relatedTarget as Node | null,
                                                        )
                                                    )
                                                        return;
                                                    setDragOverCheckpointId((current) =>
                                                        current === pp.id ? null : current,
                                                    );
                                                }}
                                                onDrop={(event) => {
                                                    event.preventDefault();
                                                    const draggedId = Number(
                                                        event.dataTransfer.getData("text/plain") ||
                                                        draggedCheckpointId,
                                                    );

                                                    if (!Number.isNaN(draggedId)) {
                                                        reorderCheckpointDrafts(draggedId, pp.id);
                                                    }

                                                    finishCheckpointDrag();
                                                }}
                                                onDragEnd={finishCheckpointDrag}
                                            >
                                                <div
                                                    className="checkpoint-manager-drag-handle"
                                                    aria-label={`Drag ${pp.name} to reorder`}
                                                    title="Drag to reorder"
                                                >
                                                    <FaGripVertical aria-hidden="true" />
                                                </div>

                                                <div className="checkpoint-manager-number">
                                                    {index + 1}
                                                </div>

                                                <div className="checkpoint-manager-row-copy">
                                                    <strong>{pp.name}</strong>
                                                    <span>
                                                        {pp.submissions ?? 0} submission
                                                        {(pp.submissions ?? 0) === 1 ? "" : "s"}
                                                    </span>
                                                </div>

                                                <div className="checkpoint-manager-row-actions">
                                                    <button
                                                        type="button"
                                                        className="project-action project-action-danger checkpoint-manager-icon-button"
                                                        onClick={() => deleteCheckpoint(pp.id, pp.name)}
                                                        disabled={
                                                            checkpointModalBusy || !canDeleteCheckpoint
                                                        }
                                                        title={
                                                            canDeleteCheckpoint
                                                                ? "Delete checkpoint"
                                                                : "A module must have at least one checkpoint"
                                                        }
                                                        aria-label={`Delete ${pp.name}`}
                                                    >
                                                        <FaTrash aria-hidden="true" />
                                                        {deletingCheckpointId === pp.id
                                                            ? "Deleting..."
                                                            : "Delete"}
                                                    </button>
                                                </div>
                                            </article>
                                        );
                                    })}
                                </div>
                            )}

                            <article className="checkpoint-manager-row checkpoint-manager-main-row">
                                <div className="checkpoint-manager-number checkpoint-manager-main-number">
                                    <FaFlagCheckered aria-hidden="true" />
                                </div>

                                <div className="checkpoint-manager-row-copy">
                                    <strong>{project.Name}</strong>
                                    <span>
                                        Main program · {project.TotalSubmissions} submission
                                        {project.TotalSubmissions === 1 ? "" : "s"}
                                    </span>
                                </div>

                                <div className="checkpoint-manager-row-actions checkpoint-manager-main-actions">
                                    <span className="checkpoint-manager-fixed-pill">
                                        Fixed final step
                                    </span>
                                </div>
                            </article>
                        </div>

                        <div className="checkpoint-manager-footer">
                            <div className="checkpoint-manager-footer-start">
                                <button
                                    type="button"
                                    className="project-action project-action-secondary"
                                    onClick={addCheckpoint}
                                    disabled={checkpointModalBusy}
                                >
                                    <FaPlus aria-hidden="true" />
                                    {addingCheckpoint ? "Adding..." : "Add Checkpoint"}
                                </button>
                                {checkpointOrderChanged ? (
                                    <span className="checkpoint-manager-unsaved-pill">
                                        Unsaved changes
                                    </span>
                                ) : null}
                            </div>

                            <div className="checkpoint-manager-footer-actions">
                                <button
                                    type="button"
                                    className="project-action project-action-secondary"
                                    onClick={closeCheckpointManager}
                                    disabled={checkpointModalBusy}
                                >
                                    Cancel
                                </button>

                                <button
                                    type="button"
                                    className="project-action project-action-primary"
                                    onClick={saveCheckpointOrder}
                                    disabled={!checkpointOrderChanged || checkpointModalBusy}
                                >
                                    <FaSave aria-hidden="true" />
                                    {savingCheckpointOrder ? "Saving..." : "Save Order"}
                                </button>
                            </div>
                        </div>
                    </section>
                </div>
            ) : null}
        </div>
    );
}
