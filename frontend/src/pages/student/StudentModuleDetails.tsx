import {
    CSSProperties,
    KeyboardEvent,
    useCallback,
    useEffect,
    useLayoutEffect,
    useMemo,
    useRef,
    useState,
} from "react";
import axios from "axios";
import { Helmet } from "react-helmet";
import { useNavigate, useParams } from "react-router-dom";
import {
    FaArrowRight,
    FaBolt,
    FaCheck,
    FaClock,
    FaFlagCheckered,
    FaForward,
    FaLock,
    FaPlay,
    FaQuestionCircle,
    FaSignOutAlt,
    FaStar,
    FaTrophy,
    FaUsers,
} from "react-icons/fa";

import MenuComponent from "../components/MenuComponent";
import StarSpendingInfo from "../components/StarSpendingInfo";
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs";
import "../../styling/StudentModuleDetails.scss";

interface ModuleObject {
    Id: number;
    ClassId: number;
    Name: string;
    Start: string;
    End: string;
    MainProjectId?: number;
    MainProjectName?: string;
    TotalSubmissions?: number;
    CheckpointTotalSubmissions?: number;
    CheckpointsEnabled?: boolean;
    MainCompleted?: boolean;
    MainRewarded?: boolean;
    MainRewardStars?: number;
    MainRewardMultiplier?: number;
    MainStartedEarly?: boolean;
}

interface ClassAccessResponse {
    id?: number;
    name?: string;
    school_id?: number;
    school_name?: string;
}

interface Checkpoint {
    id: number;
    number: number;
    name: string;
    enabled: boolean;
    solved: boolean;
    rewarded: boolean;
    skipped?: boolean;
    rewardStars?: number;
    rewardMultiplier?: number;
    startedEarly?: boolean;
}

interface IncentiveSummary {
    stars?: number;
    star_balance?: number;
    checkpoint_completion_stars?: number;
    main_project_completion_stars?: number;
    early_start_multiplier?: number;
    checkpoint_skip_cost?: number;
    cooldown_skip_cost?: number;
}

interface OfficeHoursStatus {
    status: "not_queued" | "waiting" | "being_helped";
    in_queue: boolean;
    office_hours_active?: boolean;
    session_started_at?: string | null;
    session_ends_at?: string | null;
    session_remaining_seconds?: number;
    session_duration_minutes?: number;
    user_id?: number;
    module_id?: number;
    module_name?: string;
    joined_at?: string | null;
    selected_at?: string | null;
    queue_position?: number | null;
    cooldown_exempt?: boolean;
    cooldown_exempt_until?: string | null;
    help_remaining_seconds?: number;
}

interface PathSegment {
    key: string;
    d: string;
    completed: boolean;
}

interface PathSvgState {
    width: number;
    height: number;
    segments: PathSegment[];
}

interface RewardMath {
    baseStars: number;
    multiplier: number;
    totalStars: number;
    doubled: boolean;
    skipped: boolean;
    startedEarly: boolean;
}

const authHeader = () => ({
    Authorization: `Bearer ${localStorage.getItem("AUTOTA_AUTH_TOKEN")}`,
});

export default function StudentModuleDetails() {
    const { school_id, class_id, module_id } = useParams<{
        school_id: string;
        class_id: string;
        module_id: string;
    }>();

    const navigate = useNavigate();

    const schoolId = school_id || "";
    const classId = class_id || "";
    const moduleId = Number(module_id || 0);

    const [className, setClassName] = useState("");
    const [module, setModule] = useState<ModuleObject | null>(null);
    const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [errorMessage, setErrorMessage] = useState("");
    const [actionMessage, setActionMessage] = useState("");
    const [actionError, setActionError] = useState("");
    const [skipBusyCheckpointId, setSkipBusyCheckpointId] = useState<number | null>(null);
    const [skipConfirmationCheckpointId, setSkipConfirmationCheckpointId] =
        useState<number | null>(null);
    const [incentives, setIncentives] = useState<IncentiveSummary | null>(null);
    const [pathSvgState, setPathSvgState] = useState<PathSvgState>({
        width: 0,
        height: 0,
        segments: [],
    });
    const [nowMs, setNowMs] = useState<number>(() => Date.now());
    const [officeHoursStatus, setOfficeHoursStatus] =
        useState<OfficeHoursStatus | null>(null);
    const [isOfficeHoursLoading, setIsOfficeHoursLoading] = useState(true);
    const [isOfficeHoursBusy, setIsOfficeHoursBusy] = useState(false);
    const [officeHoursError, setOfficeHoursError] = useState("");

    const modulePathTrackRef = useRef<HTMLDivElement | null>(null);

    const mainProjectId = module?.MainProjectId || 0;
    const mainProjectName = module?.MainProjectName?.trim() || "Main Project";

    const starBalance = Number(incentives?.star_balance ?? incentives?.stars ?? 0);
    const checkpointSkipCost = Math.max(0, Number(incentives?.checkpoint_skip_cost ?? 6));
    const checkpointSkipStarLabel = checkpointSkipCost === 1 ? "star" : "stars";
    const cooldownSkipCost = Math.max(0, Number(incentives?.cooldown_skip_cost ?? 2));
    const checkpointRewardBase = Math.max(0, Number(incentives?.checkpoint_completion_stars ?? 1));
    const mainRewardBase = Math.max(0, Number(incentives?.main_project_completion_stars ?? 3));
    const earlyStartMultiplier = Math.max(1, Number(incentives?.early_start_multiplier ?? 2));
    const officeHoursState = officeHoursStatus?.status || "not_queued";
    const officeHoursSessionEndsMs = officeHoursStatus?.session_ends_at
        ? Date.parse(officeHoursStatus.session_ends_at)
        : 0;
    const officeHoursActive = Boolean(
        officeHoursStatus?.office_hours_active &&
        (!officeHoursSessionEndsMs || officeHoursSessionEndsMs > nowMs),
    );
    const officeHoursExpiresMs = officeHoursStatus?.cooldown_exempt_until
        ? Date.parse(officeHoursStatus.cooldown_exempt_until)
        : 0;
    const officeHoursRemainingSeconds = Number.isFinite(officeHoursExpiresMs)
        ? Math.max(0, Math.ceil((officeHoursExpiresMs - nowMs) / 1000))
        : Math.max(0, Number(officeHoursStatus?.help_remaining_seconds || 0));

    const checkpointIsComplete = (problem: Checkpoint): boolean => {
        return Boolean(problem.solved || problem.skipped);
    };

    const getCheckpointRewardStars = (problem: Checkpoint): number => {
        return Math.max(0, Number(problem.rewardStars ?? (problem.rewarded ? checkpointRewardBase : 0)));
    };

    const parseDate = (value: string): Date | null => {
        const d = new Date(value);
        return Number.isNaN(d.getTime()) ? null : d;
    };

    const formatDateObject12h = (d: Date): string => {
        return new Intl.DateTimeFormat("en-US", {
            year: "numeric",
            month: "short",
            day: "2-digit",
            hour: "numeric",
            minute: "2-digit",
            second: "2-digit",
            hour12: true,
        }).format(d);
    };

    const formatDate12h = (value: string): string => {
        const d = parseDate(value);
        if (!d) return value;

        return formatDateObject12h(d);
    };

    const formatStarValue = (value: number): string => {
        if (!Number.isFinite(value)) return "0";
        return Number.isInteger(value) ? `${value}` : value.toFixed(1);
    };

    const formatStarCount = (value: number): string => {
        return `${formatStarValue(value)} ${value === 1 ? "star" : "stars"}`;
    };

    const formatCountdown = (seconds: number): string => {
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

        return `${minutes}m ${secs}s`;
    };

    const getEarlyStartCutoffDate = (currentModule: ModuleObject): Date | null => {
        const start = parseDate(currentModule.Start);
        const end = parseDate(currentModule.End);

        if (!start || !end || end.getTime() <= start.getTime()) {
            return null;
        }

        return new Date(start.getTime() + (end.getTime() - start.getTime()) / 2);
    };

    const getModuleStatus = (
        currentModule: ModuleObject,
    ): "active" | "upcoming" | "ended" => {
        const startMs = Date.parse(currentModule.Start);
        const endMs = Date.parse(currentModule.End);

        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "upcoming";

        if (nowMs >= startMs && nowMs <= endMs) return "active";
        return nowMs < startMs ? "upcoming" : "ended";
    };

    const statusLabel = module ? getModuleStatus(module) : "upcoming";

    const earlyStartCutoffDate = useMemo(() => {
        return module ? getEarlyStartCutoffDate(module) : null;
    }, [module]);

    const earlyStartCutoffMs = earlyStartCutoffDate?.getTime() ?? 0;
    const earlyStartWindowOpen = Boolean(earlyStartCutoffMs && nowMs <= earlyStartCutoffMs);
    const earlyStartCountdownSeconds = earlyStartCutoffMs
        ? Math.max(0, Math.ceil((earlyStartCutoffMs - nowMs) / 1000))
        : 0;
    const earlyStartDeadlineLabel = earlyStartCutoffDate
        ? formatDateObject12h(earlyStartCutoffDate)
        : "Unavailable";

    const earlyBonusStatusText = earlyStartCutoffDate
        ? earlyStartWindowOpen
            ? `${formatCountdown(earlyStartCountdownSeconds)} remaining`
            : "Bonus window closed"
        : "Bonus window unavailable";

    const earlyBonusActionText = earlyStartCutoffDate
        ? earlyStartWindowOpen
            ? `Complete before ${earlyStartDeadlineLabel} to double rewards.`
            : `Doubling stopped after ${earlyStartDeadlineLabel}.`
        : "Early bonus timing could not be calculated.";

    const sortedCheckpoints = useMemo(() => {
        return [...checkpoints].sort((a, b) => {
            if (a.number !== b.number) return a.number - b.number;
            return a.id - b.id;
        });
    }, [checkpoints]);
    const skipConfirmationCheckpoint = sortedCheckpoints.find(
        (problem) => problem.id === skipConfirmationCheckpointId,
    );

    const completedCheckpointCount = sortedCheckpoints.filter(
        (problem) => checkpointIsComplete(problem),
    ).length;
    const totalCheckpointCount = sortedCheckpoints.length;
    const mainCompleted = Boolean(module?.MainCompleted);
    const totalAssignmentCount = totalCheckpointCount + 1;
    const completedAssignmentCount =
        completedCheckpointCount + (mainCompleted ? 1 : 0);
    const allCheckpointSolved =
        totalCheckpointCount === 0 ||
        completedCheckpointCount === totalCheckpointCount;
    const firstUnsolvedIndex = sortedCheckpoints.findIndex(
        (problem) => !checkpointIsComplete(problem),
    );
    const activeCheckpointIndex =
        firstUnsolvedIndex === -1 ? totalCheckpointCount : firstUnsolvedIndex;
    const progressPercent = Math.round(
        (completedAssignmentCount / totalAssignmentCount) * 100,
    );

    const getRewardMath = (
        baseStars: number,
        rewarded: boolean,
        awardedStars: number | undefined,
        awardedMultiplier: number | undefined,
        skipped = false,
        startedEarly = false,
    ): RewardMath => {
        const safeBaseStars = Math.max(0, Number(baseStars || 0));
        const storedMultiplier = Math.max(1, Number(awardedMultiplier || 1));

        if (skipped) {
            return {
                baseStars: safeBaseStars,
                multiplier: 0,
                totalStars: 0,
                doubled: false,
                skipped: true,
                startedEarly: false,
            };
        }

        if (rewarded) {
            const storedStars = Math.max(
                0,
                Number(awardedStars ?? safeBaseStars * storedMultiplier),
            );
            const derivedBaseStars =
                storedMultiplier > 1 && storedStars > 0
                    ? storedStars / storedMultiplier
                    : safeBaseStars;

            return {
                baseStars: derivedBaseStars || safeBaseStars,
                multiplier: storedMultiplier,
                totalStars: storedStars || safeBaseStars * storedMultiplier,
                doubled: storedMultiplier > 1,
                skipped: false,
                startedEarly: Boolean(startedEarly || storedMultiplier > 1),
            };
        }

        const earlyBonusApplies = earlyStartWindowOpen || Boolean(startedEarly);
        const currentMultiplier = earlyBonusApplies ? earlyStartMultiplier : 1;

        return {
            baseStars: safeBaseStars,
            multiplier: currentMultiplier,
            totalStars: safeBaseStars * currentMultiplier,
            doubled: currentMultiplier > 1,
            skipped: false,
            startedEarly: Boolean(startedEarly),
        };
    };

    const renderRewardMath = (
        rewardMath: RewardMath,
        rewardState: "earned" | "possible" | "skipped",
    ) => {
        if (rewardMath.skipped || rewardState === "skipped") {
            return (
                <div className="module-path-node-reward is-muted" aria-label="No reward because this checkpoint was skipped">
                    <FaStar aria-hidden="true" />
                    <span className="module-path-node-reward-total">+0</span>
                    <span className="module-path-node-equation">skipped</span>
                </div>
            );
        }

        const stateLabel = rewardState === "earned" ? "earned" : "possible";
        const bonusLabel = rewardMath.startedEarly && !earlyStartWindowOpen
            ? `${rewardMath.multiplier}x locked in`
            : `${rewardMath.multiplier}x early bonus`;

        return (
            <div
                className={[
                    "module-path-node-reward",
                    rewardState === "possible" ? "is-potential" : "",
                    rewardMath.doubled ? "is-doubled" : "",
                ]
                    .join(" ")
                    .trim()}
                aria-label={`${formatStarValue(rewardMath.totalStars)} stars ${stateLabel}`}
                title={
                    rewardMath.doubled
                        ? `Early bonus: ${formatStarValue(rewardMath.baseStars)} normal stars becomes ${formatStarValue(rewardMath.totalStars)} stars.`
                        : `${formatStarValue(rewardMath.totalStars)} normal stars ${stateLabel}.`
                }
            >
                <FaStar aria-hidden="true" />
                {rewardMath.doubled ? (
                    <>
                        <span
                            className="module-path-node-pre-bonus"
                            aria-label={`${formatStarValue(rewardMath.baseStars)} stars before bonus`}
                        >
                            +{formatStarValue(rewardMath.baseStars)}
                        </span>
                        <span className="module-path-node-reward-total">
                            +{formatStarValue(rewardMath.totalStars)}
                        </span>
                        <span className="module-path-node-bonus-label">
                            {bonusLabel}
                        </span>
                    </>
                ) : (
                    <>
                        <span className="module-path-node-reward-total">
                            +{formatStarValue(rewardMath.totalStars)}
                        </span>
                        <span className="module-path-node-equation">
                            {rewardState === "earned" ? "earned" : "normal"}
                        </span>
                    </>
                )}
            </div>
        );
    };

    const pathCompletionStates = useMemo(() => {
        return [
            ...sortedCheckpoints.map((problem) => checkpointIsComplete(problem)),
            mainCompleted,
        ];
    }, [sortedCheckpoints, mainCompleted]);

    const mainRewardMath = getRewardMath(
        mainRewardBase,
        Boolean(module?.MainRewarded),
        Number(module?.MainRewardStars ?? 0),
        Number(module?.MainRewardMultiplier ?? 1),
        false,
        Boolean(module?.MainStartedEarly),
    );

    const recalculatePathConnectors = useCallback(() => {
        const trackElement = modulePathTrackRef.current;

        if (!trackElement) {
            setPathSvgState({ width: 0, height: 0, segments: [] });
            return;
        }

        const nodeElements = Array.from(
            trackElement.querySelectorAll<HTMLElement>(
                "[data-module-path-node='true']",
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
                key: `module-path-connector-${i}`,
                d,
                completed: Boolean(pathCompletionStates[i]),
            });
        }

        setPathSvgState({
            width: trackRect.width,
            height: trackRect.height,
            segments,
        });
    }, [pathCompletionStates]);

    const loadClassName = () => {
        if (!classId) {
            setClassName("");
            return;
        }

        axios
            .get<ClassAccessResponse>(
                `${import.meta.env.VITE_API_URL}/class/id/${classId}/access`,
                {
                    headers: authHeader(),
                    params: {
                        ...(schoolId ? { school_id: schoolId } : {}),
                        role_context: "student",
                    },
                },
            )
            .then((res) => {
                setClassName(res.data?.name || "");
            })
            .catch((err) => {
                console.log(err);
                setClassName("");
            });
    };

    const loadModuleDetails = () => {
        if (!classId || !moduleId) {
            setErrorMessage("Could not find this module.");
            setIsLoading(false);
            return;
        }

        setIsLoading(true);
        setErrorMessage("");

        axios
            .get(
                `${import.meta.env.VITE_API_URL}/projects/get_module_overview_student?module_id=${moduleId}`,
                {
                    headers: authHeader(),
                },
            )
            .then((res) => {
                const selectedModule = res.data?.module || null;

                if (!selectedModule) {
                    setModule(null);
                    setCheckpoints([]);
                    setIncentives(null);
                    setErrorMessage("Could not find this module.");
                    setIsLoading(false);
                    return;
                }

                setModule(selectedModule);
                setCheckpoints(res.data?.checkpoints || []);
                setIncentives(res.data?.incentives || null);
                setIsLoading(false);
            })
            .catch((err) => {
                console.log(err);
                setModule(null);
                setCheckpoints([]);
                setIncentives(null);
                setErrorMessage("Could not load this module.");
                setIsLoading(false);
            });
    };

    useEffect(() => {
        loadClassName();
        loadModuleDetails();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [schoolId, classId, moduleId]);

    const loadOfficeHoursStatus = useCallback((showLoading = false) => {
        if (!classId || !moduleId) {
            setOfficeHoursStatus(null);
            setIsOfficeHoursLoading(false);
            return;
        }

        if (showLoading) setIsOfficeHoursLoading(true);
        axios
            .get(`${import.meta.env.VITE_API_URL}/submissions/office-hours/status`, {
                headers: authHeader(),
                params: {
                    class_id: Number(classId),
                    module_id: moduleId,
                },
            })
            .then((response) => {
                setOfficeHoursStatus(response.data || null);
                setOfficeHoursError("");
            })
            .catch((error) => {
                setOfficeHoursError(
                    error.response?.data?.message ||
                    "Could not load the office-hours queue status.",
                );
            })
            .finally(() => {
                if (showLoading) setIsOfficeHoursLoading(false);
            });
    }, [classId, moduleId]);

    useEffect(() => {
        loadOfficeHoursStatus(true);
    }, [loadOfficeHoursStatus]);

    useEffect(() => {
        const timer = window.setInterval(() => {
            setNowMs(Date.now());
        }, 1000);

        return () => window.clearInterval(timer);
    }, []);

    useLayoutEffect(() => {
        recalculatePathConnectors();

        const trackElement = modulePathTrackRef.current;
        if (!trackElement) return;

        const resizeObserver = new ResizeObserver(() => {
            recalculatePathConnectors();
        });

        resizeObserver.observe(trackElement);

        Array.from(
            trackElement.querySelectorAll<HTMLElement>(
                "[data-module-path-node='true']",
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
        mainCompleted,
        allCheckpointSolved,
        isLoading,
        module?.Id,
    ]);

    const goBackToModules = () => {
        navigate(`/student/school/${schoolId}/class/${classId}/modules`);
    };

    const openCheckpoint = (checkpointId: number) => {
        if (!mainProjectId) return;

        navigate(
            `/student/school/${schoolId}/class/${classId}/module/${moduleId}/project/${mainProjectId}/checkpoint/${checkpointId}/upload`,
        );
    };

    const openMainProject = () => {
        if (!mainProjectId || !allCheckpointSolved) return;

        navigate(
            `/student/school/${schoolId}/class/${classId}/module/${moduleId}/project/${mainProjectId}/upload`,
        );
    };

    const handleCheckpointKeyDown = (
        event: KeyboardEvent<HTMLElement>,
        checkpointId: number,
        locked: boolean,
    ) => {
        if (locked || (event.key !== "Enter" && event.key !== " ")) return;

        event.preventDefault();
        openCheckpoint(checkpointId);
    };

    const skipCheckpoint = (checkpointId: number) => {
        if (!mainProjectId || !classId || !moduleId || skipBusyCheckpointId !== null) return;

        setActionMessage("");
        setActionError("");
        setSkipBusyCheckpointId(checkpointId);

        axios
            .post(
                `${import.meta.env.VITE_API_URL}/projects/skip_checkpoint`,
                {
                    class_id: Number(classId),
                    project_id: mainProjectId,
                    checkpoint_id: checkpointId,
                },
                { headers: authHeader() },
            )
            .then((res) => {
                setCheckpoints(res.data?.checkpoints || []);
                setIncentives(res.data?.incentives || null);
                setActionMessage(res.data?.message || "Checkpoint skipped.");
            })
            .catch((err) => {
                setActionError(
                    err.response?.data?.message ||
                    "Could not skip this checkpoint. Please try again.",
                );
            })
            .finally(() => {
                setSkipBusyCheckpointId(null);
                setSkipConfirmationCheckpointId(null);
            });
    };

    const joinOfficeHoursQueue = () => {
        if (!classId || !moduleId || isOfficeHoursBusy) return;
        setIsOfficeHoursBusy(true);
        setOfficeHoursError("");

        axios
            .post(
                `${import.meta.env.VITE_API_URL}/submissions/office-hours/queue`,
                {
                    class_id: Number(classId),
                    module_id: moduleId,
                },
                { headers: authHeader() },
            )
            .then((response) => setOfficeHoursStatus(response.data || null))
            .catch((error) => {
                setOfficeHoursError(
                    error.response?.data?.message ||
                    "Could not join the office-hours queue.",
                );
                if (error.response?.data?.status) {
                    setOfficeHoursStatus(error.response.data);
                }
            })
            .finally(() => setIsOfficeHoursBusy(false));
    };

    const leaveOfficeHoursQueue = () => {
        if (!classId || !moduleId || isOfficeHoursBusy) return;
        setIsOfficeHoursBusy(true);
        setOfficeHoursError("");

        axios
            .delete(
                `${import.meta.env.VITE_API_URL}/submissions/office-hours/queue`,
                {
                    headers: authHeader(),
                    data: {
                        class_id: Number(classId),
                        module_id: moduleId,
                    },
                },
            )
            .then((response) => setOfficeHoursStatus(response.data || null))
            .catch((error) => {
                setOfficeHoursError(
                    error.response?.data?.message ||
                    "Could not leave the office-hours queue.",
                );
            })
            .finally(() => setIsOfficeHoursBusy(false));
    };

    return (
        <div className="student-module-details-page">
            <Helmet>
                <title>MAAT</title>
            </Helmet>

            <MenuComponent
                showUpload={true}
                showAdminUpload={false}
                showHelp={false}
                showCreate={false}
                showLast={false}
                showReviewButton={false}
            />

            <DirectoryBreadcrumbs
                items={[
                    { label: "School Selection", to: "/schools" },
                    {
                        label: "Class Selection",
                        to: schoolId
                            ? `/student/school/${schoolId}/classes`
                            : "/schools",
                    },
                    {
                        label: "Module List",
                        to: `/student/school/${schoolId}/class/${classId}/modules`,
                    },
                    { label: "Module Details" },
                ]}
            />

            <div className="pageTitle">
                {className ? `${className} Student Module Details` : "Student Module Details"}
            </div>

            <div className="student-module-details-shell">
                {isLoading ? (
                    <div className="module-details-message">Loading module path...</div>
                ) : null}

                {!isLoading && errorMessage ? (
                    <div className="module-details-message is-error">{errorMessage}</div>
                ) : null}

                {!isLoading && module ? (
                    <>
                        <section className={`module-quest-hero is-${statusLabel}`}>
                            <div className="module-quest-hero-copy">
                                <h1>{module.Name}</h1>
                                <div className="module-quest-meta-row">
                                    <span>{formatDate12h(module.Start)}</span>
                                    <span>to</span>
                                    <span>{formatDate12h(module.End)}</span>
                                </div>
                            </div>

                            <div className="module-quest-side-cards">
                                <div className="module-quest-progress-card">
                                    <div
                                        className="module-quest-progress-ring"
                                        style={
                                            {
                                                "--progress-percent": `${progressPercent}%`,
                                            } as CSSProperties
                                        }
                                    >
                                        <span>{progressPercent}%</span>
                                    </div>

                                    <div>
                                        <div className="module-quest-progress-title">
                                            {completedAssignmentCount} / {totalAssignmentCount}{" "}
                                            assignments completed
                                        </div>
                                    </div>
                                </div>

                                <div className="module-quest-star-card" aria-label="Star balance and star uses">
                                    <div className="module-quest-star-card-header">
                                        <div className="module-quest-star-card-balance">
                                            <FaStar aria-hidden="true" />
                                            <div>
                                                <div className="module-quest-star-count">
                                                    {formatStarValue(starBalance)}
                                                </div>
                                                <div className="module-quest-star-label">Stars available</div>
                                            </div>
                                        </div>
                                        <StarSpendingInfo />
                                    </div>
                                </div>

                                <div
                                    className={[
                                        "module-quest-early-card",
                                        earlyStartWindowOpen ? "is-open" : "is-closed",
                                    ]
                                        .join(" ")
                                        .trim()}
                                    aria-label="Early start bonus explanation"
                                >
                                    <div className="module-quest-early-badge">
                                        <FaBolt aria-hidden="true" />
                                        <span>{earlyStartMultiplier}x early bonus</span>
                                    </div>
                                    <div className="module-quest-early-action">
                                        {earlyBonusActionText}
                                    </div>
                                    <div className="module-quest-early-countdown">
                                        {earlyBonusStatusText}
                                    </div>
                                </div>
                            </div>
                        </section>

                        <section
                            className="module-path-section"
                            aria-label="Checkpoint unlock path"
                        >
                            <div className="module-path-header">
                                <div>
                                    <h2>Checkpoint Path</h2>
                                    <p>
                                        Work through checkpoints in order. Stars can be spent to skip the next
                                        available checkpoint for {" "}{checkpointSkipCost} stars, or spent on the
                                        upload page to bypass the submission cooldown for {cooldownSkipCost} stars.
                                    </p>
                                </div>
                            </div>

                            {actionMessage ? (
                                <div className="module-path-action-message" role="status">
                                    {actionMessage}
                                </div>
                            ) : null}

                            {actionError ? (
                                <div className="module-path-action-message is-error" role="alert">
                                    {actionError}
                                </div>
                            ) : null}

                            <div className="module-path-track" ref={modulePathTrackRef}>
                                <svg
                                    className="module-path-connector-layer"
                                    width={pathSvgState.width}
                                    height={pathSvgState.height}
                                    viewBox={`0 0 ${pathSvgState.width} ${pathSvgState.height}`}
                                    aria-hidden="true"
                                    focusable="false"
                                >
                                    {pathSvgState.segments.map((segment) => (
                                        <path
                                            className={[
                                                "module-path-connector-line",
                                                segment.completed ? "is-complete" : "",
                                            ]
                                                .join(" ")
                                                .trim()}
                                            d={segment.d}
                                            key={segment.key}
                                        />
                                    ))}
                                </svg>

                                {sortedCheckpoints.length === 0 ? (
                                    <div className="module-path-empty">
                                        No checkpoints are available yet. The main project is open.
                                    </div>
                                ) : null}

                                {sortedCheckpoints.map((problem, index) => {
                                    const completed = checkpointIsComplete(problem);
                                    const skipped = Boolean(problem.skipped);
                                    const locked = !completed && index > activeCheckpointIndex;
                                    const available = !completed && !locked;
                                    const canSkip =
                                        available &&
                                        !completed &&
                                        checkpointSkipCost > 0 &&
                                        starBalance >= checkpointSkipCost;
                                    const rewardStars = getCheckpointRewardStars(problem);
                                    const rewardMultiplier = Number(problem.rewardMultiplier ?? 1);
                                    const rewardMath = getRewardMath(
                                        checkpointRewardBase,
                                        Boolean(problem.rewarded),
                                        rewardStars,
                                        rewardMultiplier,
                                        skipped,
                                        Boolean(problem.startedEarly),
                                    );

                                    return (
                                        <article
                                            className={[
                                                "module-path-node",
                                                completed ? "is-complete" : "",
                                                skipped ? "is-skipped" : "",
                                                available ? "is-active" : "",
                                                locked ? "is-locked" : "",
                                            ]
                                                .join(" ")
                                                .trim()}
                                            key={problem.id}
                                            data-module-path-node="true"
                                            role={locked ? "article" : "button"}
                                            tabIndex={locked ? -1 : 0}
                                            onClick={() => {
                                                if (!locked) openCheckpoint(problem.id);
                                            }}
                                            onKeyDown={(event) =>
                                                handleCheckpointKeyDown(event, problem.id, locked)
                                            }
                                            aria-label={`${problem.name} ${completed ? "completed" : locked ? "locked" : "available"}`}
                                        >
                                            <div className="module-path-node-icon">
                                                {completed ? (
                                                    <FaCheck aria-hidden="true" />
                                                ) : locked ? (
                                                    <FaLock aria-hidden="true" />
                                                ) : (
                                                    <FaPlay aria-hidden="true" />
                                                )}
                                            </div>

                                            <div className="module-path-node-content">
                                                <div className="module-path-node-label">
                                                    Checkpoint {problem.number}
                                                </div>
                                                <h3>{problem.name}</h3>
                                                <p>
                                                    {completed
                                                        ? skipped
                                                            ? "Skipped with stars"
                                                            : "Cleared"
                                                        : locked
                                                            ? "Locked until earlier checkpoints are cleared"
                                                            : "Ready to attempt"}
                                                </p>
                                            </div>

                                            <div className="module-path-node-footer">
                                                {renderRewardMath(
                                                    rewardMath,
                                                    skipped
                                                        ? "skipped"
                                                        : problem.rewarded
                                                            ? "earned"
                                                            : "possible",
                                                )}

                                                {!completed ? (
                                                    <button
                                                        type="button"
                                                        className="module-path-skip-button"
                                                        disabled={!canSkip || skipBusyCheckpointId !== null}
                                                        onClick={(event) => {
                                                            event.preventDefault();
                                                            event.stopPropagation();
                                                            if (canSkip) {
                                                                setSkipConfirmationCheckpointId(problem.id);
                                                            }
                                                        }}
                                                    >
                                                        <FaForward aria-hidden="true" />
                                                        {skipBusyCheckpointId === problem.id
                                                            ? "Skipping..."
                                                            : `Skip for ${checkpointSkipCost} stars`}
                                                    </button>
                                                ) : null}
                                            </div>
                                        </article>
                                    );
                                })}

                                <article
                                    className={[
                                        "module-path-node",
                                        "module-path-main-node",
                                        mainCompleted
                                            ? "is-complete"
                                            : allCheckpointSolved
                                                ? "is-active"
                                                : "is-locked",
                                    ]
                                        .join(" ")
                                        .trim()}
                                    data-module-path-node="true"
                                    role={allCheckpointSolved ? "button" : "article"}
                                    tabIndex={allCheckpointSolved ? 0 : -1}
                                    onClick={openMainProject}
                                    onKeyDown={(event) => {
                                        if (
                                            !allCheckpointSolved ||
                                            (event.key !== "Enter" && event.key !== " ")
                                        )
                                            return;

                                        event.preventDefault();
                                        openMainProject();
                                    }}
                                    aria-label={`${mainProjectName} ${mainCompleted ? "completed" : allCheckpointSolved ? "unlocked" : "locked"}`}
                                >
                                    <div className="module-path-node-icon">
                                        {mainCompleted ? (
                                            <FaCheck aria-hidden="true" />
                                        ) : allCheckpointSolved ? (
                                            <FaTrophy aria-hidden="true" />
                                        ) : (
                                            <FaLock aria-hidden="true" />
                                        )}
                                    </div>

                                    <div className="module-path-node-content">
                                        <div className="module-path-node-label">Main Project</div>
                                        <h3>{mainProjectName}</h3>
                                        <p>
                                            {mainCompleted
                                                ? "Completed"
                                                : allCheckpointSolved
                                                    ? "Unlocked. Submit your main solution."
                                                    : "Locked until all checkpoints are cleared."}
                                        </p>
                                    </div>

                                    <div className="module-path-node-footer">
                                        {renderRewardMath(
                                            mainRewardMath,
                                            module.MainRewarded ? "earned" : "possible",
                                        )}

                                        <div className="module-path-main-flag">
                                            <FaFlagCheckered aria-hidden="true" />
                                        </div>
                                    </div>
                                </article>
                            </div>
                        </section>

                        {officeHoursActive ? (
                            <section
                                className={`module-office-hours-card is-${officeHoursState}`}
                                aria-labelledby="module-office-hours-title"
                            >
                                <div className="module-office-hours-icon" aria-hidden="true">
                                    {officeHoursState === "being_helped" ? <FaUsers /> : <FaQuestionCircle />}
                                </div>
                                <div className="module-office-hours-copy">
                                    <div className="module-office-hours-title-row">
                                        <div>
                                            <p>Need in-person help?</p>
                                            <h2 id="module-office-hours-title">Office Hours</h2>
                                        </div>
                                    </div>

                                    <p>
                                        Once an admin accepts you for Office Hours, your submission cooldowns are
                                        bypassed while your help session is active.
                                    </p>

                                    {officeHoursError ? (
                                        <div className="module-office-hours-error" role="alert">
                                            {officeHoursError}
                                        </div>
                                    ) : null}
                                </div>

                                <div className="module-office-hours-action">
                                    <span className={`module-office-hours-status is-${officeHoursState}`}>
                                        {isOfficeHoursLoading
                                            ? "Checking queue"
                                            : officeHoursState === "waiting"
                                                ? `Awaiting Admin${officeHoursStatus?.queue_position ? ` · #${officeHoursStatus.queue_position}` : ""}`
                                                : officeHoursState === "being_helped"
                                                    ? "Being helped"
                                                    : "Not in queue"}
                                    </span>

                                    {officeHoursState === "being_helped" ? (
                                        <div className="module-office-hours-countdown" role="status">
                                            <FaClock aria-hidden="true" />
                                            <span>Cooldown-free</span>
                                            <strong>{formatCountdown(officeHoursRemainingSeconds)}</strong>
                                        </div>
                                    ) : null}

                                    {officeHoursState === "not_queued" ? (
                                        <button
                                            type="button"
                                            onClick={joinOfficeHoursQueue}
                                            disabled={isOfficeHoursLoading || isOfficeHoursBusy}
                                        >
                                            <FaUsers aria-hidden="true" />
                                            {isOfficeHoursBusy ? "Joining..." : "Join Office Hours Queue"}
                                        </button>
                                    ) : (
                                        <button
                                            type="button"
                                            className="secondary"
                                            onClick={leaveOfficeHoursQueue}
                                            disabled={isOfficeHoursBusy}
                                        >
                                            <FaSignOutAlt aria-hidden="true" />
                                            {isOfficeHoursBusy ? "Leaving..." : "Leave Queue"}
                                        </button>
                                    )}
                                </div>
                            </section>
                        ) : null}

                    </>
                ) : null}
            </div>

            {skipConfirmationCheckpoint ? (
                <div
                    className="skip-cooldown-confirmation"
                    onMouseDown={(event) => {
                        if (
                            event.target === event.currentTarget &&
                            skipBusyCheckpointId === null
                        ) {
                            setSkipConfirmationCheckpointId(null);
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
                            if (event.key === "Escape" && skipBusyCheckpointId === null) {
                                setSkipConfirmationCheckpointId(null);
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
                            Spend {checkpointSkipCost} {checkpointSkipStarLabel}?
                        </h2>
                        <p id="skip-cooldown-confirmation-description">
                            This will immediately skip Checkpoint{" "}
                            {skipConfirmationCheckpoint.number}:{" "}
                            {skipConfirmationCheckpoint.name}. This purchase cannot be undone.
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
                                    {formatStarCount(
                                        Math.max(0, starBalance - checkpointSkipCost),
                                    )}
                                </strong>
                            </span>
                        </div>

                        <div className="skip-cooldown-confirmation__actions">
                            <button
                                type="button"
                                className="skip-cooldown-confirmation__cancel"
                                disabled={skipBusyCheckpointId !== null}
                                onClick={() => setSkipConfirmationCheckpointId(null)}
                                autoFocus
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                className="skip-cooldown-confirmation__confirm"
                                disabled={skipBusyCheckpointId !== null}
                                onClick={() => skipCheckpoint(skipConfirmationCheckpoint.id)}
                            >
                                <FaForward aria-hidden="true" />
                                {skipBusyCheckpointId === skipConfirmationCheckpoint.id
                                    ? "Spending..."
                                    : `Confirm and spend ${checkpointSkipCost} ${checkpointSkipStarLabel}`}
                            </button>
                        </div>
                    </div>
                </div>
            ) : null}
        </div>
    );
}
