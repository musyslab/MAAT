// StudentModuleList.tsx: Displays the student module calendar and opens available module details.
import { CSSProperties, KeyboardEvent, useEffect, useMemo, useState } from "react"
import axios from "axios"
import { Helmet } from "react-helmet"
import { useLocation, useNavigate, useParams } from "react-router-dom"
import {
    FaCalendarAlt,
    FaChevronLeft,
    FaChevronRight,
    FaListUl
} from "react-icons/fa"

import MenuComponent from "../components/MenuComponent"
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs"
import "../../styling/ModuleList.scss"

// Describes the module object data expected by this file.
interface ModuleObject {
    Id: number
    ClassId: number
    Name: string
    Start: string
    End: string
    MainProjectId?: number
    PracticeProblemsEnabled?: boolean
    Hidden?: boolean
    IsHidden?: boolean
}

// Describes the class access response data expected by this file.
interface ClassAccessResponse {
    id?: number
    name?: string
    school_id?: number
    school_name?: string
}

// Describes the calendar day data expected by this file.
type CalendarDay = {
    date: Date
    isCurrentMonth: boolean
    key: string
}

// Describes the calendar week segment data expected by this file.
type CalendarWeekSegment = {
    module: ModuleObject
    startColumn: number
    span: number
    row: number
    startsBeforeWeek: boolean
    endsAfterWeek: boolean
}

// Describes the calendar week data expected by this file.
type CalendarWeek = {
    key: string
    days: CalendarDay[]
    segments: CalendarWeekSegment[]
}

// Builds the authorization header used by authenticated API requests.
const authHeader = () => ({
  ...(window.location.pathname.includes("/student-preview") ? { "X-MAAT-Test-User": "1" } : {}),
    Authorization: `Bearer ${localStorage.getItem("AUTOTA_AUTH_TOKEN")}`
})

// Displays the student module calendar and opens available module details.
export default function StudentModuleList() {
    // Reads the school, class, or assignment identifiers from the current route.
    const { school_id, class_id } = useParams<{ school_id: string; class_id: string }>()
    const navigate = useNavigate()
    const location = useLocation()

    const schoolId = school_id || ""
    const classId = class_id || ""
    const isAdminPreview = location.pathname.includes("/student-preview")
    const adminPreviewBasePath = `/admin/school/${schoolId}/class/${classId}/student-preview`

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [className, setClassName] = useState("")
    const [modules, setModules] = useState<ModuleObject[]>([])
    const [calendarDate, setCalendarDate] = useState<Date>(new Date())
    const [viewMode, setViewMode] = useState<"list" | "calendar">("list")
    const [isLoading, setIsLoading] = useState(true)
    const [errorMessage, setErrorMessage] = useState("")

    // Parses date for this view.
    const parseDate = (value: string): Date | null => {
        const d = new Date(value)
        return Number.isNaN(d.getTime()) ? null : d
    }

    // Helper for same day used by this component.
    const sameDay = (a: Date, b: Date): boolean => (
        a.getFullYear() === b.getFullYear()
        && a.getMonth() === b.getMonth()
        && a.getDate() === b.getDate()
    )

    // Starts of day for this view.
    const startOfDay = (d: Date): Date => {
        const next = new Date(d)
        next.setHours(0, 0, 0, 0)
        return next
    }

    // Ends of day for this view.
    const endOfDay = (d: Date): Date => {
        const next = new Date(d)
        next.setHours(23, 59, 59, 999)
        return next
    }

    // Formats month title for this view.
    const formatMonthTitle = (date: Date): string => (
        new Intl.DateTimeFormat("en-US", {
            month: "long",
            year: "numeric"
        }).format(date)
    )

    // Formats time for this view.
    const formatTime = (value: string): string => {
        const d = parseDate(value)
        if (!d) return value

        return new Intl.DateTimeFormat("en-US", {
            hour: "numeric",
            minute: "2-digit",
            hour12: true
        }).format(d)
    }

    // Formats short date for this view.
    const formatShortDate = (value: string): string => {
        const d = parseDate(value)
        if (!d) return value

        return new Intl.DateTimeFormat("en-US", {
            month: "short",
            day: "numeric"
        }).format(d)
    }

    // Formats date12h for this view.
    const formatDate12h = (value: string): string => {
        const d = parseDate(value)
        if (!d) return value

        return new Intl.DateTimeFormat("en-US", {
            year: "numeric",
            month: "short",
            day: "2-digit",
            hour: "numeric",
            minute: "2-digit",
            hour12: true
        }).format(d)
    }

    // Returns module status for this view.
    const getModuleStatus = (module: ModuleObject): "active" | "upcoming" | "ended" => {
        const startMs = Date.parse(module.Start)
        const endMs = Date.parse(module.End)

        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "upcoming"

        const now = Date.now()

        if (now >= startMs && now <= endMs) return "active"
        return now < startMs ? "upcoming" : "ended"
    }

    // Returns module status label for this view.
    const getModuleStatusLabel = (module: ModuleObject): string => {
        const status = getModuleStatus(module)

        if (status === "active") return "Active"
        if (status === "ended") return "Ended"
        return "Upcoming"
    }

    // Checks module active now for this view.
    const isModuleActiveNow = (module: ModuleObject): boolean => {
        const startMs = Date.parse(module.Start)
        const endMs = Date.parse(module.End)

        if (Number.isNaN(startMs) || Number.isNaN(endMs)) return false

        const now = Date.now()
        return now >= startMs && now <= endMs
    }

    // Helper for module occurs on date used by this component.
    const moduleOccursOnDate = (module: ModuleObject, date: Date): boolean => {
        const start = parseDate(module.Start)
        const end = parseDate(module.End)

        if (!start || !end) return false

        return start <= endOfDay(date) && end >= startOfDay(date)
    }

    // Helper for clamp used by this component.
    const clamp = (value: number, min: number, max: number): number => (
        Math.min(Math.max(value, min), max)
    )

    // Returns day index within week for this view.
    const getDayIndexWithinWeek = (date: Date, weekStart: Date): number => {
        const dayMs = 24 * 60 * 60 * 1000
        return Math.floor((startOfDay(date).getTime() - startOfDay(weekStart).getTime()) / dayMs)
    }

    // Returns module date label for this view.
    const getModuleDateLabel = (module: ModuleObject): string => {
        const start = parseDate(module.Start)
        const end = parseDate(module.End)

        if (!start || !end) {
            return `${module.Start} - ${module.End}`
        }

        if (sameDay(start, end)) {
            return `${formatTime(module.Start)} - ${formatTime(module.End)}`
        }

        return `${formatShortDate(module.Start)}, ${formatTime(module.Start)} - ${formatShortDate(module.End)}, ${formatTime(module.End)}`
    }

    // Loads class name for this view.
    const loadClassName = () => {
        if (!classId) {
            setClassName("")
            return
        }

        axios
            .get<ClassAccessResponse>(
                `${import.meta.env.VITE_API_URL}/classes/validate_class_access/${classId}`,
                {
                    headers: authHeader(),
                    params: {
                        ...(schoolId ? { school_id: schoolId } : {}),
                        role_context: isAdminPreview ? "admin" : "student"
                    }
                }
            )
            .then((res) => {
                setClassName(res.data?.name || "")
            })
            .catch((err) => {
                console.log(err)
                setClassName("")
            })
    }

    // Loads modules for this view.
    const loadModules = () => {
        if (!classId) {
            setModules([])
            setIsLoading(false)
            setErrorMessage("Please select a class first.")
            return
        }

        setIsLoading(true)
        setErrorMessage("")

        axios
            .get(`${import.meta.env.VITE_API_URL}/assignment_tracking/get_modules_by_class_id_student?id=${classId}`, {
                headers: authHeader()
            })
            .then((res) => {
                const parsed: ModuleObject[] = (res.data as any[]).map(
                    (item: any) => typeof item === "string" ? JSON.parse(item) as ModuleObject : item as ModuleObject
                )
                const visibleModules = parsed.filter((module) => !module.Hidden && !module.IsHidden)

                setModules(visibleModules)
                setIsLoading(false)

                const firstModuleDate = visibleModules
                    .map((m) => parseDate(m.Start))
                    .filter((d): d is Date => !!d)
                    .sort((a, b) => a.getTime() - b.getTime())[0]

                if (firstModuleDate) {
                    setCalendarDate(new Date(firstModuleDate.getFullYear(), firstModuleDate.getMonth(), 1))
                }
            })
            .catch((err) => {
                console.log(err)
                setModules([])
                setErrorMessage("Could not load modules for this class.")
                setIsLoading(false)
            })
    }

    // Loads or refreshes view data when the dependencies below change.
    useEffect(() => {
        loadClassName()
        loadModules()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [schoolId, classId, isAdminPreview])

    // Recomputes sorted modules only when its dependencies change.
    const sortedModules = useMemo(() => {
        return [...modules].sort((a, b) => {
            const da = Date.parse(a.Start)
            const db = Date.parse(b.Start)
            const aInvalid = Number.isNaN(da)
            const bInvalid = Number.isNaN(db)

            if (aInvalid && bInvalid) return 0
            if (aInvalid) return 1
            if (bInvalid) return -1

            return da - db
        })
    }, [modules])

    // Recomputes calendar days only when its dependencies change.
    const calendarDays = useMemo<CalendarDay[]>(() => {
        const year = calendarDate.getFullYear()
        const month = calendarDate.getMonth()

        const firstOfMonth = new Date(year, month, 1)
        const start = new Date(firstOfMonth)
        start.setDate(firstOfMonth.getDate() - firstOfMonth.getDay())

        const days: CalendarDay[] = []

        for (let i = 0; i < 42; i += 1) {
            const date = new Date(start)
            date.setDate(start.getDate() + i)

            days.push({
                date,
                isCurrentMonth: date.getMonth() === month,
                key: `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
            })
        }

        return days
    }, [calendarDate])

    // Recomputes calendar weeks only when its dependencies change.
    const calendarWeeks = useMemo<CalendarWeek[]>(() => {
        const weeks: CalendarWeek[] = []

        for (let i = 0; i < calendarDays.length; i += 7) {
            const days = calendarDays.slice(i, i + 7)
            const weekStart = startOfDay(days[0].date)
            const weekEnd = endOfDay(days[6].date)

            const rowEndByRow: number[] = []

            const segments: CalendarWeekSegment[] = sortedModules
                .filter((module) => {
                    const start = parseDate(module.Start)
                    const end = parseDate(module.End)

                    if (!start || !end) return false

                    return start <= weekEnd && end >= weekStart
                })
                .map((module) => {
                    const start = parseDate(module.Start) as Date
                    const end = parseDate(module.End) as Date

                    const startsBeforeWeek = start < weekStart
                    const endsAfterWeek = end > weekEnd

                    const startColumn = clamp(getDayIndexWithinWeek(start, weekStart), 0, 6)
                    const endColumn = clamp(getDayIndexWithinWeek(end, weekStart), 0, 6)
                    const span = Math.max(1, endColumn - startColumn + 1)

                    let row = rowEndByRow.findIndex((rowEnd) => startColumn > rowEnd)

                    if (row === -1) {
                        row = rowEndByRow.length
                        rowEndByRow.push(endColumn)
                    } else {
                        rowEndByRow[row] = endColumn
                    }

                    return {
                        module,
                        startColumn,
                        span,
                        row,
                        startsBeforeWeek,
                        endsAfterWeek
                    }
                })

            weeks.push({
                key: days[0].key,
                days,
                segments
            })
        }

        return weeks
    }, [calendarDays, sortedModules])

    // Helper for go to previous month used by this component.
    const goToPreviousMonth = () => {
        setCalendarDate((current) => new Date(current.getFullYear(), current.getMonth() - 1, 1))
    }

    // Helper for go to next month used by this component.
    const goToNextMonth = () => {
        setCalendarDate((current) => new Date(current.getFullYear(), current.getMonth() + 1, 1))
    }

    // Helper for go to today used by this component.
    const goToToday = () => {
        const today = new Date()
        setCalendarDate(new Date(today.getFullYear(), today.getMonth(), 1))
    }

    // Opens module for this view.
    const openModule = (module: ModuleObject) => {
        navigate(
            isAdminPreview
                ? `${adminPreviewBasePath}/module/${module.Id}`
                : `/student/school/${schoolId}/class/${classId}/module/${module.Id}`
        )
    }

    // Handles module card key down for this view.
    const handleModuleCardKeyDown = (event: KeyboardEvent<HTMLElement>, module: ModuleObject) => {
        if (event.key !== "Enter" && event.key !== " ") return

        event.preventDefault()
        openModule(module)
    }

    // Renders the interface using the current data and interaction state.
    return (
        <div className="projects-page">
            {/* Sets the page title and document metadata. */}
            <Helmet>
                <title>MAAT</title>
            </Helmet>

            {/* Displays the navigation and actions available on this page. */}
            <MenuComponent
            />

            {/* Shows the current location and links back to parent pages. */}
            <DirectoryBreadcrumbs
                items={isAdminPreview
                    ? [
                        { label: "School Selection", to: "/schools" },
                        {
                            label: "Class Selection",
                            to: schoolId ? `/admin/school/${schoolId}/classes` : "/schools"
                        },
                        {
                            label: "Admin Menu",
                            to: `/admin/school/${schoolId}/class/${classId}/menu`
                        },
                        { label: "Student Preview" }
                    ]
                    : [
                        { label: "School Selection", to: "/schools" },
                        {
                            label: "Class Selection",
                            to: schoolId ? `/student/school/${schoolId}/classes` : "/schools"
                        },
                        { label: "Module List" }
                    ]}
            />

            <div className="pageTitle">
                {className ? `${className} Student Module List` : "Student Module List"}
            </div>

            {isAdminPreview ? (
                <div className="pageMessage" role="status">
                    Admin preview: open an assignment and submit as the Test User.
                    Submissions and student-only actions are disabled.
                </div>
            ) : null}

            <div className="module-calendar-command-row">
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

            {errorMessage ? <div className="pageMessage">{errorMessage}</div> : null}

            {isLoading ? (
                <div className="empty-projects">Loading modules...</div>
            ) : null}

            {!isLoading && viewMode === "list" && sortedModules.length > 0 ? (
                <section className="module-list-shell" aria-label="Module list">
                    <div className="module-list-header-row">
                        <div>
                            <h2>Modules</h2>
                        </div>
                    </div>

                    <div className="module-list-grid">
                        {sortedModules.map((module) => {
                            const status = getModuleStatus(module)
                            const active = status === "active"

                            // Renders the interface using the current data and interaction state.
                            return (
                                <article
                                    className={`module-list-card is-${status}`}
                                    key={module.Id}
                                    role="button"
                                    tabIndex={0}
                                    onClick={() => openModule(module)}
                                    onKeyDown={(event) => handleModuleCardKeyDown(event, module)}
                                    aria-label={`Open ${module.Name}`}
                                >
                                    <div className="module-list-card-main">
                                        <div className="module-list-card-title-row">
                                            <h3>{module.Name}</h3>
                                            <span className={`module-status-badge is-${status}`}>
                                                {active ? "● " : ""}{getModuleStatusLabel(module)}
                                            </span>
                                        </div>

                                        <div className="module-list-card-dates">
                                            {formatDate12h(module.Start)} - {formatDate12h(module.End)}
                                        </div>
                                    </div>

                                    <div className="module-list-card-actions">
                                        <button
                                            type="button"
                                            className="project-action project-action-primary"
                                            onClick={(event) => {
                                                event.stopPropagation()
                                                openModule(module)
                                            }}
                                        >
                                            Open Module
                                        </button>
                                    </div>
                                </article>
                            )
                        })}
                    </div>
                </section>
            ) : null}

            {!isLoading && viewMode === "calendar" ? (
                <section className="calendar-shell" aria-label="Module calendar">
                    <div className="calendar-toolbar">
                        <button type="button" className="button calendar-nav-button" onClick={goToPreviousMonth}>
                            <FaChevronLeft aria-hidden="true" />
                            <span>Previous</span>
                        </button>

                        <div className="calendar-month-title">{formatMonthTitle(calendarDate)}</div>

                        <div className="calendar-toolbar-right">
                            <button type="button" className="button calendar-today-button" onClick={goToToday}>
                                Today
                            </button>

                            <button type="button" className="button calendar-nav-button" onClick={goToNextMonth}>
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
                                0
                            )

                            const weekStyle = {
                                "--event-rows": maxEventRow
                            } as CSSProperties

                            // Renders the interface using the current data and interaction state.
                            return (
                                <div className="calendar-week" key={week.key} style={weekStyle}>
                                    {week.days.map((day) => {
                                        const today = sameDay(day.date, new Date())

                                        // Renders the interface using the current data and interaction state.
                                        return (
                                            <div
                                                className={[
                                                    "calendar-day",
                                                    day.isCurrentMonth ? "" : "is-outside-month",
                                                    today ? "is-today" : ""
                                                ].join(" ").trim()}
                                                key={day.key}
                                            >
                                                <div className="calendar-day-number">{day.date.getDate()}</div>

                                                <div className="calendar-mobile-projects">
                                                    {sortedModules
                                                        .filter((module) => moduleOccursOnDate(module, day.date))
                                                        .map((module) => {
                                                            const active = isModuleActiveNow(module)

                                                            // Renders the interface using the current data and interaction state.
                                                            return (
                                                                <button
                                                                    type="button"
                                                                    className={`calendar-project${active ? " is-active" : ""}`}
                                                                    key={`${day.key}-${module.Id}`}
                                                                    onClick={() => openModule(module)}
                                                                    title={module.Name}
                                                                >
                                                                    <span className="calendar-project-name">{module.Name}</span>
                                                                    <span className="calendar-project-meta">
                                                                        {getModuleDateLabel(module)}
                                                                        {active ? " • Active" : ""}
                                                                    </span>
                                                                </button>
                                                            )
                                                        })}
                                                </div>
                                            </div>
                                        )
                                    })}

                                    {week.segments.length > 0 ? (
                                        <div className="calendar-week-events" aria-label="Modules for this week">
                                            {week.segments.map((segment) => {
                                                const active = isModuleActiveNow(segment.module)

                                                // Renders the interface using the current data and interaction state.
                                                return (
                                                    <button
                                                        type="button"
                                                        className={[
                                                            "calendar-project",
                                                            "calendar-project-span",
                                                            active ? "is-active" : "",
                                                            segment.startsBeforeWeek ? "continues-from-left" : "",
                                                            segment.endsAfterWeek ? "continues-to-right" : ""
                                                        ].join(" ").trim()}
                                                        key={`${week.key}-${segment.module.Id}-${segment.startColumn}-${segment.row}`}
                                                        onClick={() => openModule(segment.module)}
                                                        title={segment.module.Name}
                                                        style={{
                                                            gridColumn: `${segment.startColumn + 1} / span ${segment.span}`,
                                                            gridRow: `${segment.row + 1}`
                                                        }}
                                                    >
                                                        <span className="calendar-project-name">{segment.module.Name}</span>
                                                        <span className="calendar-project-meta">
                                                            {getModuleDateLabel(segment.module)}
                                                            {active ? " • Active" : ""}
                                                        </span>
                                                    </button>
                                                )
                                            })}
                                        </div>
                                    ) : null}
                                </div>
                            )
                        })}
                    </div>
                </section>
            ) : null}

            {!isLoading && sortedModules.length === 0 && !errorMessage ? (
                <div className="empty-projects">
                    No modules are currently available for this class.
                </div>
            ) : null}
        </div>
    )
}
