// AdminOfficeHours.tsx: Displays the office-hours queue and manages helping sessions.
import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { Helmet } from "react-helmet";
import { useParams } from "react-router-dom";
import { FaCheck, FaClock, FaPlay, FaRedo } from "react-icons/fa";

import MenuComponent from "../components/MenuComponent";
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs";
import "../../styling/Selection.scss";
import "../../styling/AdminOfficeHours.scss";

// Describes the class access response data expected by this file.
type ClassAccessResponse = {
    name?: string;
};

// Describes the office hours entry data expected by this file.
type OfficeHoursEntry = {
    id: number;
    user_id: number;
    first_name: string;
    last_name: string;
    email: string;
    student_number: string;
    module_id: number;
    module_name: string;
    status:
    | "waiting"
    | "being_helped"
    | "not_queued"
    | "expired"
    | "helped"
    | "expired_waiting"
    | "left_queue";
    joined_at: string | null;
    waiting_expires_at: string | null;
    selected_at: string | null;
    help_expires_at: string | null;
    completed_at: string | null;
};

// Describes the office hours queue response data expected by this file.
type OfficeHoursQueueResponse = {
    waiting: OfficeHoursEntry[];
    helping: OfficeHoursEntry[];
    helped: OfficeHoursEntry[];
    waiting_count: number;
    helping_count: number;
    helped_count: number;
    help_duration_minutes?: number;
    wait_duration_minutes?: number;
    office_hours_active?: boolean;
    session_started_at?: string | null;
    session_ends_at?: string | null;
    session_remaining_seconds?: number;
    session_duration_minutes?: number;
    message?: string;
};

// Builds the authorization header used by authenticated API requests.
const authHeader = () => ({
    Authorization: `Bearer ${localStorage.getItem("AUTOTA_AUTH_TOKEN")}`,
});

// Helper for display name used by this component.
const displayName = (entry: OfficeHoursEntry): string => {
    const name = `${entry.first_name || ""} ${entry.last_name || ""}`.trim();
    return name || entry.email || `Student ${entry.user_id}`;
};

// Formats clock time for this view.
const formatClockTime = (value: string | null | undefined): string => {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";

    return new Intl.DateTimeFormat("en-US", {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
    }).format(parsed);
};

// Formats countdown for this view.
const formatCountdown = (seconds: number): string => {
    const safeSeconds = Math.max(0, Math.ceil(seconds));
    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    const secs = safeSeconds % 60;

    if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
    return `${minutes}m ${secs}s`;
};

// Helper for history status label used by this component.
const historyStatusLabel = (entry: OfficeHoursEntry): string => {
    if (entry.status === "expired_waiting") return "Expired in Waiting";
    return "Helped";
};

// Helper for history started at used by this component.
const historyStartedAt = (entry: OfficeHoursEntry): string | null => {
    return entry.status === "expired_waiting" ? entry.joined_at : entry.selected_at;
};

// Helper for empty queue used by this component.
const emptyQueue = (): OfficeHoursQueueResponse => ({
    waiting: [],
    helping: [],
    helped: [],
    waiting_count: 0,
    helping_count: 0,
    helped_count: 0,
    help_duration_minutes: 30,
    wait_duration_minutes: 30,
    office_hours_active: false,
    session_started_at: null,
    session_ends_at: null,
    session_remaining_seconds: 0,
    session_duration_minutes: 120,
});

// Displays the office-hours queue and manages helping sessions.
export default function AdminOfficeHours() {
    // Reads the school, class, or assignment identifiers from the current route.
    const { school_id, class_id } = useParams<{
        school_id: string;
        class_id: string;
    }>();
    const schoolId = school_id || "";
    const classId = Number(class_id || 0);

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [className, setClassName] = useState("");
    const [queue, setQueue] = useState<OfficeHoursQueueResponse>(emptyQueue);
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [message, setMessage] = useState("");
    const [errorMessage, setErrorMessage] = useState("");
    const [nowMs, setNowMs] = useState(() => Date.now());

    const queueRequestRef = useRef<{ classId: number; controller: AbortController } | null>(null);
    const currentClassRef = useRef(classId);
    currentClassRef.current = classId;

    useEffect(() => {
        setQueue(emptyQueue);
        setIsSaving(false);
        setMessage("");
        setErrorMessage("");
        return () => {
            queueRequestRef.current?.controller.abort();
            queueRequestRef.current = null;
        };
    }, [classId]);

    // Keeps the apply queue callback stable until its dependencies change.
    const applyQueue = useCallback((nextQueue: OfficeHoursQueueResponse) => {
        setQueue({
            waiting: Array.isArray(nextQueue?.waiting) ? nextQueue.waiting : [],
            helping: Array.isArray(nextQueue?.helping) ? nextQueue.helping : [],
            helped: Array.isArray(nextQueue?.helped) ? nextQueue.helped : [],
            waiting_count: Number(nextQueue?.waiting_count || 0),
            helping_count: Number(nextQueue?.helping_count || 0),
            helped_count: Number(nextQueue?.helped_count || 0),
            help_duration_minutes: Number(nextQueue?.help_duration_minutes || 30),
            wait_duration_minutes: Number(nextQueue?.wait_duration_minutes || 30),
            office_hours_active: Boolean(nextQueue?.office_hours_active),
            session_started_at: nextQueue?.session_started_at || null,
            session_ends_at: nextQueue?.session_ends_at || null,
            session_remaining_seconds: Number(nextQueue?.session_remaining_seconds || 0),
            session_duration_minutes: Number(nextQueue?.session_duration_minutes || 120),
            message: nextQueue?.message,
        });
    }, []);

    // Keeps the load queue callback stable until its dependencies change.
    const loadQueue = useCallback(async (showLoading = false) => {
        if (!Number.isSafeInteger(classId) || classId <= 0) {
            setIsLoading(false);
            return;
        }
        if (queueRequestRef.current?.classId === classId) return;
        queueRequestRef.current?.controller.abort();
        const controller = new AbortController();
        queueRequestRef.current = { classId, controller };
        if (showLoading) setIsLoading(true);

        try {
            // Fetches the server data needed for this operation.
            const response = await axios.get<OfficeHoursQueueResponse>(
                `${import.meta.env.VITE_API_URL}/office_hours/office_hours_queue`,
                {
                    headers: authHeader(),
                    params: { class_id: classId },
                    signal: controller.signal,
                },
            );
            if (controller.signal.aborted || currentClassRef.current !== classId) return;
            applyQueue(response.data);
            setErrorMessage("");
        } catch (error: any) {
            if (controller.signal.aborted || currentClassRef.current !== classId) return;
            setErrorMessage(
                error?.response?.data?.message ||
                "Could not load the office-hours queue.",
            );
        } finally {
            if (queueRequestRef.current?.controller === controller) queueRequestRef.current = null;
            if (!controller.signal.aborted && currentClassRef.current === classId && showLoading) setIsLoading(false);
        }
    }, [applyQueue, classId]);

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (!schoolId || !classId) return;

        const controller = new AbortController();
        setClassName("");
        axios
            .get<ClassAccessResponse>(
                `${import.meta.env.VITE_API_URL}/classes/validate_class_access/${classId}`,
                {
                    signal: controller.signal,
                    headers: authHeader(),
                    params: { school_id: schoolId, role_context: "admin" },
                },
            )
            .then((response) => { if (!controller.signal.aborted) setClassName(response.data?.name || ""); })
            .catch(() => { if (!controller.signal.aborted) setClassName(""); });
        return () => controller.abort();
    }, [classId, schoolId]);

    // Loads or refreshes view data when the dependencies below change.
    useEffect(() => {
        loadQueue(true);
    }, [loadQueue]);

    // Registers browser listeners and cleans them up when this effect reruns or the component unmounts.
    useEffect(() => {
        const interval = window.setInterval(() => {
            if (document.visibilityState === "visible") loadQueue(false);
        }, 10_000);

        // Refreshes when visible for this view.
        const refreshWhenVisible = () => {
            if (document.visibilityState === "visible") loadQueue(false);
        };

        window.addEventListener("focus", refreshWhenVisible);
        document.addEventListener("visibilitychange", refreshWhenVisible);

        return () => {
            window.clearInterval(interval);
            window.removeEventListener("focus", refreshWhenVisible);
            document.removeEventListener("visibilitychange", refreshWhenVisible);
        };
    }, [loadQueue]);

    // Starts a timer for periodic updates and clears it during effect cleanup.
    useEffect(() => {
        const interval = window.setInterval(() => setNowMs(Date.now()), 1000);
        return () => window.clearInterval(interval);
    }, []);

    const sessionEndsMs = queue.session_ends_at
        ? Date.parse(queue.session_ends_at)
        : 0;
    const sessionRemainingSeconds = sessionEndsMs && Number.isFinite(sessionEndsMs)
        ? Math.max(0, Math.ceil((sessionEndsMs - nowMs) / 1000))
        : Math.max(0, Number(queue.session_remaining_seconds || 0));
    const officeHoursActive = Boolean(
        queue.office_hours_active && sessionRemainingSeconds > 0,
    );
    const sessionDurationHours = Math.max(
        1,
        Math.round(Number(queue.session_duration_minutes || 120) / 60),
    );

    // Starts office hours for this view.
    const startOfficeHours = async () => {
        if (!classId || isSaving || officeHoursActive) return;
        setIsSaving(true);
        setMessage("");
        setErrorMessage("");

        try {
            // Sends this operation and its payload to the server.
            const response = await axios.post<OfficeHoursQueueResponse>(
                `${import.meta.env.VITE_API_URL}/office_hours/office_hours_start_session`,
                { class_id: classId },
                { headers: authHeader() },
            );
            if (currentClassRef.current !== classId) return;
            applyQueue(response.data);
            setMessage(
                response.data?.message ||
                `Office hours started for ${sessionDurationHours} hours.`,
            );
        } catch (error: any) {
            if (currentClassRef.current !== classId) return;
            setErrorMessage(
                error?.response?.data?.message ||
                "Could not start office hours.",
            );
        } finally {
            if (currentClassRef.current === classId) setIsSaving(false);
        }
    };

    // Starts helping for this view.
    const startHelping = async (studentId: number) => {
        if (!classId || !studentId || isSaving || !officeHoursActive) return;
        setIsSaving(true);
        setMessage("");
        setErrorMessage("");

        try {
            // Sends this operation and its payload to the server.
            const response = await axios.post<OfficeHoursQueueResponse>(
                `${import.meta.env.VITE_API_URL}/office_hours/office_hours_help_student`,
                {
                    class_id: classId,
                    student_id: studentId,
                },
                { headers: authHeader() },
            );
            if (currentClassRef.current !== classId) return;
            applyQueue(response.data);
            setMessage(
                response.data?.message ||
                `The student can now submit without a cooldown for up to ${queue.help_duration_minutes || 30} minutes.`,
            );
        } catch (error: any) {
            if (currentClassRef.current !== classId) return;
            setErrorMessage(
                error?.response?.data?.message ||
                "Could not start the help session.",
            );
        } finally {
            if (currentClassRef.current === classId) setIsSaving(false);
        }
    };

    // Ends helping for this view.
    const endHelping = async (studentId: number) => {
        if (!classId || !studentId || isSaving) return;
        setIsSaving(true);
        setMessage("");
        setErrorMessage("");

        try {
            // Sends this operation and its payload to the server.
            const response = await axios.post<OfficeHoursQueueResponse>(
                `${import.meta.env.VITE_API_URL}/office_hours/office_hours_complete_student`,
                {
                    class_id: classId,
                    student_id: studentId,
                },
                { headers: authHeader() },
            );
            if (currentClassRef.current !== classId) return;
            applyQueue(response.data);
            setMessage(response.data?.message || "The help session ended.");
        } catch (error: any) {
            if (currentClassRef.current !== classId) return;
            setErrorMessage(
                error?.response?.data?.message ||
                "Could not end the help session.",
            );
        } finally {
            if (currentClassRef.current === classId) setIsSaving(false);
        }
    };

    // Renders the interface using the current data and interaction state.
    return (
        <div className="projects-page admin-office-hours-page">
            {/* Sets the page title and document metadata. */}
            <Helmet>
                <title>[Admin] Office Hours | MAAT</title>
            </Helmet>

            {/* Displays the navigation and actions available on this page. */}
            <MenuComponent
            />

            {/* Shows the current location and links back to parent pages. */}
            <DirectoryBreadcrumbs
                items={[
                    { label: "School Selection", to: "/schools" },
                    {
                        label: "Class Selection",
                        to: `/admin/school/${schoolId}/classes`,
                    },
                    {
                        label: "Admin Menu",
                        to: `/admin/school/${schoolId}/class/${classId}/menu`,
                    },
                    { label: "Office Hours" },
                ]}
                trailingSeparator={true}
            />

            <div className="pageTitle">
                {className ? `${className} Office Hours` : "Office Hours"}
            </div>

            <main className="admin-office-hours-shell">
                <section
                    className={`office-hours-session-card ${officeHoursActive ? "is-active" : "is-inactive"}`}
                    aria-label="Office hours activation status"
                >
                    <div className="office-hours-session-copy">
                        <span className="office-hours-kicker">Office hours session</span>
                        <div className="office-hours-session-heading-row">
                            <h2>{officeHoursActive ? "Office Hours Active" : "Office Hours Inactive"}</h2>
                            <span
                                className={`office-hours-session-status ${officeHoursActive ? "is-active" : "is-inactive"}`}
                            >
                                {officeHoursActive ? "Active" : "Inactive"}
                            </span>
                        </div>
                        {officeHoursActive ? (
                            <p>
                                Students can see and join Office Hours until {formatClockTime(queue.session_ends_at)}.
                                The session closes automatically after {sessionDurationHours} hours.
                            </p>
                        ) : (
                            <p>
                                Students cannot see or join Office Hours until an admin starts a session.
                                Each session stays open for {sessionDurationHours} hours.
                            </p>
                        )}
                    </div>

                    <div className="office-hours-session-actions">
                        {officeHoursActive ? (
                            <div className="office-hours-session-countdown" role="status">
                                <FaClock aria-hidden="true" />
                                <span>Time remaining</span>
                                <strong>{formatCountdown(sessionRemainingSeconds)}</strong>
                            </div>
                        ) : (
                            <button
                                type="button"
                                className="office-hours-start-button"
                                onClick={startOfficeHours}
                                disabled={isSaving || isLoading}
                            >
                                <FaPlay aria-hidden="true" />
                                {isSaving ? "Starting..." : `Start ${sessionDurationHours}-Hour Session`}
                            </button>
                        )}
                    </div>
                </section>

                <section className="office-hours-intro">
                    <div className="office-hours-intro-copy">
                        <span className="office-hours-kicker">Queue management</span>
                        <p>
                            Students may wait for up to {queue.wait_duration_minutes || 30} minutes.
                            Once you click <strong>Help</strong>, the student can submit without a
                            cooldown for up to {queue.help_duration_minutes || 30} minutes, but never
                            beyond the end of the active office-hours session.
                        </p>
                    </div>
                    <button
                        type="button"
                        className="office-hours-refresh-button"
                        onClick={() => loadQueue(false)}
                        disabled={isSaving}
                    >
                        <FaRedo aria-hidden="true" />
                        Refresh
                    </button>
                </section>

                <section className="office-hours-summary" aria-label="Office hours summary">
                    <div className="office-hours-stat is-waiting">
                        <span>Waiting</span>
                        <strong>{queue.waiting_count}</strong>
                    </div>
                    <div className="office-hours-stat is-helping">
                        <span>Currently Helping</span>
                        <strong>{queue.helping_count}</strong>
                    </div>
                    <div className="office-hours-stat is-helped">
                        <span>Helped</span>
                        <strong>{queue.helped_count}</strong>
                    </div>
                </section>

                {message ? (
                    <div className="office-hours-notice is-success" role="status">
                        {message}
                    </div>
                ) : null}
                {errorMessage ? (
                    <div className="office-hours-notice is-error" role="alert">
                        {errorMessage}
                    </div>
                ) : null}

                <section
                    className="office-hours-panel is-waiting"
                    aria-labelledby="waiting-heading"
                >
                    <header className="office-hours-panel-header">
                        <div>
                            <h2 id="waiting-heading">Waiting</h2>
                            <p>
                                The table remains visible when Office Hours are inactive.
                                Students can only join during an active session.
                            </p>
                        </div>
                        <span className="office-hours-count">{queue.waiting_count}</span>
                    </header>

                    <div className="office-hours-table-wrap">
                        <table className="office-hours-table">
                            <thead>
                                <tr>
                                    <th scope="col">Student</th>
                                    <th scope="col">Module</th>
                                    <th scope="col">Joined</th>
                                    <th scope="col">Expires</th>
                                    <th scope="col" className="office-hours-action-column">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {isLoading ? (
                                    <tr>
                                        <td className="office-hours-table-empty" colSpan={5}>Loading the queue...</td>
                                    </tr>
                                ) : queue.waiting.length === 0 ? (
                                    <tr>
                                        <td className="office-hours-table-empty" colSpan={5}>No students are waiting.</td>
                                    </tr>
                                ) : (
                                    queue.waiting.map((entry) => (
                                        <tr key={entry.id}>
                                            <td className="office-hours-student-cell">
                                                <strong>{displayName(entry)}</strong>
                                                <span>{entry.student_number || entry.email}</span>
                                            </td>
                                            <td>{entry.module_name || `Module ${entry.module_id}`}</td>
                                            <td>{formatClockTime(entry.joined_at)}</td>
                                            <td>
                                                <span className="office-hours-expiration is-waiting">
                                                    {formatClockTime(entry.waiting_expires_at)}
                                                </span>
                                            </td>
                                            <td className="office-hours-action-cell">
                                                <button
                                                    type="button"
                                                    className="primary"
                                                    disabled={isSaving || !officeHoursActive}
                                                    onClick={() => startHelping(entry.user_id)}
                                                    title={officeHoursActive ? "Help this student" : "Start Office Hours first"}
                                                >
                                                    Help
                                                </button>
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>
                </section>

                <section
                    className="office-hours-panel is-helping"
                    aria-labelledby="helping-heading"
                >
                    <header className="office-hours-panel-header">
                        <div>
                            <h2 id="helping-heading">Currently Helping</h2>
                            <p>Cooldown-free submissions end at the earlier of the help timer or the office-hours session end.</p>
                        </div>
                        <span className="office-hours-count">{queue.helping_count}</span>
                    </header>

                    <div className="office-hours-table-wrap">
                        <table className="office-hours-table">
                            <thead>
                                <tr>
                                    <th scope="col">Student</th>
                                    <th scope="col">Module</th>
                                    <th scope="col">Started</th>
                                    <th scope="col">Expires</th>
                                    <th scope="col" className="office-hours-action-column">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {isLoading ? (
                                    <tr>
                                        <td className="office-hours-table-empty" colSpan={5}>Loading help sessions...</td>
                                    </tr>
                                ) : queue.helping.length === 0 ? (
                                    <tr>
                                        <td className="office-hours-table-empty" colSpan={5}>No active help sessions.</td>
                                    </tr>
                                ) : (
                                    queue.helping.map((entry) => (
                                        <tr key={entry.id}>
                                            <td className="office-hours-student-cell">
                                                <strong>{displayName(entry)}</strong>
                                                <span>{entry.student_number || entry.email}</span>
                                            </td>
                                            <td>{entry.module_name || `Module ${entry.module_id}`}</td>
                                            <td>{formatClockTime(entry.selected_at)}</td>
                                            <td>
                                                <span className="office-hours-expiration is-helping">
                                                    {formatClockTime(entry.help_expires_at)}
                                                </span>
                                            </td>
                                            <td className="office-hours-action-cell">
                                                <button
                                                    type="button"
                                                    className="success"
                                                    disabled={isSaving}
                                                    onClick={() => endHelping(entry.user_id)}
                                                >
                                                    <FaCheck aria-hidden="true" />
                                                    Done
                                                </button>
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>
                </section>

                <section
                    className="office-hours-panel is-helped"
                    aria-labelledby="helped-heading"
                >
                    <header className="office-hours-panel-header">
                        <div>
                            <h2 id="helped-heading">Helped</h2>
                            <p>Completed help sessions and students whose wait expired.</p>
                        </div>
                        <span className="office-hours-count">{queue.helped_count}</span>
                    </header>

                    <div className="office-hours-table-wrap">
                        <table className="office-hours-table">
                            <thead>
                                <tr>
                                    <th scope="col">Student</th>
                                    <th scope="col">Module</th>
                                    <th scope="col">Status</th>
                                    <th scope="col">Started</th>
                                    <th scope="col">Finished</th>
                                </tr>
                            </thead>
                            <tbody>
                                {isLoading ? (
                                    <tr>
                                        <td className="office-hours-table-empty" colSpan={5}>Loading history...</td>
                                    </tr>
                                ) : queue.helped.length === 0 ? (
                                    <tr>
                                        <td className="office-hours-table-empty" colSpan={5}>No completed or expired office-hours sessions yet.</td>
                                    </tr>
                                ) : (
                                    queue.helped.map((entry) => (
                                        <tr key={entry.id}>
                                            <td className="office-hours-student-cell">
                                                <strong>{displayName(entry)}</strong>
                                                <span>{entry.student_number || entry.email}</span>
                                            </td>
                                            <td>{entry.module_name || `Module ${entry.module_id}`}</td>
                                            <td><strong>{historyStatusLabel(entry)}</strong></td>
                                            <td>{formatClockTime(historyStartedAt(entry))}</td>
                                            <td>{formatClockTime(entry.completed_at)}</td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>
                </section>
            </main>
        </div>
    );
}
