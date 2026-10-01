// AdminGrading.tsx: Displays a submission and manages grading deductions, code selections, and saved feedback.
// AdminGrading.tsx
import { useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { Helmet } from 'react-helmet'
import MenuComponent from '../components/MenuComponent'
import '../../styling/AdminGrading.scss'
import DirectoryBreadcrumbs from '../components/DirectoryBreadcrumbs'
import DiffView from '../components/CodeDiffView'
import LoadingAnimation from '../components/LoadingAnimation'

import { FiTrendingUp, FiChevronLeft, FiChevronRight, FiSave, FiUser, FiX, FiCheckCircle } from 'react-icons/fi'

const defaultpagenumber = -1

// Describes the error option data expected by this file.
type ErrorOption = {
    id: string
    label: string
    description: string
    points: number
}

// Describes the observed error data expected by this file.
type ObservedError = {
    startLine: number
    endLine: number
    errorId: string
    count: number
    note?: string
}

// Describes the line range data expected by this file.
type LineRange = {
    start: number
    end: number
}

// Describes the scoring mode data expected by this file.
type ScoringMode = 'perInstance' | 'flatPerError'

// Describes the student submission nav row data expected by this file.
type StudentSubmissionNavRow = {
    userId: number
    firstName: string
    lastName: string
    fullName: string
    submissionId: number
}

// Displays a submission and manages grading deductions, code selections, and saved feedback.
export function AdminGrading() {
    // Reads the school, class, or assignment identifiers from the current route.
    const { id, school_id, class_id, module_id, project_id, checkpoint_id: route_checkpoint_id } = useParams<{
        id: string
        school_id: string
        class_id: string
        module_id: string
        project_id: string
        checkpoint_id?: string
    }>()

    const submissionId = id !== undefined ? parseInt(id, 10) : defaultpagenumber
    const cid = class_id !== undefined ? parseInt(class_id, 10) : -1
    const pid = project_id !== undefined ? parseInt(project_id, 10) : -1
    const navigate = useNavigate()
    const location = useLocation()

    const schoolIdStr = school_id ?? ''
    const classIdStr = class_id ?? ''
    const moduleIdStr = module_id ?? ''
    const projectIdStr = project_id ?? ''

    const params = new URLSearchParams(location.search)
    const fromParam = (params.get('from') || '').toLowerCase()
    const fromAnalytics = fromParam === 'analytics'
    const parsedCheckpointId = Number(route_checkpoint_id)
    const checkpointId = Number.isInteger(parsedCheckpointId) && parsedCheckpointId > 0
        ? parsedCheckpointId
        : undefined
    const isCheckpoint = checkpointId !== undefined

    const classSelectionUrl = `/admin/school/${schoolIdStr}/classes`
    const adminMenuUrl = `/admin/school/${schoolIdStr}/class/${classIdStr}/menu`
    const analyticsDashboardUrl = `/admin/school/${schoolIdStr}/class/${classIdStr}/analytics`
    const moduleListUrl = `/admin/school/${schoolIdStr}/class/${classIdStr}/modules`
    const moduleDetailsUrl = `/admin/school/${schoolIdStr}/class/${classIdStr}/module/${moduleIdStr}/overview`
    const studentListUrl = isCheckpoint && checkpointId
        ? `/admin/school/${schoolIdStr}/class/${classIdStr}/module/${moduleIdStr}/project/${projectIdStr}/checkpoint/${checkpointId}/submissions`
        : `/admin/school/${schoolIdStr}/class/${classIdStr}/module/${moduleIdStr}/project/${projectIdStr}/submissions`

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [studentName, setStudentName] = useState<string>('')
    const [studentRoster, setStudentRoster] = useState<StudentSubmissionNavRow[]>([])
    const [studentHeaderLoading, setStudentHeaderLoading] = useState<boolean>(true)
    const [savedGradingLoading, setSavedGradingLoading] = useState<boolean>(true)
    const [showSavedBanner, setShowSavedBanner] = useState<boolean>(false)
    const [savedGrade, setSavedGrade] = useState<number | null>(null)
    const [activeTestcaseName, setActiveTestcaseName] = useState<string>('')
    const [activeTestcaseLongDiff, setActiveTestcaseLongDiff] = useState<string>('')

    const activeSubmissionRef = useRef(submissionId)
    activeSubmissionRef.current = submissionId
    useEffect(() => () => { aiAbortRef.current?.abort() }, [submissionId])

    // Track which lines contain errors and if errors exist
    const [observedErrors, setObservedErrors] = useState<ObservedError[]>([])
    const hasErrors = observedErrors.length > 0

    // Scoring mode:
    // - perInstance: points deducted per instance (count matters for grading)
    // - flatPerError: points deducted once if error exists on a selected range (count does not affect grading)
    const [scoringMode, setScoringMode] = useState<ScoringMode>('perInstance')

    const [baseErrorDefs, setBaseErrorDefs] = useState<ErrorOption[]>([])
    const [errorDefsLoading, setErrorDefsLoading] = useState<boolean>(true)
    const [errorDefsError, setErrorDefsError] = useState<string | null>(null)

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        setErrorDefsLoading(true)
        setErrorDefsError(null)

        axios
            .get(`${import.meta.env.VITE_API_URL}/ai_suggestions/grading_error_defs`, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })
            .then((response) => {
                const defsRaw = Array.isArray(response.data?.errorDefs) ? response.data.errorDefs : []
                const defs: ErrorOption[] = defsRaw
                    .map((item: any) => ({
                        id: String(item?.id ?? '').trim(),
                        label: String(item?.label ?? item?.id ?? '').trim(),
                        description: String(item?.description ?? '').trim(),
                        points: Number.isFinite(Number(item?.points)) ? Math.max(0, Math.floor(Number(item.points))) : 0,
                    }))
                    .filter((item: ErrorOption) => item.id && item.label)

                setBaseErrorDefs(defs)
                if (defs.length === 0) setErrorDefsError('No grading categories were returned by the backend.')
            })
            .catch((err) => {
                console.error('Could not load grading categories:', err)
                setBaseErrorDefs([])
                setErrorDefsError('Could not load grading categories.')
            })
            .finally(() => setErrorDefsLoading(false))
    }, [])

    // Custom (user-created) error defs for this grading session/page (persisted with grading config)
    const [customErrorDefs, setCustomErrorDefs] = useState<ErrorOption[]>([])
    const [newCustomLabel, setNewCustomLabel] = useState<string>('')
    const [newCustomPoints, setNewCustomPoints] = useState<number>(10)
    const [customAddError, setCustomAddError] = useState<string | null>(null)

    // Helper for make custom error id used by this component.
    const makeCustomErrorId = () => `CUST_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`.toUpperCase()

    // Adds custom error def for this view.
    const addCustomErrorDef = () => {
        const label = newCustomLabel.trim()
        if (!label) {
            setCustomAddError('Label is required.')
            return
        }
        const id = makeCustomErrorId()
        const pts = Number.isFinite(newCustomPoints) ? Math.max(0, Math.floor(newCustomPoints)) : 0

        setCustomErrorDefs((prev) => [...prev, { id, label, description: '', points: pts }])
        setErrorPoints((prev) => ({ ...prev, [id]: pts }))
        setNewCustomLabel('')
        setNewCustomPoints(10)
        setCustomAddError(null)
        setSaveStatus('idle')
    }

    // Recomputes all error defs only when its dependencies change.
    const ALL_ERROR_DEFS = useMemo<ErrorOption[]>(() => [...baseErrorDefs, ...customErrorDefs], [baseErrorDefs, customErrorDefs])

    // Store ONLY overrides (modified points). Defaults come from ALL_ERROR_DEFS.
    const [errorPoints, setErrorPoints] = useState<Record<string, number>>({})

    // Recomputes default points map only when its dependencies change.
    const DEFAULT_POINTS_MAP = useMemo<Record<string, number>>(() => {
        const m: Record<string, number> = {}
        for (const e of ALL_ERROR_DEFS) m[e.id] = Number.isFinite(e.points) ? Math.max(0, e.points) : 0
        return m
    }, [ALL_ERROR_DEFS])

    // Returns the point value currently applied to a grading error.
    const getEffectivePoints = (errorId: string) => {
        const o = errorPoints[errorId]
        if (Number.isFinite(o)) return Math.max(0, o)
        return Number.isFinite(DEFAULT_POINTS_MAP[errorId]) ? Math.max(0, DEFAULT_POINTS_MAP[errorId]) : 0
    }

    // Recomputes error defs only when its dependencies change.
    const ERROR_DEFS = useMemo<ErrorOption[]>(
        () =>
            ALL_ERROR_DEFS.map((e) => ({
                ...e,
                points: getEffectivePoints(e.id),
            })),
        [ALL_ERROR_DEFS, errorPoints, DEFAULT_POINTS_MAP],
    )

    // Recomputes error map only when its dependencies change.
    const ERROR_MAP = useMemo<Record<string, ErrorOption>>(() => {
        const map: Record<string, ErrorOption> = {}
        for (const err of ERROR_DEFS) map[err.id] = err
        return map
    }, [ERROR_DEFS])

    // Helper for bump error points used by this component.
    const bumpErrorPoints = (errorId: string, delta: number) => {
        if (!delta) return
        setErrorPoints((prev) => {
            const curEffective = Number.isFinite(prev[errorId]) ? prev[errorId] : getEffectivePoints(errorId)
            const next = Math.max(0, curEffective + delta)
            const def = Number.isFinite(DEFAULT_POINTS_MAP[errorId]) ? Math.max(0, DEFAULT_POINTS_MAP[errorId]) : 0

            // If back to default, remove override key.
            if (next === def) {
                const { [errorId]: _removed, ...rest } = prev
                return rest
            }
            return { ...prev, [errorId]: next }
        })
        setSaveStatus('idle')
    }

    // AI suggestions (dynamic, based on selected lines + failing diffs)
    const [aiSuggestionIds, setAiSuggestionIds] = useState<string[]>([])
    const [aiSuggestStatus, setAiSuggestStatus] = useState<'idle' | 'loading' | 'error'>('idle')
    const [aiSuggestError, setAiSuggestError] = useState<string | null>(null)
    // Keeps last ai key ref available across renders without triggering a state update.
    const lastAiKeyRef = useRef<string>('')
    // Keeps ai abort ref available across renders without triggering a state update.
    const aiAbortRef = useRef<AbortController | null>(null)
    // Keeps the values that drive this component’s display and user interactions in React state.
    const [aiEverRequested, setAiEverRequested] = useState<boolean>(false)
    const [aiHidden, setAiHidden] = useState<boolean>(false)

    // References for code lines
    const codeContainerRef = useRef<HTMLDivElement | null>(null)
    // Keeps line refs available across renders without triggering a state update.
    const lineRefs = useRef<Record<number, HTMLLIElement | null>>({})

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [suppressNativeSelection, setSuppressNativeSelection] = useState<boolean>(false)

    // Clears the browser text selection after a code-selection interaction.
    const clearBrowserSelection = () => {
        const sel = window.getSelection()
        if (sel && sel.rangeCount > 0) {
            sel.removeAllRanges()
        }
    }

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        const el = codeContainerRef.current
        if (!el) return

        el.classList.toggle('suppress-native-selection', suppressNativeSelection)

        return () => {
            el.classList.remove('suppress-native-selection')
        }
    }, [suppressNativeSelection])

    // Recomputes error count by key only when its dependencies change.
    const errorCountByKey = useMemo(() => {
        const m: Record<string, number> = {}
        for (const e of observedErrors) {
            m[`${e.startLine}-${e.endLine}-${e.errorId}`] = e.count
        }
        return m
    }, [observedErrors])

    // Returns error count for this view.
    const getErrorCount = (start: number, end: number, errorId: string) => {
        return errorCountByKey[`${start}-${end}-${errorId}`] ?? 0
    }

    // Adjust COUNT (instances) via separate + / - buttons
    const bumpErrorCount = (start: number, end: number, errorId: string, delta: number) => {
        if (delta === 0) return

        setObservedErrors((prev) => {
            const idx = prev.findIndex((e) => e.startLine === start && e.endLine === end && e.errorId === errorId)
            if (idx === -1) {
                if (delta < 0) return prev
                return [...prev, { startLine: start, endLine: end, errorId: errorId, count: delta, note: '' }]
            }

            const next = [...prev]
            const cur = next[idx]
            const nextCount = (cur.count ?? 1) + delta

            if (nextCount <= 0) {
                next.splice(idx, 1)
            } else {
                next[idx] = { ...cur, count: nextCount }
            }
            return next
        })

        setSaveStatus('idle')
    }

    // Updates error note for this view.
    const setErrorNote = (start: number, end: number, errorId: string, note: string) => {
        setObservedErrors((prev) =>
            prev.map((e) => (e.startLine === start && e.endLine === end && e.errorId === errorId ? { ...e, note } : e)),
        )
        setSaveStatus('idle')
    }

    // Helper for scroll to line used by this component.
    const scrollToLine = (lineNo: number) => {
        const el = lineRefs.current[lineNo]
        if (!el) return
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }

    // Reads the selected code text from the browser selection.
    const getSelectedCodeFromDom = (range: LineRange): string => {
        const lines: string[] = []
        for (let ln = range.start; ln <= range.end; ln++) {
            const el = lineRefs.current[ln]
            if (!el) continue
            const codeText = el.querySelector('.code-text')?.textContent ?? ''
            const cleaned = codeText.replace(/\u00A0/g, ' ').trimEnd()
            lines.push(`${ln}: ${cleaned}`)
        }
        return lines.join('\n').trim()
    }

    // Requests suggested grading feedback for the selected submission.
    const requestAiSuggestions = async (range: LineRange) => {
        const selectedCode = getSelectedCodeFromDom(range)
        if (!selectedCode) {
            setAiSuggestionIds([])
            setAiSuggestStatus('idle')
            setAiSuggestError(null)
            return
        }

        const key = `${submissionId}:${range.start}-${range.end}:${selectedCode.length}:${activeTestcaseName}:${activeTestcaseLongDiff.length}:${ERROR_DEFS.length}`
        if (lastAiKeyRef.current === key) return
        lastAiKeyRef.current = key

        if (aiAbortRef.current) aiAbortRef.current.abort()
        const ctrl = new AbortController()
        aiAbortRef.current = ctrl

        setAiEverRequested(true)
        setAiSuggestStatus('loading')
        setAiSuggestError(null)

        try {
            // Sends this operation and its payload to the server.
            const res = await axios.post(
                `${import.meta.env.VITE_API_URL}/ai_suggestions/grading_suggestions`,
                {
                    submissionId: submissionId,
                    startLine: range.start,
                    endLine: range.end,
                    selectedCode: selectedCode,
                    testcaseName: activeTestcaseName,
                    testcaseLongDiff: activeTestcaseLongDiff,
                },
                {
                    headers: {
                        Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                    },
                    signal: ctrl.signal,
                },
            )

            if (ctrl.signal.aborted || activeSubmissionRef.current !== submissionId) return
            const ids = Array.isArray(res.data?.suggestions)
                ? res.data.suggestions.map((id: any) => String(id)).filter((id: string) => ERROR_MAP[id])
                : []
            setAiSuggestionIds(ids)
            setAiSuggestStatus('idle')
        } catch (e: any) {
            if (ctrl.signal.aborted || activeSubmissionRef.current !== submissionId || e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED') return
            setAiSuggestStatus('error')
            setAiSuggestError('AI suggestion request failed.')
            setAiSuggestionIds([])
        }
    }

    // Calculates the point deduction from the selected grading errors.
    const computeDeduction = (errorId: string, count: number) => {
        const pts = ERROR_MAP[errorId]?.points ?? 0
        if (scoringMode === 'flatPerError') return pts
        return pts * Math.max(1, count)
    }

    // Helper for auto resize textarea used by this component.
    const autoResizeTextarea = (el: HTMLTextAreaElement | null) => {
        if (!el) return
        el.style.height = 'auto'
        el.style.height = `${el.scrollHeight}px`
    }

    // Recomputes total points only when its dependencies change.
    const totalPoints = useMemo(() => {
        if (scoringMode === 'flatPerError') {
            const uniq = new Set<string>()
            for (const e of observedErrors) uniq.add(e.errorId)
            let sum = 0
            for (const id of uniq) sum += ERROR_MAP[id]?.points ?? 0
            return sum
        }

        return observedErrors.reduce((sum, err) => sum + computeDeduction(err.errorId, err.count ?? 1), 0)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [observedErrors, ERROR_MAP, scoringMode])

    const grade = Math.max(0, 100 - totalPoints)
    // Recomputes graded student roster only when its dependencies change.
    const gradedStudentRoster = useMemo(() => studentRoster.filter((row) => row.submissionId > 0), [studentRoster])

    // Recomputes current student index only when its dependencies change.
    const currentStudentIndex = useMemo(
        () => gradedStudentRoster.findIndex((row) => row.submissionId === submissionId),
        [gradedStudentRoster, submissionId],
    )

    const previousStudentNav =
        currentStudentIndex > 0
            ? gradedStudentRoster[currentStudentIndex - 1]
            : null

    const currentStudentNav = currentStudentIndex >= 0 ? gradedStudentRoster[currentStudentIndex] : null
    const nextStudentNav =
        currentStudentIndex >= 0 && currentStudentIndex < gradedStudentRoster.length - 1
            ? gradedStudentRoster[currentStudentIndex + 1]
            : null

    // Helper for go to submission used by this component.
    const goToSubmission = (nextSubmissionId: number) => {
        const parts = location.pathname.split('/')
        let targetIndex = parts.length - 1

        for (let i = parts.length - 1; i >= 0; i -= 1) {
            if (parts[i] === String(submissionId)) {
                targetIndex = i
                break
            }
        }

        parts[targetIndex] = String(nextSubmissionId)
        navigate(`${parts.join('/')}${location.search}${location.hash}`)
    }

    // Line hover and selection
    const [hoveredLine, setHoveredLine] = useState<number | null>(null)
    const [initialLine, setInitialLine] = useState<number | null>(null)
    const [selectedRange, setSelectedRange] = useState<LineRange | null>(null)
    // Keeps selected range ref available across renders without triggering a state update.
    const selectedRangeRef = useRef<LineRange | null>(null)
    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        selectedRangeRef.current = selectedRange
    }, [selectedRange])

    // Helper for select lines used by this component.
    const selectLines = (start: number, end: number) => {
        setSelectedRange({ start: start, end: end })
    }

    // Checks range selected for this view.
    const isRangeSelected = (start: number, end: number) => {
        if (selectedRange === null) return false
        return start <= selectedRange.end && end >= selectedRange.start
    }

    // Handles mouse down for this view.
    const handleMouseDown = (line: number) => {
        setSuppressNativeSelection(false)
        setInitialLine(line)
        selectLines(line, line)
    }

    // Handles mouse enter for this view.
    const handleMouseEnter = (line: number) => {
        setHoveredLine(line)
        if (initialLine === null) return

        const nextRange = {
            start: Math.min(initialLine, line),
            end: Math.max(initialLine, line),
        }

        setSelectedRange(nextRange)

        const isMultiLine = nextRange.start !== nextRange.end
        setSuppressNativeSelection(isMultiLine)

        if (isMultiLine) {
            clearBrowserSelection()
        }
    }

    // Handles mouse up for this view.
    const handleMouseUp = () => {
        const range = selectedRangeRef.current
        const isMultiLine = range !== null && range.start !== range.end

        if (isMultiLine) {
            clearBrowserSelection()
        }

        setSuppressNativeSelection(false)
        setInitialLine(null)
    }

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (initialLine !== null) return
        if (selectedRange === null) {
            setAiSuggestionIds([])
            setAiSuggestStatus('idle')
            setAiSuggestError(null)
            lastAiKeyRef.current = ''
            return
        }
        requestAiSuggestions(selectedRange)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedRange, initialLine, activeTestcaseName, activeTestcaseLongDiff, ERROR_MAP])

    // Ctrl+F style find (commits on Enter)
    const [findInput, setFindInput] = useState<string>('')
    const [findQuery, setFindQuery] = useState<string>('') // last committed query
    const [findMatches, setFindMatches] = useState<number[]>([])
    const [findMatchIndex, setFindMatchIndex] = useState<number>(0)

    // Recomputes find match set only when its dependencies change.
    const findMatchSet = useMemo(() => new Set(findMatches), [findMatches])
    const activeFindLine = findMatches.length > 0 ? findMatches[findMatchIndex] : null

    // Searches the displayed code and updates the current match.
    const performFind = (rawQuery: string) => {
        const needle = rawQuery.trim()
        if (!needle) {
            setFindQuery('')
            setFindMatches([])
            setFindMatchIndex(0)
            return
        }

        const lowerNeedle = needle.toLowerCase()
        const lineNos = Object.keys(lineRefs.current)
            .map((k) => Number(k))
            .filter((n) => Number.isFinite(n))
            .sort((a, b) => a - b)

        const matches: number[] = []
        for (const ln of lineNos) {
            const el = lineRefs.current[ln]
            if (!el) continue
            const hay = (el.textContent ?? '').replace(/\u00A0/g, ' ').toLowerCase()
            if (hay.includes(lowerNeedle)) matches.push(ln)
        }

        setFindQuery(needle)
        setFindMatches(matches)
        setFindMatchIndex(0)
        if (matches.length > 0) scrollToLine(matches[0])
    }

    // Helper for step find used by this component.
    const stepFind = (dir: 1 | -1) => {
        if (findMatches.length === 0) return
        setFindMatchIndex((prev) => {
            const next = (prev + dir + findMatches.length) % findMatches.length
            scrollToLine(findMatches[next])
            return next
        })
    }

    // References for Navigation
    const diffViewRef = useRef<HTMLElement | null>(null)
    // Keeps all observed errors ref available across renders without triggering a state update.
    const allObservedErrorsRef = useRef<HTMLElement | null>(null)

    // Helper for scroll to section used by this component.
    const scrollToSection = (section: HTMLElement) => {
        if (!section) return
        section.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }

    // Fetch student name for header
    useEffect(() => {
        if (submissionId < 0 || pid < 0) {
            setStudentHeaderLoading(false)
            return
        }

        const controller = new AbortController()
        setStudentRoster([])
        setStudentName('')
        setStudentHeaderLoading(true)

        axios
            .post(
                `${import.meta.env.VITE_API_URL}/submissions/recentsubproject`,
                { project_id: pid, checkpoint: isCheckpoint, checkpoint_id: checkpointId ?? null },
                {
                    signal: controller.signal,
                    headers: {
                        Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                    },
                },
            )
            .then((res) => {
                if (controller.signal.aborted) return
                const data = res.data ?? {}
                const rows: StudentSubmissionNavRow[] = Object.entries(data)
                    .map(([userId, value]) => {
                        const studentData = Array.isArray(value) ? value : []
                        const lastName = String(studentData[0] ?? '').trim()
                        const firstName = String(studentData[1] ?? '').trim()
                        const rawSubmissionId = parseInt(String(studentData[7] ?? ''), 10)

                        return {
                            userId: parseInt(userId, 10),
                            firstName: firstName,
                            lastName: lastName,
                            fullName: `${firstName} ${lastName}`.trim(),
                            submissionId: Number.isFinite(rawSubmissionId) ? rawSubmissionId : -1,
                        }
                    })
                    .sort((a, b) => {
                        const byLast = a.lastName.localeCompare(b.lastName, undefined, { sensitivity: 'base' })
                        if (byLast !== 0) return byLast

                        const byFirst = a.firstName.localeCompare(b.firstName, undefined, { sensitivity: 'base' })
                        if (byFirst !== 0) return byFirst

                        return a.userId - b.userId
                    })

                setStudentRoster(rows)

                const currentRow = rows.find((row) => row.submissionId === submissionId)
                setStudentName(currentRow?.fullName ?? '')
            })
            .catch((err) => { if (!controller.signal.aborted) console.log(err) })
            .finally(() => { if (!controller.signal.aborted) setStudentHeaderLoading(false) })
        return () => controller.abort()
    }, [submissionId, pid, isCheckpoint, checkpointId])

    // Fetch saved grading errors
    useEffect(() => {
        if (submissionId < 0) {
            setSavedGradingLoading(false)
            return
        }

        const controller = new AbortController()
        savedSignatureRef.current = computeSignatureFromParts([], 'perInstance', {}, [])
        setObservedErrors([])
        setErrorPoints({})
        setCustomErrorDefs([])
        setScoringMode('perInstance')
        setSavedGrade(null)
        setSelectedRange(null)
        setActiveTestcaseName('')
        setActiveTestcaseLongDiff('')
        setIsDirty(false)
        setSaveStatus('idle')
        setShowSavedBanner(false)
        setSavedGradingLoading(true)

        axios
            .get(`${import.meta.env.VITE_API_URL}/submissions/get_grading/${submissionId}`, {
                signal: controller.signal,
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })
            .then((response) => {
                if (controller.signal.aborted) return
                const { errors, scoringMode: savedMode, errorPoints: savedPoints, errorDefs: savedDefs } = response.data
                const savedDbGrade = response.data?.grade

                setSavedGrade(Number.isFinite(Number(savedDbGrade)) ? Number(savedDbGrade) : null)

                if (savedMode === 'perInstance' || savedMode === 'flatPerError') {
                    setScoringMode(savedMode)
                }

                if (savedPoints && typeof savedPoints === 'object') {
                    setErrorPoints(savedPoints as Record<string, number>)
                } else {
                    setErrorPoints({})
                }

                if (savedDefs && typeof savedDefs === 'object') {
                    const defsArr: ErrorOption[] = Object.entries(savedDefs).map(([id, meta]) => ({
                        id: String(id),
                        label: String((meta as any)?.label ?? id),
                        description: String((meta as any)?.description ?? ''),
                        points: Number.isFinite((meta as any)?.points) ? Math.max(0, Number((meta as any).points)) : 0,
                    }))
                    setCustomErrorDefs(defsArr)
                } else {
                    setCustomErrorDefs([])
                }

                const nextErrors: ObservedError[] = (Array.isArray(errors) ? errors : []).map((item: any) => ({
                    startLine: Number(item.startLine),
                    endLine: Number(item.endLine),
                    errorId: String(item.errorId),
                    count: Math.max(1, Number(item.count ?? 1)),
                    note: typeof item.note === 'string' ? item.note : '',
                }))
                setObservedErrors(nextErrors)

                const modeForSig: ScoringMode = savedMode === 'flatPerError' ? 'flatPerError' : 'perInstance'
                const pointsForSig = savedPoints && typeof savedPoints === 'object' ? (savedPoints as Record<string, number>) : {}
                const defsForSig: ErrorOption[] =
                    savedDefs && typeof savedDefs === 'object'
                        ? Object.entries(savedDefs).map(([id, meta]) => ({
                            id: String(id),
                            label: String((meta as any)?.label ?? id),
                            description: String((meta as any)?.description ?? ''),
                            points: Number.isFinite((meta as any)?.points) ? Math.max(0, Number((meta as any).points)) : 0,
                        }))
                        : []

                savedSignatureRef.current = computeSignatureFromParts(nextErrors, modeForSig, pointsForSig, defsForSig)
                setIsDirty(false)
                setSaveStatus('idle')

            })
            .catch((err) => { if (!controller.signal.aborted) console.error('Could not load saved grading:', err) })
            .finally(() => { if (!controller.signal.aborted) setSavedGradingLoading(false) })
        return () => controller.abort()
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [submissionId])

    // Handles saving grading errors
    const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
    const isInitialPageLoading = studentHeaderLoading || savedGradingLoading || errorDefsLoading
    const showLoadingOverlay = isInitialPageLoading || saveStatus === 'saving'

    // Unsaved changes tracking (dirty state)
    const [isDirty, setIsDirty] = useState<boolean>(false)
    // Keeps saved signature ref available across renders without triggering a state update.
    const savedSignatureRef = useRef<string>('') // last loaded/saved snapshot

    // Computes signature from parts for this view.
    const computeSignatureFromParts = (
        errs: ObservedError[],
        mode: ScoringMode,
        points: Record<string, number>,
        defs: ErrorOption[],
    ) => {
        const sortedErrs = [...errs].sort((a, b) => {
            if (a.startLine !== b.startLine) return a.startLine - b.startLine
            if (a.endLine !== b.endLine) return a.endLine - b.endLine
            return a.errorId.localeCompare(b.errorId)
        })

        const sortedDefs = [...defs]
            .map((d) => ({
                id: String(d.id),
                label: String(d.label ?? ''),
                description: String(d.description ?? ''),
                points: Math.max(0, Math.floor(Number(d.points ?? 0))),
            }))
            .sort((a, b) => a.id.localeCompare(b.id))

        const sortedPointsEntries = Object.entries(points ?? {})
            .map(([k, v]) => [String(k), Math.max(0, Math.floor(Number(v ?? 0)))] as const)
            .sort(([a], [b]) => a.localeCompare(b))

        return JSON.stringify({
            scoringMode: mode,
            errorPoints: sortedPointsEntries,
            errorDefs: sortedDefs,
            errors: sortedErrs.map((e) => ({
                startLine: Number(e.startLine),
                endLine: Number(e.endLine),
                errorId: String(e.errorId),
                count: Math.max(1, Number(e.count ?? 1)),
                note: String(e.note ?? ''),
            })),
        })
    }

    // Recomputes current signature only when its dependencies change.
    const currentSignature = useMemo(
        () => computeSignatureFromParts(observedErrors, scoringMode, errorPoints, customErrorDefs),
        [observedErrors, scoringMode, errorPoints, customErrorDefs],
    )

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        const dirty = savedSignatureRef.current !== '' && currentSignature !== savedSignatureRef.current
        setIsDirty(dirty)
    }, [currentSignature])

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (!isDirty) return
        setShowSavedBanner(false)
        setSaveStatus((prev) => (prev === 'saved' ? 'idle' : prev))
    }, [isDirty])

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (!showSavedBanner) return

        const timer = window.setTimeout(() => {
            setShowSavedBanner(false)
            setSaveStatus('idle')
        }, 3000)

        return () => window.clearTimeout(timer)
    }, [showSavedBanner])

    // Helper for dismiss saved banner used by this component.
    const dismissSavedBanner = () => {
        setShowSavedBanner(false)
        setSaveStatus((prev) => (prev === 'saved' ? 'idle' : prev))
    }

    // Warn before leaving the page if there are unsaved changes (tab close/refresh/navigate away)
    useEffect(() => {
        // Helper for on before unload used by this component.
        const onBeforeUnload = (e: BeforeUnloadEvent) => {
            if (!isDirty) return
            e.preventDefault()
            e.returnValue = ''
        }
        window.addEventListener('beforeunload', onBeforeUnload)
        return () => window.removeEventListener('beforeunload', onBeforeUnload)
    }, [isDirty])

    // Converts the selected grading errors into the payload sent when saving.
    const serializeErrorsForSave = (errs: ObservedError[]) => {
        // New backend supports counts directly.
        return errs.map((e) => ({
            startLine: e.startLine,
            endLine: e.endLine,
            errorId: e.errorId,
            count: Math.max(1, Number(e.count ?? 1)),
            note: (e.note ?? '').toString(),
        }))
    }

    // Handles save for this view.
    const handleSave = () => {
        setSaveStatus('saving')
        setShowSavedBanner(false)

        const customDefsForSave: Record<string, { label: string; description: string; points: number }> = {}
        for (const e of customErrorDefs) {
            customDefsForSave[e.id] = { label: e.label, description: '', points: Math.max(0, Math.floor(Number(e.points ?? 0))) }
        }

        axios
            .post(
                `${import.meta.env.VITE_API_URL}/submissions/save_grading`,
                {
                    submissionId: submissionId,
                    grade: grade,
                    scoringMode: scoringMode,
                    errorPoints: errorPoints,
                    errorDefs: customDefsForSave,
                    errors: serializeErrorsForSave(observedErrors),
                    projectId: pid,
                    checkpoint: isCheckpoint,
                    checkpoint_id: checkpointId ?? null,
                },
                {
                    headers: {
                        Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                    },
                },
            )
            .then(() => {
                if (activeSubmissionRef.current !== submissionId) return
                setSaveStatus('saved')
                setSavedGrade(grade)
                savedSignatureRef.current = currentSignature
                setIsDirty(false)
                setShowSavedBanner(true)
            })
            .catch((error) => {
                if (activeSubmissionRef.current !== submissionId) return
                console.error('Failed to save:', error)
                setSaveStatus('error')
            })
    }

    // Groups and sorts errors for the All Errors table
    const tableRows = useMemo(() => {
        return Object.values(
            observedErrors.reduce((table, err) => {
                const key = `${err.startLine}-${err.endLine}`
                if (!table[key]) table[key] = [err.startLine, err.endLine, []]

                table[key][2].push(err)
                return table
            }, {} as Record<string, [number, number, ObservedError[]]>),
        ).sort((a, b) => {
            const [aStart, aEnd] = a
            const [bStart, bEnd] = b
            if (aStart !== bStart) return aStart - bStart
            return aEnd - bEnd
        })
    }, [observedErrors])

    const selectedRangeErrors = selectedRange !== null ? observedErrors.filter((err) => isRangeSelected(err.startLine, err.endLine)) : []

    // Recomputes selected range counts by error id only when its dependencies change.
    const selectedRangeCountsByErrorId = useMemo(() => {
        const m: Record<string, number> = {}
        if (!selectedRange) return m
        for (const e of observedErrors) {
            if (e.startLine === selectedRange.start && e.endLine === selectedRange.end) {
                m[e.errorId] = e.count
            }
        }
        return m
    }, [observedErrors, selectedRange])

    // Renders the interface using the current data and interaction state.
    return (
        <div className="page-container" id="admin-output-diff">
            {/* Shows progress while this view is waiting for data. */}
            <LoadingAnimation show={showLoadingOverlay} message={isInitialPageLoading ? 'Loading grading page...' : 'Saving grade...'} />
            {/* Sets the page title and document metadata. */}
            <Helmet>
                <title>MAAT</title>
            </Helmet>
            {/* Displays the navigation and actions available on this page. */}
            <MenuComponent />

            {/* Shows the current location and links back to parent pages. */}
            <DirectoryBreadcrumbs
                items={[
                    { label: 'School Selection', to: '/schools' },
                    { label: 'Class Selection', to: classSelectionUrl },
                    { label: 'Admin Menu', to: adminMenuUrl },
                    ...(fromAnalytics
                        ? [
                            { label: 'Analytics Dashboard', to: analyticsDashboardUrl },
                        ]
                        : [
                            { label: 'Module List', to: moduleListUrl },
                            { label: 'Module Details', to: moduleDetailsUrl },
                            { label: 'Student List', to: studentListUrl },
                        ]),
                    { label: 'Grade Submission' },
                ]}
                confirmOnNavigate={isDirty}
                confirmMessage="You have unsaved changes. Leave this page?"
            />

            <div className="pageTitle">Grade Submission: {studentName || 'Unknown Student'}</div>

            <div className="grading-student-nav">
                <div className="grading-student-nav__summary">
                    <div className="grading-student-chip">
                        <span className="grading-student-chip__icon" aria-hidden="true">
                            <FiUser />
                        </span>
                        <div className="grading-student-chip__content">
                            <span className="grading-student-chip__label">Student</span>
                            <span className="grading-student-chip__value">{currentStudentNav?.fullName || studentName || 'Unknown Student'}</span>
                        </div>
                    </div>

                    <div className="grading-student-chip">
                        <span className="grading-student-chip__icon" aria-hidden="true">
                            <FiSave />
                        </span>
                        <div className="grading-student-chip__content">
                            <span className="grading-student-chip__label">Current Saved Grade</span>
                            <span className="grading-student-chip__value">{savedGrade ?? 'Not saved yet'}</span>
                        </div>
                    </div>

                    <div className="grading-student-chip grading-student-chip--progress">
                        <span className="grading-student-chip__icon" aria-hidden="true">
                            <FiTrendingUp />
                        </span>
                        <div className="grading-student-chip__content">
                            <span className="grading-student-chip__label">Progress</span>
                            <span className="grading-student-chip__value">
                                {currentStudentIndex >= 0
                                    ? `Student ${currentStudentIndex + 1} of ${gradedStudentRoster.length}`
                                    : 'Student not found in roster'}
                            </span>
                        </div>
                    </div>
                </div>

                <div className="grading-student-nav__actions">
                    <button
                        type="button"
                        className="grading-student-nav__button"
                        disabled={!previousStudentNav}
                        onClick={() => (previousStudentNav ? goToSubmission(previousStudentNav.submissionId) : null)}
                    >
                        <span className="grading-student-nav__button-icon" aria-hidden="true">
                            <FiChevronLeft />
                        </span>
                        <span className="grading-student-nav__button-text">
                            <span className="grading-student-nav__button-label">Previous</span>
                            <span className="grading-student-nav__button-name">
                                {previousStudentNav ? previousStudentNav.fullName : 'No previous submission'}
                            </span>
                        </span>
                    </button>

                    <button
                        type="button"
                        className="grading-student-nav__button"
                        disabled={!nextStudentNav}
                        onClick={() => (nextStudentNav ? goToSubmission(nextStudentNav.submissionId) : null)}
                    >
                        <span className="grading-student-nav__button-text">
                            <span className="grading-student-nav__button-label">Next</span>
                            <span className="grading-student-nav__button-name">
                                {nextStudentNav ? nextStudentNav.fullName : 'No next submission'}
                            </span>
                        </span>
                        <span className="grading-student-nav__button-icon" aria-hidden="true">
                            <FiChevronRight />
                        </span>
                    </button>
                </div>
            </div>

            <div
                className={`grading-saved-alert ${showSavedBanner ? 'is-visible' : ''}`}
                role="alert"
                aria-live="polite"
                aria-hidden={!showSavedBanner}
            >
                <div className="grading-saved-alert__icon" aria-hidden="true">
                    <FiCheckCircle />
                </div>

                <div className="grading-saved-alert__content">
                    <div className="grading-saved-alert__eyebrow">Grade submitted</div>
                    <div className="grading-saved-alert__title">Saved successfully</div>
                    <div className="grading-saved-alert__text">
                        The grade for {studentName || 'this student'} was saved.
                    </div>
                </div>

                <button
                    type="button"
                    className="grading-saved-alert__close"
                    onClick={dismissSavedBanner}
                    aria-label="Dismiss saved grade alert"
                    title="Dismiss"
                >
                    <FiX />
                </button>
            </div>

            <DiffView
                submissionId={submissionId}
                classId={cid}
                revealHiddenOutput
                diffViewRef={diffViewRef}
                codeSectionTitle="Submitted Code (click lines to mark errors)"
                onActiveTestcaseChange={(tc) => {
                    if (!tc || tc.passed) {
                        setActiveTestcaseName('')
                        setActiveTestcaseLongDiff('')
                        return
                    }
                    setActiveTestcaseName(tc.name ?? '')
                    setActiveTestcaseLongDiff(tc.longDiff ?? '')
                }}
                betweenDiffAndCode={
                    <div className="grading-banner" role="note" aria-label="How to add errors">
                        <div className="banner-title">How to mark errors</div>
                        <div className="banner-text">
                            Click a line or select multiple lines in the submitted code. Use the Grading Panel on the right to add or remove error
                            categories for the selected line(s).
                        </div>
                    </div>
                }
                codeContainerRef={codeContainerRef}
                lineRefs={lineRefs}
                getLineClassName={(lineNo) => {
                    const errors = observedErrors.some((err) => err.startLine <= lineNo && err.endLine >= lineNo)
                    const isFindMatch = findMatchSet.has(lineNo)
                    const isFindActive = activeFindLine === lineNo
                    return [errors ? 'has-error' : '', hoveredLine === lineNo ? 'is-hovered' : '', isRangeSelected(lineNo, lineNo) ? 'is-selected' : '', isFindMatch ? 'is-find-match' : '', isFindActive ? 'is-find-active' : '']
                        .filter(Boolean)
                        .join(' ')
                }}
                onLineMouseDown={(lineNo) => handleMouseDown(lineNo)}
                onLineMouseEnter={(lineNo) => handleMouseEnter(lineNo)}
                onLineMouseLeave={() => setHoveredLine(null)}
                onLineMouseUp={() => handleMouseUp()}
                belowCode={
                    <section className="all-observed-section" aria-label="Grading Summary and Save" ref={allObservedErrorsRef}>
                        <h2 className="section-title">Grading Summary and Save</h2>
                        <div className="all-observed-panel">
                            <div className="save-panel">
                                <div className="grade-column">
                                    <div className="grade-stack">
                                        <div className="grade-value">{grade}</div>
                                        <div className="grade-mode">{scoringMode === 'perInstance' ? 'Per-instance scoring' : 'Flat per-error scoring'}</div>
                                    </div>
                                </div>

                                <button className={`save-grade ${saveStatus}`} onClick={handleSave} disabled={saveStatus === 'saving'}>
                                    {saveStatus === 'idle' && 'Save'}
                                    {saveStatus === 'saving' && 'Saving...'}
                                    {saveStatus === 'saved' && 'Saved!'}
                                    {saveStatus === 'error' && 'Error'}
                                </button>

                                <div className="scoring-toggle" role="group" aria-label="Scoring mode toggle">
                                    <button type="button" className={`toggle-btn ${scoringMode === 'perInstance' ? 'active' : ''}`} onClick={() => setScoringMode('perInstance')}>
                                        Per instance
                                    </button>
                                    <button type="button" className={`toggle-btn ${scoringMode === 'flatPerError' ? 'active' : ''}`} onClick={() => setScoringMode('flatPerError')}>
                                        Flat per error
                                    </button>
                                </div>

                                {saveStatus === 'error' && <div className="muted small save-status">Save failed. Try again.</div>}
                            </div>

                            {!hasErrors && <div className="muted">No errors added yet.</div>}

                            {hasErrors && (
                                <div className="all-errors">
                                    {tableRows.map(([start, end, errors]) => {
                                        const totalPointsForRange =
                                            scoringMode === 'flatPerError'
                                                ? (() => {
                                                    const uniq = new Set<string>()
                                                    for (const e of errors) uniq.add(e.errorId)
                                                    let sum = 0
                                                    for (const id of uniq) sum += ERROR_MAP[id]?.points ?? 0
                                                    return sum
                                                })()
                                                : errors.reduce((sum, e) => sum + computeDeduction(e.errorId, e.count ?? 1), 0)
                                        const totalCountForRange = errors.reduce((sum, e) => sum + (e.count ?? 1), 0)

                                        // Renders the interface using the current data and interaction state.
                                        return (
                                            <div
                                                key={`${start}-${end}`}
                                                className={`
                                                    all-errors-line
                                                    ${selectedRange?.start === start && selectedRange.end === end ? 'is-selected' : ''}
                                                `}
                                            >
                                                <button
                                                    type="button"
                                                    className="all-errors-line-header"
                                                    onClick={() => {
                                                        setSelectedRange({ start: start, end: end })
                                                        scrollToLine(start)
                                                    }}
                                                    title="Select line(s)"
                                                >
                                                    <span className="all-errors-line-title">{start === end ? `Line ${start}` : `Lines ${start}-${end}`}</span>
                                                    <span className="all-errors-line-meta">
                                                        {totalCountForRange} {totalCountForRange === 1 ? 'instance' : 'instances'}, -{totalPointsForRange}
                                                        {isDirty && (
                                                            <span className="unsaved-badge" title="You have unsaved changes">
                                                                Unsaved
                                                            </span>
                                                        )}
                                                    </span>
                                                </button>

                                                <div className="all-errors-line-body">
                                                    <div className="all-errors-table-header" role="row">
                                                        <div className="col label" role="columnheader">
                                                            Error
                                                        </div>
                                                        <div className="col instances" role="columnheader">
                                                            Instances
                                                        </div>
                                                        <div className="col note" role="columnheader">
                                                            Comment
                                                        </div>
                                                        <div className="col points" role="columnheader">
                                                            Points
                                                        </div>
                                                    </div>
                                                    {errors.map((err, idx) => {
                                                        const meta = ERROR_MAP[err.errorId]
                                                        const label = meta?.label ?? err.errorId
                                                        const desc = meta?.description ?? ''
                                                        const count = Math.max(1, err.count ?? 1)
                                                        const shownDeduction = computeDeduction(err.errorId, count)

                                                        // Renders the interface using the current data and interaction state.
                                                        return (
                                                            <div key={`${start}-${end}-${err.errorId}-${idx}`} className="all-errors-item" title={desc}>
                                                                <div className="col label">
                                                                    <span className="all-errors-item-label">{label}</span>
                                                                </div>

                                                                <div className="col instances">
                                                                    <div className="instance-box" aria-label="Instances">
                                                                        <span className={`count-badge ${count > 0 ? 'active' : ''}`}>x{count}</span>
                                                                        <div className="count-controls" aria-label="Adjust count">
                                                                            <button
                                                                                type="button"
                                                                                className="count-btn plus"
                                                                                onClick={() => bumpErrorCount(start, end, err.errorId, 1)}
                                                                                aria-label="Increase count"
                                                                                title="Increase count"
                                                                            >
                                                                                +
                                                                            </button>
                                                                            {getErrorCount(start, end, err.errorId) > 0 && (
                                                                                <button
                                                                                    type="button"
                                                                                    className="count-btn minus"
                                                                                    onClick={() => bumpErrorCount(start, end, err.errorId, -1)}
                                                                                    aria-label="Decrease count"
                                                                                    title="Decrease count"
                                                                                >
                                                                                    −
                                                                                </button>
                                                                            )}
                                                                        </div>
                                                                    </div>
                                                                </div>

                                                                <textarea
                                                                    className="col note error-note"
                                                                    rows={1}
                                                                    value={err.note ?? ''}
                                                                    onChange={(e) => {
                                                                        setErrorNote(start, end, err.errorId, e.target.value)
                                                                        autoResizeTextarea(e.currentTarget)
                                                                    }}
                                                                    onInput={(e) => autoResizeTextarea(e.currentTarget)}
                                                                    placeholder="Add optional comment"
                                                                />

                                                                <div className="col points">
                                                                    <div className="points-box" aria-label="Point deduction">
                                                                        <span className="deduction-value">-{shownDeduction}</span>
                                                                        <div className="points-controls" aria-label="Adjust points">
                                                                            <button
                                                                                type="button"
                                                                                className="points-btn plus"
                                                                                onClick={() => bumpErrorPoints(err.errorId, 1)}
                                                                                aria-label="Increase points for this error type"
                                                                                title="Increase points"
                                                                            >
                                                                                +
                                                                            </button>
                                                                            <button
                                                                                type="button"
                                                                                className="points-btn minus"
                                                                                onClick={() => bumpErrorPoints(err.errorId, -1)}
                                                                                disabled={(ERROR_MAP[err.errorId]?.points ?? 0) <= 0}
                                                                                aria-label="Decrease points for this error type"
                                                                                title="Decrease points"
                                                                            >
                                                                                −
                                                                            </button>
                                                                        </div>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        )
                                                    })}
                                                </div>
                                            </div>
                                        )
                                    })}
                                </div>
                            )}
                        </div>
                    </section>
                }
                rightPanel={
                    <aside className="grading-panel" aria-label="Grading panel">
                        <div className="grading-panel-header">
                            <div className="grading-title">Grading Panel</div>
                            <div className="grading-hint">{!selectedRange ? 'Select a line to start.' : 'Add errors to the selected line(s).'}</div>
                        </div>

                        <div className="find-bar" role="search" aria-label="Find in code">
                            <input
                                className="find-input"
                                type="text"
                                placeholder="Find in code (Enter to search)"
                                value={findInput}
                                onChange={(e) => setFindInput(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key !== 'Enter') return
                                    e.preventDefault()

                                    const nextQuery = findInput.trim()
                                    if (nextQuery !== findQuery) {
                                        performFind(findInput)
                                        return
                                    }
                                    stepFind(e.shiftKey ? -1 : 1)
                                }}
                            />

                            <div className="find-count" aria-label="Match count">
                                {findMatches.length === 0 ? '0/0' : `${findMatchIndex + 1}/${findMatches.length}`}
                            </div>

                            <div className="find-nav" aria-label="Find navigation">
                                <button type="button" className="find-nav-btn" disabled={findMatches.length === 0} onClick={() => stepFind(-1)} title="Previous match (Shift+Enter)">
                                    ‹
                                </button>
                                <button type="button" className="find-nav-btn" disabled={findMatches.length === 0} onClick={() => stepFind(1)} title="Next match (Enter)">
                                    ›
                                </button>
                            </div>
                        </div>

                        <div className="navigation-section">
                            <div className="navigation-header">Jump To</div>
                            <ul className="navigation-list">
                                <li className="navigation-item" onClick={() => (diffViewRef.current !== null ? scrollToSection(diffViewRef.current) : null)}>
                                    Test Cases
                                </li>
                                <li className="navigation-item" onClick={() => (allObservedErrorsRef.current !== null ? scrollToSection(allObservedErrorsRef.current) : null)}>
                                    Grading Summary and Save
                                </li>
                            </ul>
                        </div>

                        <div className="grading-section">
                            <div className="section-label">Selected line(s)</div>
                            <div className="selected-line-row">
                                <button
                                    type="button"
                                    className={`selected-line-pill ${selectedRange ? 'active' : 'inactive'}`}
                                    disabled={!selectedRange}
                                    onClick={() => (selectedRange !== null ? scrollToLine(selectedRange.start) : null)}
                                    title="Click to jump to selected line"
                                >
                                    {selectedRange === null ? 'None' : selectedRange.start === selectedRange.end ? `Line ${selectedRange.start}` : `Lines ${selectedRange.start}-${selectedRange.end}`}
                                </button>
                            </div>
                        </div>

                        <div className="grading-section">
                            <div className="ai-suggestions-picker">
                                <div className="ai-suggestions-picker-header">
                                    <span className="ai-suggestions-picker-title">AI suggestions</span>
                                    <div className="ai-suggestions-actions">
                                        {aiEverRequested && (
                                            <>
                                                <button
                                                    type="button"
                                                    className="ai-suggest-btn"
                                                    disabled={selectedRange === null || aiSuggestStatus === 'loading'}
                                                    onClick={() => {
                                                        if (!selectedRangeRef.current) return
                                                        lastAiKeyRef.current = ''
                                                        requestAiSuggestions(selectedRangeRef.current)
                                                    }}
                                                    title="Retry AI suggestions"
                                                >
                                                    Retry
                                                </button>

                                                <button
                                                    type="button"
                                                    className="ai-suggest-btn"
                                                    onClick={() => setAiHidden((v) => !v)}
                                                    title={aiHidden ? 'Show AI suggestions' : 'Hide AI suggestions'}
                                                >
                                                    {aiHidden ? 'Show' : 'Hide'}
                                                </button>
                                            </>
                                        )}
                                    </div>
                                </div>

                                <div className="ai-suggestions-picker-body">
                                    {!aiHidden && (
                                        <div className="suggestions-grid">
                                            {aiSuggestStatus === 'loading' && <div className="muted small">Generating suggestions...</div>}

                                            {aiSuggestStatus === 'error' && <div className="muted small">{aiSuggestError ?? 'AI error.'}</div>}

                                            {aiSuggestStatus === 'idle' && selectedRange !== null && aiSuggestionIds.length === 0 && (
                                                <div className="muted small">No suggestions yet for this selection.</div>
                                            )}

                                            {aiSuggestStatus !== 'loading' &&
                                                aiSuggestionIds.map((errorId) => {
                                                    const meta = ERROR_MAP[errorId]
                                                    const label = meta?.label ?? errorId
                                                    const pts = meta?.points ?? 0
                                                    const desc = meta?.description ?? ''
                                                    const count = selectedRange === null ? 0 : selectedRangeCountsByErrorId[errorId] ?? 0
                                                    const shownDeduction = selectedRange === null ? 0 : computeDeduction(errorId, Math.max(1, count))

                                                    // Renders the interface using the current data and interaction state.
                                                    return (
                                                        <div key={`ai-${errorId}`} className="suggestion-card" title={desc}>
                                                            <div className="suggestion-top">
                                                                <span className="suggestion-title">{label}</span>
                                                            </div>
                                                            {desc && <div className="suggestion-description">{desc}</div>}

                                                            <div className="suggestion-bottom">
                                                                <div className="instance-box" aria-label="Instances">
                                                                    <span className={`count-badge ${count > 0 ? 'active' : ''}`}>x{count}</span>
                                                                    <div className="count-controls" aria-label="Adjust count">
                                                                        <button
                                                                            type="button"
                                                                            className="count-btn plus"
                                                                            disabled={selectedRange === null}
                                                                            onClick={() => {
                                                                                if (selectedRange === null) return
                                                                                bumpErrorCount(selectedRange.start, selectedRange.end, errorId, 1)
                                                                            }}
                                                                            aria-label="Increase count"
                                                                            title="Increase count"
                                                                        >
                                                                            +
                                                                        </button>
                                                                        {selectedRange !== null && count > 0 && (
                                                                            <button
                                                                                type="button"
                                                                                className="count-btn minus"
                                                                                onClick={() => bumpErrorCount(selectedRange.start, selectedRange.end, errorId, -1)}
                                                                                aria-label="Decrease count"
                                                                                title="Decrease count"
                                                                            >
                                                                                −
                                                                            </button>
                                                                        )}
                                                                    </div>
                                                                </div>

                                                                <div className="points-box" aria-label="Point deduction">
                                                                    <span className="deduction-value">{selectedRange === null ? `-${pts}` : `-${shownDeduction}`}</span>
                                                                    <div className="points-controls" aria-label="Adjust points">
                                                                        <button
                                                                            type="button"
                                                                            className="points-btn plus"
                                                                            onClick={() => bumpErrorPoints(errorId, 1)}
                                                                            aria-label="Increase points for this error type"
                                                                            title="Increase points"
                                                                        >
                                                                            +
                                                                        </button>
                                                                        <button
                                                                            type="button"
                                                                            className="points-btn minus"
                                                                            onClick={() => bumpErrorPoints(errorId, -1)}
                                                                            disabled={pts <= 0}
                                                                            aria-label="Decrease points for this error type"
                                                                            title="Decrease points"
                                                                        >
                                                                            −
                                                                        </button>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    )
                                                })}
                                        </div>
                                    )}

                                    {aiHidden && <div className="muted small">AI suggestions are hidden.</div>}
                                </div>
                            </div>
                        </div>

                        <div className="grading-section">
                            {errorDefsError && <div className="muted small">{errorDefsError}</div>}
                            <details className="all-errors-picker" defaultChecked={false as any}>
                                <summary className="all-errors-picker-header">
                                    <span className="all-errors-picker-title">All errors</span>
                                    <span className="all-errors-picker-count">{ERROR_DEFS.length}</span>
                                </summary>
                                <div className="all-errors-picker-body">
                                    <div className="error-options-list">
                                        {ERROR_DEFS.map((err) => {
                                            const count = selectedRange ? selectedRangeCountsByErrorId[err.id] ?? 0 : 0
                                            const shownDeduction = selectedRange === null ? err.points : computeDeduction(err.id, Math.max(1, count))

                                            // Renders the interface using the current data and interaction state.
                                            return (
                                                <div key={err.id} className="suggestion-card" title={err.description}>
                                                    <div className="suggestion-top">
                                                        <span className="suggestion-title">{err.label}</span>
                                                    </div>
                                                    {err.description && <div className="suggestion-description">{err.description}</div>}

                                                    <div className="suggestion-bottom">
                                                        <div className="instance-box" aria-label="Instances">
                                                            <span className={`count-badge ${count > 0 ? 'active' : ''}`}>x{count}</span>
                                                            <div className="count-controls" aria-label="Adjust count">
                                                                <button
                                                                    type="button"
                                                                    className="count-btn plus"
                                                                    disabled={selectedRange === null}
                                                                    onClick={() => {
                                                                        if (selectedRange === null) return
                                                                        bumpErrorCount(selectedRange.start, selectedRange.end, err.id, 1)
                                                                    }}
                                                                    aria-label="Increase count"
                                                                    title="Increase count"
                                                                >
                                                                    +
                                                                </button>
                                                                {selectedRange !== null && count > 0 && (
                                                                    <button
                                                                        type="button"
                                                                        className="count-btn minus"
                                                                        onClick={() => bumpErrorCount(selectedRange.start, selectedRange.end, err.id, -1)}
                                                                        aria-label="Decrease count"
                                                                        title="Decrease count"
                                                                    >
                                                                        −
                                                                    </button>
                                                                )}
                                                            </div>
                                                        </div>

                                                        <div className="points-box" aria-label="Point deduction">
                                                            <span className="deduction-value">-{shownDeduction}</span>
                                                            <div className="points-controls" aria-label="Adjust points">
                                                                <button
                                                                    type="button"
                                                                    className="points-btn plus"
                                                                    onClick={() => bumpErrorPoints(err.id, 1)}
                                                                    aria-label="Increase points for this error type"
                                                                    title="Increase points"
                                                                >
                                                                    +
                                                                </button>
                                                                <button
                                                                    type="button"
                                                                    className="points-btn minus"
                                                                    onClick={() => bumpErrorPoints(err.id, -1)}
                                                                    disabled={err.points <= 0}
                                                                    aria-label="Decrease points for this error type"
                                                                    title="Decrease points"
                                                                >
                                                                    −
                                                                </button>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            )
                                        })}
                                    </div>

                                    <div className="custom-error-builder" aria-label="Add custom grading category">
                                        <div className="custom-error-title">Add a custom category</div>
                                        <input
                                            className="custom-error-input"
                                            type="text"
                                            value={newCustomLabel}
                                            onChange={(e) => setNewCustomLabel(e.target.value)}
                                            placeholder="Category label (required)"
                                        />
                                        <div className="custom-error-row">
                                            <input
                                                className="custom-error-points"
                                                type="number"
                                                min={0}
                                                value={Number.isFinite(newCustomPoints) ? newCustomPoints : 0}
                                                onChange={(e) => setNewCustomPoints(Number(e.target.value))}
                                            />
                                            <button type="button" className="custom-error-add-btn" onClick={addCustomErrorDef}>
                                                Add
                                            </button>
                                        </div>
                                        {customAddError && <div className="muted small">{customAddError}</div>}
                                    </div>

                                </div>

                            </details>

                            {selectedRange === null && <div className="muted small">Select a line to enable adjusting.</div>}

                        </div>

                        <div className="grading-section">
                            <div className="section-label">Errors on selected line(s)</div>

                            {selectedRange === null && <div className="muted">No line selected.</div>}

                            {selectedRange !== null && selectedRangeErrors.length === 0 && <div className="muted">No errors on this line.</div>}

                            {selectedRange !== null && selectedRangeErrors.length > 0 && (
                                <div className="line-error-list">
                                    {selectedRangeErrors.map((err, idx) => {
                                        const meta = ERROR_MAP[err.errorId]
                                        const label = meta?.label ?? err.errorId
                                        const desc = meta?.description ?? ''
                                        const count = Math.max(1, err.count ?? 1)
                                        const shownDeduction = computeDeduction(err.errorId, count)
                                        const ptsNow = meta?.points ?? 0
                                        const rangeLabel = err.startLine === err.endLine ? `Line ${err.startLine}` : `Lines ${err.startLine}-${err.endLine}`

                                        // Renders the interface using the current data and interaction state.
                                        return (
                                            <div key={`${err.startLine}-${err.endLine}-${err.errorId}-${idx}`} className="suggestion-card" title={desc}>
                                                <div className="suggestion-top">
                                                    <span className="suggestion-title">{label}</span>
                                                    <span className="muted small">{rangeLabel}</span>
                                                </div>
                                                {desc && <div className="suggestion-description">{desc}</div>}

                                                <div className="suggestion-bottom">
                                                    <div className="instance-box" aria-label="Instances">
                                                        <span className={`count-badge ${count > 0 ? 'active' : ''}`}>x{count}</span>
                                                        <div className="count-controls" aria-label="Adjust count">
                                                            <button
                                                                type="button"
                                                                className="count-btn plus"
                                                                onClick={() => bumpErrorCount(err.startLine, err.endLine, err.errorId, 1)}
                                                                aria-label="Increase count"
                                                                title="Increase count"
                                                            >
                                                                +
                                                            </button>
                                                            {getErrorCount(err.startLine, err.endLine, err.errorId) > 0 && (
                                                                <button
                                                                    type="button"
                                                                    className="count-btn minus"
                                                                    onClick={() => bumpErrorCount(err.startLine, err.endLine, err.errorId, -1)}
                                                                    aria-label="Decrease count"
                                                                    title="Decrease count"
                                                                >
                                                                    −
                                                                </button>
                                                            )}
                                                        </div>
                                                    </div>

                                                    <div className="points-box" aria-label="Point deduction">
                                                        <span className="deduction-value">-{shownDeduction}</span>
                                                        <div className="points-controls" aria-label="Adjust points">
                                                            <button
                                                                type="button"
                                                                className="points-btn plus"
                                                                onClick={() => bumpErrorPoints(err.errorId, 1)}
                                                                aria-label="Increase points for this error type"
                                                                title="Increase points"
                                                            >
                                                                +
                                                            </button>
                                                            <button
                                                                type="button"
                                                                className="points-btn minus"
                                                                onClick={() => bumpErrorPoints(err.errorId, -1)}
                                                                disabled={ptsNow <= 0}
                                                                aria-label="Decrease points for this error type"
                                                                title="Decrease points"
                                                            >
                                                                −
                                                            </button>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        )
                                    })}
                                </div>
                            )}
                        </div>
                    </aside>
                }
            />
        </div>
    )
}

export default AdminGrading
