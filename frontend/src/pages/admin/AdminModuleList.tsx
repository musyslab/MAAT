// AdminModuleList.tsx: Displays the module calendar and manages module creation, dates, and edits.
import {
    CSSProperties,
    KeyboardEvent,
    useEffect,
    useMemo,
    useRef,
    useState,
} from "react";
import axios from "axios";
import { createPortal } from "react-dom";
import DatePicker from "react-datepicker";
import "react-datepicker/dist/react-datepicker.css";
import { eachDayOfInterval } from "date-fns";
import { Helmet } from "react-helmet";
import { useNavigate, useParams } from "react-router-dom";
import {
    FaCalendarAlt,
    FaChevronLeft,
    FaChevronRight,
    FaListUl,
    FaEdit,
    FaSave,
    FaTimes,
} from "react-icons/fa";

import DefaultContentImport from "../components/DefaultContentImport";
import MenuComponent from "../components/MenuComponent";
import "../../styling/ModuleList.scss";
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs";

// Describes the module object data expected by this file.
interface ModuleObject {
    Id: number;
    ClassId: number;
    Name: string;
    Start: string;
    End: string;
    MainProjectId?: number;
    TotalSubmissions?: number;
    PracticeTotalSubmissions?: number;
    PracticeProblemsEnabled?: boolean;
}

// Describes the class access response data expected by this file.
interface ClassAccessResponse {
    id?: number;
    name?: string;
    school_id?: number;
    school_name?: string;
}

// Describes the calendar day data expected by this file.
type CalendarDay = {
    date: Date;
    isCurrentMonth: boolean;
    key: string;
};

// Describes the calendar week segment data expected by this file.
type CalendarWeekSegment = {
    module: ModuleObject;
    startColumn: number;
    span: number;
    row: number;
    startsBeforeWeek: boolean;
    endsAfterWeek: boolean;
};

// Describes the calendar week data expected by this file.
type CalendarWeek = {
    key: string;
    days: CalendarDay[];
    segments: CalendarWeekSegment[];
};

// Describes the date range data expected by this file.
type DateRange = {
    start: Date;
    end: Date;
};

// Describes the date time field props data expected by this file.
type DateTimeFieldProps = {
    label: string;
    value: string;
    onChange: (value: string) => void;
    highlightedDates: Date[];
    blockedDates: Date[];
    timeClassName: (time: Date) => string | null;
    selectsStart?: boolean;
    selectsEnd?: boolean;
    startDate: Date | null;
    endDate: Date | null;
    hasError: boolean;
};

// Builds the authorization header used by authenticated API requests.
const authHeader = () => ({
    Authorization: `Bearer ${localStorage.getItem("AUTOTA_AUTH_TOKEN")}`,
});

// Helper for pad used by this component.
const pad = (n: number) => n.toString().padStart(2, "0");

// Formats date time local for this view.
const formatDateTimeLocal = (date: Date): string => {
    return [
        date.getFullYear(),
        "-",
        pad(date.getMonth() + 1),
        "-",
        pad(date.getDate()),
        "T",
        pad(date.getHours()),
        ":",
        pad(date.getMinutes()),
    ].join("");
};

// Parses date time local for this view.
const parseDateTimeLocal = (value: string): Date | null => {
    if (!value) return null;

    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
};

// Helper for default module start used by this component.
const defaultModuleStart = () => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
};

// Helper for default module end used by this component.
const defaultModuleEnd = () => {
    const d = new Date();
    d.setDate(d.getDate() + 7);
    d.setHours(23, 59, 0, 0);
    return d;
};

// Returns injected times for this view.
const getInjectedTimes = (dateValue: Date | null): Date[] => {
    if (!dateValue) return [];

    const endOfDay = new Date(dateValue.getTime());
    endOfDay.setHours(23, 59, 0, 0);

    return [endOfDay];
};

// Builds the dates highlighted by the selected calendar range.
const getDateRangeHighlightDates = (
    start: Date | null,
    end: Date | null,
): Date[] => {
    if (!start || !end || start.getTime() > end.getTime()) return [];

    return eachDayOfInterval({ start, end });
};

// Finds calendar days blocked by the existing scheduling ranges.
const getFullyBlockedDates = (ranges: DateRange[]): Date[] => {
    const dates: Date[] = [];

    ranges.forEach((range) => {
        const currentDay = new Date(range.start);
        currentDay.setHours(0, 0, 0, 0);

        const lastDay = new Date(range.end);
        lastDay.setHours(0, 0, 0, 0);

        while (currentDay <= lastDay) {
            const dayStart = new Date(currentDay);
            dayStart.setHours(0, 0, 0, 0);

            const dayEnd = new Date(currentDay);
            dayEnd.setHours(23, 59, 0, 0);

            if (range.start <= dayStart && range.end >= dayEnd) {
                dates.push(new Date(currentDay));
            }

            currentDay.setDate(currentDay.getDate() + 1);
        }
    });

    return dates;
};

// Checks whether a date falls within the supplied scheduling range.
const dateOverlapsRange = (date: Date, ranges: DateRange[]): boolean =>
    ranges.some((range) => date > range.start && date < range.end);

// Checks the proposed date range against existing ranges for conflicts.
const dateRangeOverlapsRanges = (
    start: Date | null,
    end: Date | null,
    ranges: DateRange[],
): boolean => {
    if (!start || !end) return false;

    return ranges.some((range) => start < range.end && end > range.start);
};

// Renders the date time field interface and coordinates its local data and interactions.
function DateTimeField({
    label,
    value,
    onChange,
    highlightedDates,
    blockedDates,
    timeClassName,
    selectsStart,
    selectsEnd,
    startDate,
    endDate,
    hasError,
}: DateTimeFieldProps) {
    const selectedDate = parseDateTimeLocal(value);

    // Renders the interface using the current data and interaction state.
    return (
        <div
            className={`form-field input-field datetime-field${hasError ? " input-error" : ""}`}
        >
            <label>{label}</label>

            <DatePicker
                selected={selectedDate}
                onChange={(date: Date | null) =>
                    onChange(date ? formatDateTimeLocal(date) : "")
                }
                showTimeSelect
                timeFormat="h:mm aa"
                timeIntervals={15}
                injectTimes={getInjectedTimes(selectedDate || new Date())}
                timeCaption="Time"
                dateFormat="yyyy-MM-dd h:mm aa"
                highlightDates={[
                    {
                        "react-datepicker__day--highlighted": highlightedDates,
                    },
                    {
                        "react-datepicker__day--highlighted-red": blockedDates,
                    },
                ]}
                timeClassName={timeClassName}
                selectsStart={selectsStart}
                selectsEnd={selectsEnd}
                startDate={startDate}
                endDate={endDate}
                placeholderText={`Select ${label.toLowerCase()}`}
            />
        </div>
    );
}

// Displays the module calendar and manages module creation, dates, and edits.
export default function AdminModuleList() {
    // Reads the school, class, or assignment identifiers from the current route.
    const { school_id, class_id, id } = useParams<{
        school_id: string;
        class_id: string;
        id: string;
    }>();
    const navigate = useNavigate();
    const schoolId = school_id || "";
    const classId = class_id || id || "";

    // Keeps overlap dialog available across renders without triggering a state update.
    const overlapDialog = useRef<HTMLDialogElement>(null);
    // Keeps overlap decision available across renders without triggering a state update.
    const overlapDecision = useRef<((confirmed: boolean) => void) | null>(null);
    // Keeps the values that drive this component’s display and user interactions in React state.
    const [overlapWarningOpen, setOverlapWarningOpen] = useState(false);

    // Continues the pending scheduling action after an overlap is confirmed.
    const confirmOverlap = (): Promise<boolean> => new Promise(resolve => {
        overlapDecision.current = resolve;
        overlapDialog.current?.showModal();
        setOverlapWarningOpen(true);
    });

    // Closes overlap warning for this view.
    const closeOverlapWarning = (confirmed: boolean) => {
        const resolve = overlapDecision.current;
        overlapDecision.current = null;
        overlapDialog.current?.close();
        setOverlapWarningOpen(false);
        resolve?.(confirmed);
    };

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (!overlapWarningOpen) return;
        const html = document.documentElement;
        const previous = html.style.overflow;
        const bodyPrevious = document.body.style.overflow;
        html.style.overflow = "hidden";
        document.body.style.overflow = "hidden";
        return () => {
            html.style.overflow = previous;
            document.body.style.overflow = bodyPrevious;
        };
    }, [overlapWarningOpen]);

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => () => {
        overlapDecision.current?.(false);
    }, []);

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [className, setClassName] = useState("");
    const [modules, setModules] = useState<ModuleObject[]>([]);
    const [calendarDate, setCalendarDate] = useState<Date>(new Date());
    const [showCreateModule, setShowCreateModule] = useState(false);
    const [editingModuleId, setEditingModuleId] = useState<number | null>(null);
    const [editModuleName, setEditModuleName] = useState("");
    const [editModuleStart, setEditModuleStart] = useState("");
    const [editModuleEnd, setEditModuleEnd] = useState("");
    const [savingEditModule, setSavingEditModule] = useState(false);
    const [editOverlapError, setEditOverlapError] = useState(false);
    const [newModuleName, setNewModuleName] = useState("");
    const [newModuleStart, setNewModuleStart] = useState(
        formatDateTimeLocal(defaultModuleStart()),
    );
    const [newModuleEnd, setNewModuleEnd] = useState(
        formatDateTimeLocal(defaultModuleEnd()),
    );
    const [savingModule, setSavingModule] = useState(false);
    const [overlapError, setOverlapError] = useState(false);
    const [viewMode, setViewMode] = useState<"list" | "calendar">("list");

    // Parses date for this view.
    const parseDate = (value: string): Date | null => {
        const d = new Date(value);
        return Number.isNaN(d.getTime()) ? null : d;
    };

    // Helper for same day used by this component.
    const sameDay = (a: Date, b: Date): boolean =>
        a.getFullYear() === b.getFullYear() &&
        a.getMonth() === b.getMonth() &&
        a.getDate() === b.getDate();

    // Starts of day for this view.
    const startOfDay = (d: Date): Date => {
        const next = new Date(d);
        next.setHours(0, 0, 0, 0);
        return next;
    };

    // Ends of day for this view.
    const endOfDay = (d: Date): Date => {
        const next = new Date(d);
        next.setHours(23, 59, 59, 999);
        return next;
    };

    // Formats month title for this view.
    const formatMonthTitle = (date: Date): string =>
        new Intl.DateTimeFormat("en-US", {
            month: "long",
            year: "numeric",
        }).format(date);

    // Formats time for this view.
    const formatTime = (value: string): string => {
        const d = parseDate(value);
        if (!d) return value;

        return new Intl.DateTimeFormat("en-US", {
            hour: "numeric",
            minute: "2-digit",
            hour12: true,
        }).format(d);
    };

    // Formats short date for this view.
    const formatShortDate = (value: string): string => {
        const d = parseDate(value);
        if (!d) return value;

        return new Intl.DateTimeFormat("en-US", {
            month: "short",
            day: "numeric",
        }).format(d);
    };

    // Formats date12h for this view.
    const formatDate12h = (value: string): string => {
        const d = parseDate(value);
        if (!d) return value;

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
        module: ModuleObject,
    ): "active" | "upcoming" | "ended" => {
        const startMs = Date.parse(module.Start);
        const endMs = Date.parse(module.End);
        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "upcoming";

        const now = Date.now();
        if (now >= startMs && now <= endMs) return "active";
        return now < startMs ? "upcoming" : "ended";
    };

    // Returns module status label for this view.
    const getModuleStatusLabel = (module: ModuleObject): string => {
        const status = getModuleStatus(module);
        if (status === "active") return "Active";
        if (status === "ended") return "Ended";
        return "Upcoming";
    };

    // Checks module active now for this view.
    const isModuleActiveNow = (m: ModuleObject): boolean => {
        const startMs = Date.parse(m.Start);
        const endMs = Date.parse(m.End);
        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return false;

        const now = Date.now();
        return now >= startMs && now <= endMs;
    };

    // Helper for module occurs on date used by this component.
    const moduleOccursOnDate = (module: ModuleObject, date: Date): boolean => {
        const start = parseDate(module.Start);
        const end = parseDate(module.End);
        if (!start || !end) return false;

        return start <= endOfDay(date) && end >= startOfDay(date);
    };

    // Helper for clamp used by this component.
    const clamp = (value: number, min: number, max: number): number =>
        Math.min(Math.max(value, min), max);

    // Returns day index within week for this view.
    const getDayIndexWithinWeek = (date: Date, weekStart: Date): number => {
        const dayMs = 24 * 60 * 60 * 1000;
        return Math.floor(
            (startOfDay(date).getTime() - startOfDay(weekStart).getTime()) / dayMs,
        );
    };

    // Returns module date label for this view.
    const getModuleDateLabel = (module: ModuleObject): string => {
        const start = parseDate(module.Start);
        const end = parseDate(module.End);

        if (!start || !end) {
            return `${module.Start} - ${module.End}`;
        }

        if (sameDay(start, end)) {
            return `${formatTime(module.Start)} - ${formatTime(module.End)}`;
        }

        return `${formatShortDate(module.Start)}, ${formatTime(module.Start)} - ${formatShortDate(module.End)}, ${formatTime(module.End)}`;
    };

    // Loads class name for this view.
    const loadClassName = () => {
        if (!classId) {
            setClassName("");
            return;
        }

        axios
            .get<ClassAccessResponse>(
                `${import.meta.env.VITE_API_URL}/classes/validate_class_access/${classId}`,
                {
                    headers: authHeader(),
                    params: {
                        ...(schoolId ? { school_id: schoolId } : {}),
                        role_context: "admin",
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

    // Loads modules for this view.
    const loadModules = () => {
        if (!classId) {
            setModules([]);
            return;
        }

        axios
            .get(
                `${import.meta.env.VITE_API_URL}/assignment_tracking/get_modules_by_class_id?id=${classId}`,
                {
                    headers: authHeader(),
                },
            )
            .then((res) => {
                const parsed: ModuleObject[] = (res.data as any[]).map((item: any) =>
                    typeof item === "string"
                        ? (JSON.parse(item) as ModuleObject)
                        : (item as ModuleObject),
                );

                setModules(parsed);

                const firstModuleDate = parsed
                    .map((m) => parseDate(m.Start))
                    .filter((d): d is Date => !!d)
                    .sort((a, b) => a.getTime() - b.getTime())[0];

                if (firstModuleDate) {
                    setCalendarDate(
                        new Date(
                            firstModuleDate.getFullYear(),
                            firstModuleDate.getMonth(),
                            1,
                        ),
                    );
                }
            })
            .catch((err) => {
                console.log(err);
                setModules([]);
            });
    };

    // Loads or refreshes view data when the dependencies below change.
    useEffect(() => {
        loadClassName();
        loadModules();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [schoolId, classId]);

    // Recomputes sorted modules only when its dependencies change.
    const sortedModules = useMemo(() => {
        return [...modules].sort((a, b) => {
            const da = Date.parse(a.Start);
            const db = Date.parse(b.Start);
            const aInvalid = Number.isNaN(da);
            const bInvalid = Number.isNaN(db);
            if (aInvalid && bInvalid) return 0;
            if (aInvalid) return 1;
            if (bInvalid) return -1;
            return da - db;
        });
    }, [modules]);

    // Recomputes module conflict ranges only when its dependencies change.
    const moduleConflictRanges = useMemo<DateRange[]>(() => {
        return modules
            .map((m) => {
                const start = parseDate(m.Start);
                const end = parseDate(m.End);

                return start && end ? { start, end } : null;
            })
            .filter((range): range is DateRange => !!range);
    }, [modules]);

    // Returns module conflict ranges for this view.
    const getModuleConflictRanges = (excludedModuleId?: number): DateRange[] => {
        return modules
            .filter((module) => module.Id !== excludedModuleId)
            .map((module) => {
                const start = parseDate(module.Start);
                const end = parseDate(module.End);

                return start && end ? { start, end } : null;
            })
            .filter((range): range is DateRange => !!range);
    };

    // Recomputes new module start date only when its dependencies change.
    const newModuleStartDate = useMemo(
        () => parseDateTimeLocal(newModuleStart),
        [newModuleStart],
    );
    // Recomputes new module end date only when its dependencies change.
    const newModuleEndDate = useMemo(
        () => parseDateTimeLocal(newModuleEnd),
        [newModuleEnd],
    );
    // Recomputes edit module start date only when its dependencies change.
    const editModuleStartDate = useMemo(
        () => parseDateTimeLocal(editModuleStart),
        [editModuleStart],
    );
    // Recomputes edit module end date only when its dependencies change.
    const editModuleEndDate = useMemo(
        () => parseDateTimeLocal(editModuleEnd),
        [editModuleEnd],
    );

    // Recomputes highlighted new module dates only when its dependencies change.
    const highlightedNewModuleDates = useMemo(
        () => getDateRangeHighlightDates(newModuleStartDate, newModuleEndDate),
        [newModuleStartDate, newModuleEndDate],
    );

    // Recomputes blocked new module dates only when its dependencies change.
    const blockedNewModuleDates = useMemo(
        () => getFullyBlockedDates(moduleConflictRanges),
        [moduleConflictRanges],
    );

    // Handles new module time colors for this view.
    const handleNewModuleTimeColors = (time: Date): string | null =>
        dateOverlapsRange(time, moduleConflictRanges)
            ? "react-datepicker__time--highlighted-red"
            : null;

    // Updates new module date for this view.
    const setNewModuleDate = (dateValue: string, isStart: boolean) => {
        const finalDate = parseDateTimeLocal(dateValue);

        if (isStart) {
            setNewModuleStart(finalDate ? formatDateTimeLocal(finalDate) : "");
        } else {
            setNewModuleEnd(finalDate ? formatDateTimeLocal(finalDate) : "");
        }

        const startToCheck = isStart ? finalDate : newModuleStartDate;
        const endToCheck = isStart ? newModuleEndDate : finalDate;
        setOverlapError(
            !!finalDate &&
            (dateOverlapsRange(finalDate, moduleConflictRanges) ||
                dateRangeOverlapsRanges(
                    startToCheck,
                    endToCheck,
                    moduleConflictRanges,
                )),
        );
    };

    // Helper for begin module edit used by this component.
    const beginModuleEdit = (module: ModuleObject) => {
        setShowCreateModule(false);
        setEditingModuleId(module.Id);
        setEditModuleName(module.Name);
        setEditModuleStart(formatDateTimeLocal(new Date(module.Start)));
        setEditModuleEnd(formatDateTimeLocal(new Date(module.End)));
        setEditOverlapError(false);
    };

    // Cancels module edit for this view.
    const cancelModuleEdit = () => {
        if (savingEditModule) return;

        setEditingModuleId(null);
        setEditModuleName("");
        setEditModuleStart("");
        setEditModuleEnd("");
        setEditOverlapError(false);
    };

    // Updates edit module date for this view.
    const setEditModuleDate = (dateValue: string, isStart: boolean) => {
        const conflictRanges = getModuleConflictRanges(
            editingModuleId ?? undefined,
        );
        const finalDate = parseDateTimeLocal(dateValue);

        if (isStart) {
            setEditModuleStart(finalDate ? formatDateTimeLocal(finalDate) : "");
        } else {
            setEditModuleEnd(finalDate ? formatDateTimeLocal(finalDate) : "");
        }

        const startToCheck = isStart ? finalDate : editModuleStartDate;
        const endToCheck = isStart ? editModuleEndDate : finalDate;
        setEditOverlapError(
            !!finalDate &&
            (dateOverlapsRange(finalDate, conflictRanges) ||
                dateRangeOverlapsRanges(startToCheck, endToCheck, conflictRanges)),
        );
    };

    // Saves module edit for this view.
    const saveModuleEdit = async () => {
        if (savingEditModule || overlapDecision.current) return;
        if (editingModuleId === null) return;

        const trimmedName = editModuleName.trim();
        if (!trimmedName || !editModuleStart || !editModuleEnd) {
            window.alert("Please enter a module name, start date, and end date.");
            return;
        }

        const start = new Date(editModuleStart);
        const end = new Date(editModuleEnd);

        if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
            window.alert("Please enter valid dates.");
            return;
        }

        if (start.getTime() >= end.getTime()) {
            window.alert("The module end date must be after the start date.");
            return;
        }

        const conflictRanges = getModuleConflictRanges(editingModuleId);
        if (dateRangeOverlapsRanges(start, end, conflictRanges)) {
            setEditOverlapError(true);
            if (!(await confirmOverlap())) return;
        }

        try {
            setSavingEditModule(true);
            await axios.post(
                `${import.meta.env.VITE_API_URL}/assignment_setup/update_module`,
                {
                    module_id: editingModuleId,
                    name: trimmedName,
                    start_date: editModuleStart,
                    end_date: editModuleEnd,
                },
                { headers: authHeader() },
            );

            setModules((current) =>
                current.map((module) =>
                    module.Id === editingModuleId
                        ? {
                            ...module,
                            Name: trimmedName,
                            Start: editModuleStart,
                            End: editModuleEnd,
                        }
                        : module,
                ),
            );
            setEditingModuleId(null);
            setEditModuleName("");
            setEditModuleStart("");
            setEditModuleEnd("");
            setEditOverlapError(false);
            loadModules();
        } catch (err) {
            console.log(err);
            window.alert("Could not save the module.");
        } finally {
            setSavingEditModule(false);
        }
    };

    // Recomputes calendar days only when its dependencies change.
    const calendarDays = useMemo<CalendarDay[]>(() => {
        const year = calendarDate.getFullYear();
        const month = calendarDate.getMonth();

        const firstOfMonth = new Date(year, month, 1);
        const start = new Date(firstOfMonth);
        start.setDate(firstOfMonth.getDate() - firstOfMonth.getDay());

        const days: CalendarDay[] = [];
        for (let i = 0; i < 42; i += 1) {
            const date = new Date(start);
            date.setDate(start.getDate() + i);

            days.push({
                date,
                isCurrentMonth: date.getMonth() === month,
                key: `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`,
            });
        }

        return days;
    }, [calendarDate]);

    // Recomputes calendar weeks only when its dependencies change.
    const calendarWeeks = useMemo<CalendarWeek[]>(() => {
        const weeks: CalendarWeek[] = [];

        for (let i = 0; i < calendarDays.length; i += 7) {
            const days = calendarDays.slice(i, i + 7);
            const weekStart = startOfDay(days[0].date);
            const weekEnd = endOfDay(days[6].date);

            const rowEndByRow: number[] = [];

            const segments: CalendarWeekSegment[] = sortedModules
                .filter((module) => {
                    const start = parseDate(module.Start);
                    const end = parseDate(module.End);
                    if (!start || !end) return false;

                    return start <= weekEnd && end >= weekStart;
                })
                .map((module) => {
                    const start = parseDate(module.Start) as Date;
                    const end = parseDate(module.End) as Date;

                    const startsBeforeWeek = start < weekStart;
                    const endsAfterWeek = end > weekEnd;

                    const startColumn = clamp(
                        getDayIndexWithinWeek(start, weekStart),
                        0,
                        6,
                    );
                    const endColumn = clamp(getDayIndexWithinWeek(end, weekStart), 0, 6);
                    const span = Math.max(1, endColumn - startColumn + 1);

                    let row = rowEndByRow.findIndex((rowEnd) => startColumn > rowEnd);

                    if (row === -1) {
                        row = rowEndByRow.length;
                        rowEndByRow.push(endColumn);
                    } else {
                        rowEndByRow[row] = endColumn;
                    }

                    return {
                        module,
                        startColumn,
                        span,
                        row,
                        startsBeforeWeek,
                        endsAfterWeek,
                    };
                });

            weeks.push({
                key: days[0].key,
                days,
                segments,
            });
        }

        return weeks;
    }, [calendarDays, sortedModules]);

    // Helper for go to previous month used by this component.
    const goToPreviousMonth = () => {
        setCalendarDate(
            (current) => new Date(current.getFullYear(), current.getMonth() - 1, 1),
        );
    };

    // Helper for go to next month used by this component.
    const goToNextMonth = () => {
        setCalendarDate(
            (current) => new Date(current.getFullYear(), current.getMonth() + 1, 1),
        );
    };

    // Helper for go to today used by this component.
    const goToToday = () => {
        const today = new Date();
        setCalendarDate(new Date(today.getFullYear(), today.getMonth(), 1));
    };

    // Returns admin class base path for this view.
    const getAdminClassBasePath = (): string => {
        return schoolId
            ? `/admin/school/${schoolId}/class/${classId}`
            : "/schools";
    };

    // Opens module for this view.
    const openModule = (moduleId: number) => {
        navigate(`${getAdminClassBasePath()}/module/${moduleId}/overview`);
    };

    // Handles module card key down for this view.
    const handleModuleCardKeyDown = (
        event: KeyboardEvent<HTMLElement>,
        moduleId: number,
    ) => {
        if (event.key !== "Enter" && event.key !== " ") return;

        event.preventDefault();
        openModule(moduleId);
    };

    // Helper for create module used by this component.
    const createModule = async () => {
        if (savingModule || overlapDecision.current) return;
        const trimmedName = newModuleName.trim();
        if (!trimmedName || !newModuleStart || !newModuleEnd) {
            window.alert("Please enter a module name, start date, and end date.");
            return;
        }

        const start = new Date(newModuleStart);
        const end = new Date(newModuleEnd);

        if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
            window.alert("Please enter valid dates.");
            return;
        }

        if (start.getTime() >= end.getTime()) {
            window.alert("The module end date must be after the start date.");
            return;
        }

        if (dateRangeOverlapsRanges(start, end, moduleConflictRanges)) {
            setOverlapError(true);
            if (!(await confirmOverlap())) return;
        }

        try {
            setSavingModule(true);
            // Sends this operation and its payload to the server.
            const res = await axios.post(
                `${import.meta.env.VITE_API_URL}/assignment_setup/create_module`,
                {
                    class_id: Number(classId),
                    name: trimmedName,
                    start_date: newModuleStart,
                    end_date: newModuleEnd,
                },
                { headers: authHeader() },
            );

            const moduleId = Number(res.data?.module_id ?? res.data?.id ?? res.data);
            setShowCreateModule(false);
            setNewModuleName("");
            setNewModuleStart(formatDateTimeLocal(defaultModuleStart()));
            setNewModuleEnd(formatDateTimeLocal(defaultModuleEnd()));
            setOverlapError(false);
            loadModules();

            if (moduleId > 0) {
                navigate(`${getAdminClassBasePath()}/module/${moduleId}/overview`);
            }
        } catch (err) {
            console.log(err);
            window.alert("Could not create the module.");
        } finally {
            setSavingModule(false);
        }
    };

    // Renders the interface using the current data and interaction state.
    return (
        <div className="projects-page">
            {createPortal(
                <dialog ref={overlapDialog} className="module-overlap-dialog"
                    aria-labelledby="module-overlap-title" aria-describedby="module-overlap-description"
                    onCancel={event => { event.preventDefault(); closeOverlapWarning(false); }}
                    onClose={() => {
                        setOverlapWarningOpen(false);
                        overlapDecision.current?.(false);
                        overlapDecision.current = null;
                    }}>
                    <span className="module-overlap-eyebrow">Schedule warning</span>
                    <h2 id="module-overlap-title">These dates overlap</h2>
                    <p id="module-overlap-description">
                        This module overlaps with another module in this class. You can keep these dates
                        and continue, or go back to adjust them.
                    </p>
                    <div className="module-overlap-actions">
                        <button type="button" autoFocus onClick={() => closeOverlapWarning(false)}>Go back</button>
                        <button type="button" className="module-overlap-continue"
                            onClick={() => closeOverlapWarning(true)}>Continue anyway</button>
                    </div>
                </dialog>, document.body)}
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
                    {
                        label: "Class Selection",
                        to: schoolId
                            ? `/admin/school/${schoolId}/classes`
                            : "/schools",
                    },
                    {
                        label: "Admin Menu",
                        to: schoolId
                            ? `/admin/school/${schoolId}/class/${classId}/menu`
                            : "/schools",
                    },
                    { label: "Module List" },
                ]}
            />

            <div className="pageTitle">
                {className ? `${className} Admin Module List` : "Admin Module List"}
            </div>

            <div className="module-calendar-command-row">
                {/* Displays the default content import component with the data and callbacks below. */}
                <DefaultContentImport key={classId} classId={classId} onImported={loadModules}
                    createOpen={showCreateModule}
                    onCreateCustom={() => {
                        cancelModuleEdit();
                        setShowCreateModule(true);
                    }}
                    onToggleCreate={() => {
                        cancelModuleEdit();
                        setShowCreateModule(current => !current);
                    }} />

                <div className="module-view-toggle" aria-label="Module view selector">
                    <button
                        type="button"
                        className={`module-view-toggle-button${viewMode === "list" ? " is-active" : ""}`}
                        onClick={() => setViewMode("list")}
                    >
                        <FaListUl aria-hidden="true" />
                        <span>List</span>
                    </button>
                    <button
                        type="button"
                        className={`module-view-toggle-button${viewMode === "calendar" ? " is-active" : ""}`}
                        onClick={() => setViewMode("calendar")}
                    >
                        <FaCalendarAlt aria-hidden="true" />
                        <span>Calendar</span>
                    </button>
                </div>
            </div>

            {showCreateModule && (
                <section className="calendar-create-card" aria-label="Create module">
                    <div className="calendar-create-card-header">
                        <div>
                            <span className="calendar-create-eyebrow">New Module</span>
                            <h2>Create module details</h2>
                            <p>Choose the name, start date, and end date for this module.</p>
                        </div>
                    </div>

                    <div className="calendar-create-grid">
                        <div className="form-field input-field module-name-field">
                            <label>Module Name</label>
                            <input
                                type="text"
                                value={newModuleName}
                                onChange={(e) => setNewModuleName(e.currentTarget.value)}
                                placeholder="Module 1"
                            />
                        </div>

                        <DateTimeField
                            label="Start Date"
                            value={newModuleStart}
                            onChange={(value) => setNewModuleDate(value, true)}
                            highlightedDates={highlightedNewModuleDates}
                            blockedDates={blockedNewModuleDates}
                            timeClassName={handleNewModuleTimeColors}
                            selectsStart
                            startDate={newModuleStartDate}
                            endDate={newModuleEndDate}
                            hasError={overlapError}
                        />

                        <DateTimeField
                            label="End Date"
                            value={newModuleEnd}
                            onChange={(value) => setNewModuleDate(value, false)}
                            highlightedDates={highlightedNewModuleDates}
                            blockedDates={blockedNewModuleDates}
                            timeClassName={handleNewModuleTimeColors}
                            selectsEnd
                            startDate={newModuleStartDate}
                            endDate={newModuleEndDate}
                            hasError={overlapError}
                        />
                    </div>

                    <div className="project-detail-edit-actions">
                        <button
                            className="project-action project-action-primary"
                            type="button"
                            onClick={createModule}
                            disabled={savingModule}
                        >
                            <FaSave aria-hidden="true" />
                            {savingModule ? "Creating..." : "Create Module"}
                        </button>

                        <button
                            className="project-action project-action-secondary"
                            type="button"
                            onClick={() => setShowCreateModule(false)}
                            disabled={savingModule}
                        >
                            <FaTimes aria-hidden="true" />
                            Cancel
                        </button>
                    </div>
                </section>
            )}

            {viewMode === "list" && sortedModules.length > 0 && (
                <section className="module-list-shell" aria-label="Module list">
                    <div className="module-list-header-row">
                        <div>
                            <h2>Modules</h2>
                        </div>
                    </div>

                    <div className="module-list-grid">
                        {sortedModules.map((module) => {
                            const status = getModuleStatus(module);
                            const active = status === "active";
                            const isEditing = editingModuleId === module.Id;
                            const editConflictRanges = getModuleConflictRanges(module.Id);
                            const editChanged =
                                isEditing &&
                                (editModuleName.trim() !== module.Name.trim() ||
                                    editModuleStart !==
                                    formatDateTimeLocal(new Date(module.Start)) ||
                                    editModuleEnd !== formatDateTimeLocal(new Date(module.End)));

                            // Renders the interface using the current data and interaction state.
                            return (
                                <article
                                    className={[
                                        "module-list-card",
                                        `is-${status}`,
                                        isEditing ? "is-editing" : "",
                                    ]
                                        .join(" ")
                                        .trim()}
                                    key={module.Id}
                                    role={isEditing ? undefined : "button"}
                                    tabIndex={isEditing ? undefined : 0}
                                    onClick={() => {
                                        if (!isEditing) {
                                            openModule(module.Id);
                                        }
                                    }}
                                    onKeyDown={(event) => {
                                        if (!isEditing) {
                                            handleModuleCardKeyDown(event, module.Id);
                                        }
                                    }}
                                    aria-label={isEditing ? undefined : `Open ${module.Name}`}
                                >
                                    <div className="module-list-card-main">
                                        <div className="module-list-card-title-row">
                                            <h3>{module.Name}</h3>
                                            <span className={`module-status-badge is-${status}`}>
                                                {active ? "● " : ""}
                                                {getModuleStatusLabel(module)}
                                            </span>
                                        </div>

                                        <div className="module-list-card-dates">
                                            {formatDate12h(module.Start)} -{" "}
                                            {formatDate12h(module.End)}
                                        </div>
                                    </div>

                                    <div className="module-list-card-actions">
                                        <button
                                            type="button"
                                            className="project-action project-action-secondary"
                                            onClick={(event) => {
                                                event.stopPropagation();
                                                beginModuleEdit(module);
                                            }}
                                            disabled={savingEditModule}
                                        >
                                            <FaEdit aria-hidden="true" />
                                            Edit Module
                                        </button>

                                        <button
                                            type="button"
                                            className="project-action project-action-primary"
                                            onClick={(event) => {
                                                event.stopPropagation();
                                                openModule(module.Id);
                                            }}
                                        >
                                            Open Module
                                        </button>
                                    </div>

                                    {isEditing ? (
                                        <div
                                            className="module-list-edit-panel"
                                            onClick={(event) => event.stopPropagation()}
                                        >
                                            <div className="calendar-create-grid">
                                                <div className="form-field input-field module-name-field">
                                                    <label>Module Name</label>
                                                    <input
                                                        type="text"
                                                        value={editModuleName}
                                                        onChange={(event) =>
                                                            setEditModuleName(event.currentTarget.value)
                                                        }
                                                    />
                                                </div>

                                                <DateTimeField
                                                    label="Start Date"
                                                    value={editModuleStart}
                                                    onChange={(value) => setEditModuleDate(value, true)}
                                                    highlightedDates={getDateRangeHighlightDates(
                                                        editModuleStartDate,
                                                        editModuleEndDate,
                                                    )}
                                                    blockedDates={getFullyBlockedDates(
                                                        editConflictRanges,
                                                    )}
                                                    timeClassName={(time) =>
                                                        dateOverlapsRange(time, editConflictRanges)
                                                            ? "react-datepicker__time--highlighted-red"
                                                            : null
                                                    }
                                                    selectsStart
                                                    startDate={editModuleStartDate}
                                                    endDate={editModuleEndDate}
                                                    hasError={editOverlapError}
                                                />

                                                <DateTimeField
                                                    label="End Date"
                                                    value={editModuleEnd}
                                                    onChange={(value) => setEditModuleDate(value, false)}
                                                    highlightedDates={getDateRangeHighlightDates(
                                                        editModuleStartDate,
                                                        editModuleEndDate,
                                                    )}
                                                    blockedDates={getFullyBlockedDates(
                                                        editConflictRanges,
                                                    )}
                                                    timeClassName={(time) =>
                                                        dateOverlapsRange(time, editConflictRanges)
                                                            ? "react-datepicker__time--highlighted-red"
                                                            : null
                                                    }
                                                    selectsEnd
                                                    startDate={editModuleStartDate}
                                                    endDate={editModuleEndDate}
                                                    hasError={editOverlapError}
                                                />
                                            </div>

                                            <div className="project-detail-edit-actions">
                                                <button
                                                    type="button"
                                                    className="project-action project-action-primary"
                                                    onClick={saveModuleEdit}
                                                    disabled={savingEditModule || !editChanged}
                                                >
                                                    <FaSave aria-hidden="true" />
                                                    {savingEditModule ? "Saving..." : "Save Module"}
                                                </button>

                                                <button
                                                    type="button"
                                                    className="project-action project-action-secondary"
                                                    onClick={cancelModuleEdit}
                                                    disabled={savingEditModule}
                                                >
                                                    <FaTimes aria-hidden="true" />
                                                    Cancel
                                                </button>
                                            </div>
                                        </div>
                                    ) : null}
                                </article>
                            );
                        })}
                    </div>
                </section>
            )}

            {viewMode === "calendar" && (
                <section className="calendar-shell" aria-label="Module calendar">
                    <div className="calendar-toolbar">
                        <button
                            type="button"
                            className="button calendar-nav-button"
                            onClick={goToPreviousMonth}
                        >
                            <FaChevronLeft aria-hidden="true" />
                            <span>Previous</span>
                        </button>

                        <div className="calendar-month-title">
                            {formatMonthTitle(calendarDate)}
                        </div>

                        <div className="calendar-toolbar-right">
                            <button
                                type="button"
                                className="button calendar-today-button"
                                onClick={goToToday}
                            >
                                Today
                            </button>
                            <button
                                type="button"
                                className="button calendar-nav-button"
                                onClick={goToNextMonth}
                            >
                                <span>Next</span>
                                <FaChevronRight aria-hidden="true" />
                            </button>
                        </div>
                    </div>

                    <div className="calendar-weekdays">
                        {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((day) => (
                            <div className="calendar-weekday" key={day}>
                                {day}
                            </div>
                        ))}
                    </div>

                    <div className="calendar-grid">
                        {calendarWeeks.map((week) => {
                            const maxEventRow = week.segments.reduce(
                                (max, segment) => Math.max(max, segment.row + 1),
                                0,
                            );

                            const weekStyle = {
                                "--event-rows": maxEventRow,
                            } as CSSProperties;

                            // Renders the interface using the current data and interaction state.
                            return (
                                <div className="calendar-week" key={week.key} style={weekStyle}>
                                    {week.days.map((day) => {
                                        const today = sameDay(day.date, new Date());

                                        // Renders the interface using the current data and interaction state.
                                        return (
                                            <div
                                                className={[
                                                    "calendar-day",
                                                    day.isCurrentMonth ? "" : "is-outside-month",
                                                    today ? "is-today" : "",
                                                ]
                                                    .join(" ")
                                                    .trim()}
                                                key={day.key}
                                            >
                                                <div className="calendar-day-number">
                                                    {day.date.getDate()}
                                                </div>

                                                <div className="calendar-mobile-projects">
                                                    {sortedModules
                                                        .filter((module) =>
                                                            moduleOccursOnDate(module, day.date),
                                                        )
                                                        .map((module) => {
                                                            const active = isModuleActiveNow(module);

                                                            // Renders the interface using the current data and interaction state.
                                                            return (
                                                                <button
                                                                    type="button"
                                                                    className={`calendar-project${active ? " is-active" : ""}`}
                                                                    key={`${day.key}-${module.Id}`}
                                                                    onClick={() => openModule(module.Id)}
                                                                    title={module.Name}
                                                                >
                                                                    <span className="calendar-project-name">
                                                                        {module.Name}
                                                                    </span>
                                                                    <span className="calendar-project-meta">
                                                                        {getModuleDateLabel(module)}
                                                                        {active ? " • Active" : ""}
                                                                    </span>
                                                                </button>
                                                            );
                                                        })}
                                                </div>
                                            </div>
                                        );
                                    })}

                                    {week.segments.length > 0 && (
                                        <div
                                            className="calendar-week-events"
                                            aria-label="Modules for this week"
                                        >
                                            {week.segments.map((segment) => {
                                                const active = isModuleActiveNow(segment.module);

                                                // Renders the interface using the current data and interaction state.
                                                return (
                                                    <button
                                                        type="button"
                                                        className={[
                                                            "calendar-project",
                                                            "calendar-project-span",
                                                            active ? "is-active" : "",
                                                            segment.startsBeforeWeek
                                                                ? "continues-from-left"
                                                                : "",
                                                            segment.endsAfterWeek ? "continues-to-right" : "",
                                                        ]
                                                            .join(" ")
                                                            .trim()}
                                                        key={`${week.key}-${segment.module.Id}-${segment.startColumn}-${segment.row}`}
                                                        onClick={() => openModule(segment.module.Id)}
                                                        title={segment.module.Name}
                                                        style={{
                                                            gridColumn: `${segment.startColumn + 1} / span ${segment.span}`,
                                                            gridRow: `${segment.row + 1}`,
                                                        }}
                                                    >
                                                        <span className="calendar-project-name">
                                                            {segment.module.Name}
                                                        </span>
                                                        <span className="calendar-project-meta">
                                                            {getModuleDateLabel(segment.module)}
                                                            {active ? " • Active" : ""}
                                                        </span>
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </section>
            )}

            {sortedModules.length === 0 && (
                <div className="empty-projects">No modules found for this class.</div>
            )}
        </div>
    );
}
