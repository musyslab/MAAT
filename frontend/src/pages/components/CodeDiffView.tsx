// CodeDiffView.tsx: Renders the code diff view interface and coordinates its local data and interactions.
// frontend/src/pages/components/CodeDiffView.tsx
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import { diffChars } from 'diff'
import '../../styling/CodeDiffView.scss'
import { Highlight, themes, Prism } from 'prism-react-renderer'

import {
    FaArrowRight,
    FaRegCheckSquare,
    FaChevronDown,
    FaEye,
    FaLock,
    FaBars,
    FaColumns,
    FaGripLines,
    FaAlignJustify,
    FaKeyboard,
    FaSearch,
    FaStar,
} from 'react-icons/fa'

// Ensure Prism languages (like Java) are registered once per page load.
let prismLangsLoaded = false
let prismLangsPromise: Promise<void> | null = null
// Helper for ensure prism langs loaded used by this component.
function ensurePrismLangsLoaded() {
    if (prismLangsLoaded) return Promise.resolve()
    if (prismLangsPromise) return prismLangsPromise

        ; (globalThis as any).Prism = (globalThis as any).Prism ?? Prism
    prismLangsPromise = Promise.all([
        import('prismjs/components/prism-java'),
        // Optional: keep python explicit too (safe even if already present)
        import('prismjs/components/prism-python'),
    ]).then(() => {
        prismLangsLoaded = true
    }).catch((error) => {
        prismLangsPromise = null
        throw error
    })

    return prismLangsPromise
}

// Describes the diff mode data expected by this file.
type DiffMode = 'short' | 'long'
// Describes the diff layout data expected by this file.
type DiffLayout = 'stacked' | 'side-by-side'
// Describes the ui log action data expected by this file.
type UiLogAction = 'Diff Finder' | 'Diff Mode' | 'Diff Layout'

// Describes the testcase result data expected by this file.
type TestcaseResult = {
    name: string
    order?: number
    passed: boolean
    hidden?: boolean
    shortDiff?: string
    longDiff?: string
    shortDiffSameAsLong?: boolean
}

// Describes the testcase payload data expected by this file.
type TestcasePayload = {
    results?: TestcaseResult[]
}

// Describes the diff entry data expected by this file.
type DiffEntry = {
    id: string
    num: number
    order: number
    test: string
    status: string
    passed: boolean
    skipped: boolean
    shortDiff: string
    longDiff: string
    shortDiffSameAsLong: boolean
    hidden: boolean
}

// Describes the code file data expected by this file.
type CodeFile = {
    name: string
    content: string
}

// Describes the testcase input option data expected by this file.
type TestcaseInputOption = {
    testcase_id: number
    name: string
    order: number
    purchased: boolean
    result_status: 'passed' | 'failed' | 'skipped' | 'unavailable'
    purchase_eligible: boolean
    input: string | null
}

// Describes the testcase input store data expected by this file.
type TestcaseInputStore = {
    project_id: number
    checkpoint_id: number
    is_checkpoint: boolean
    input_cost: number
    star_balance: number
    testcases: TestcaseInputOption[]
}

// Describes the seg data expected by this file.
type Seg = { text: string; changed: boolean }

// Describes the diff cell kind data expected by this file.
type DiffCellKind = 'add' | 'del' | 'ctx' | 'meta' | 'add-header' | 'del-header' | 'empty'

// Describes the side by side row data expected by this file.
type SideBySideRow = {
    key: string
    leftText: string
    rightText: string
    leftKind: DiffCellKind
    rightKind: DiffCellKind
    leftSegs?: Seg[]
    rightSegs?: Seg[]
    headerReversed?: boolean
}

const MAX_CHANGE_RATIO_FOR_INTRA = 0.7
const SHARED_SIDE_SCROLLBAR_EPSILON_PX = 1
const INPUT_EVENT_PATTERN = /\[\[\[MAAT_INPUT_(?:B64:([A-Za-z0-9_-]*)|HIDDEN)\]\]\]/g
const HIDDEN_INPUT_EVENT = '[[[MAAT_INPUT_HIDDEN]]]'

// Converts a recorded input event into the values used by the input transcript.
function decodeInputEvent(encoded: string) {
    try {
        const padded = encoded.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(encoded.length / 4) * 4, '=')
        const binary = window.atob(padded)
        const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0))
        return new TextDecoder().decode(bytes)
    } catch {
        return 'Unreadable input'
    }
}

// Renders input transcript for this view.
function renderInputTranscript(text: string, keyPrefix: string): React.ReactNode {
    if (!text || !text.includes('[[[MAAT_INPUT_')) return text || '\u00A0'

    const parts: React.ReactNode[] = []
    let cursor = 0
    let eventIndex = 0

    for (const match of text.matchAll(INPUT_EVENT_PATTERN)) {
        const start = match.index ?? 0
        if (start > cursor) parts.push(text.slice(cursor, start))

        const inputIsHidden = match[1] === undefined
        const value = inputIsHidden ? '' : decodeInputEvent(match[1] ?? '')
        parts.push(
            <span
                key={`${keyPrefix}-input-${eventIndex}`}
                className={`input-event ${inputIsHidden ? 'is-hidden' : ''}`}
                aria-label={inputIsHidden ? 'Program input hidden' : `Program input: ${value || 'empty input'}`}
                title={
                    inputIsHidden
                        ? 'Reveal this testcase input to see its value'
                        : 'The program paused here and read one line of input'
                }
            >
                <span className="input-event__label">
                    <FaKeyboard aria-hidden="true" /> Input
                </span>
                <span className={`input-event__value ${!inputIsHidden && value === '' ? 'is-empty' : ''}`}>
                    {inputIsHidden ? (
                        <><FaLock aria-hidden="true" /> Hidden</>
                    ) : value === '' ? (
                        'empty input'
                    ) : (
                        value
                    )}
                </span>
            </span>
        )
        cursor = start + match[0].length
        eventIndex++
    }

    if (cursor < text.length) parts.push(text.slice(cursor))
    return parts
}

// Hides input events that should not be exposed in the displayed transcript.
function concealInputEvents(text: string) {
    return (text ?? '').replace(INPUT_EVENT_PATTERN, HIDDEN_INPUT_EVENT)
}

// Helper for diff mode state label used by this component.
function diffModeStateLabel(mode: DiffMode) {
    return mode === 'short' ? 'Short' : 'Long'
}

// Helper for diff layout state label used by this component.
function diffLayoutStateLabel(layout: DiffLayout) {
    return layout === 'side-by-side' ? 'Side by Side' : 'Vertical'
}

// Identifies changed portions within paired lines so the diff can highlight individual edits.
function intralineSegments(a: string, b: string): { a: Seg[]; b: Seg[] } | null {
    if (a.length + b.length > 4000) return null
    const parts = diffChars(a, b, { timeout: 10 })
    if (!parts) return null
    const changed = parts.reduce((total, part) =>
        total + (part.added || part.removed ? part.value.length : 0), 0)
    if (changed / Math.max(a.length, b.length, 1) > MAX_CHANGE_RATIO_FOR_INTRA) return null

    const left: Seg[] = []
    const right: Seg[] = []
    for (const part of parts) {
        const segment = { text: part.value, changed: Boolean(part.added || part.removed) }
        if (!part.added) left.push(segment)
        if (!part.removed) right.push(segment)
    }
    return { a: left, b: right }
}

// Renders segs for this view.
function renderSegs(segs: Seg[], cls: 'add-ch' | 'del-ch') {
    // Renders the interface using the current data and interaction state.
    return segs.map((seg, idx) =>
        seg.changed ? (
            <span key={idx} className={`intra ${cls}`}>
                {renderInputTranscript(seg.text, `changed-${idx}`)}
            </span>
        ) : (
            <span key={idx}>{renderInputTranscript(seg.text, `same-${idx}`)}</span>
        )
    )
}

// Describes the diff view props data expected by this file.
type DiffViewProps = {
    submissionId: number
    classId: number
    isPractice?: boolean
    practiceProblemId?: number | null

    // Optional: enable grading-like behaviors (AdminGrading uses these)
    codeSectionTitle?: string
    diffViewRef?: React.RefObject<HTMLElement | null>
    codeContainerRef?: React.RefObject<HTMLDivElement | null>
    lineRefs?: React.MutableRefObject<Record<number, HTMLLIElement | null>>
    getLineClassName?: (lineNo: number) => string
    onLineMouseEnter?: (lineNo: number) => void
    onLineMouseLeave?: (lineNo: number) => void
    onLineMouseDown?: (lineNo: number) => void
    onLineMouseUp?: () => void
    rightPanel?: React.ReactNode
    betweenDiffAndCode?: React.ReactNode
    belowCode?: React.ReactNode
    onActiveTestcaseChange?: (tc: { name: string; num: number; passed: boolean; longDiff: string; shortDiff: string }) => void

    // If true: prevent selection/copying in the diff (student view).
    // If false/undefined: allow selecting/copying (admin views).
    disableCopy?: boolean

    // If true: show the "Output hidden" banner, but still reveal the diff/output below (admin views).
    // If false/undefined: keep hidden outputs hidden (student view).
    revealHiddenOutput?: boolean

    // Student-only store shown once at the bottom of the complete testcase menu.
    allowTestcaseInputPurchases?: boolean
}

// Displays submission code and test-case differences with selectable layouts.
export default function DiffView(props: DiffViewProps) {
    const {
        submissionId,
        classId,
        diffViewRef,
        codeSectionTitle = 'Submitted Code',
        codeContainerRef,
        lineRefs,
        getLineClassName,
        onLineMouseEnter,
        onLineMouseLeave,
        onLineMouseDown,
        onLineMouseUp,
        rightPanel,
        betweenDiffAndCode,
        belowCode,
        onActiveTestcaseChange,
        disableCopy = false,
        revealHiddenOutput = false,
        allowTestcaseInputPurchases = false,
        isPractice = false,
        practiceProblemId = null,
    } = props

    // Keeps internal code container ref available across renders without triggering a state update.
    const internalCodeContainerRef = useRef<HTMLDivElement | null>(null)
    const effectiveCodeContainerRef = codeContainerRef ?? internalCodeContainerRef

    // Keeps side by side left ref available across renders without triggering a state update.
    const sideBySideLeftRef = useRef<HTMLDivElement | null>(null)
    // Keeps side by side right ref available across renders without triggering a state update.
    const sideBySideRightRef = useRef<HTMLDivElement | null>(null)
    // Keeps side by side bar ref available across renders without triggering a state update.
    const sideBySideBarRef = useRef<HTMLDivElement | null>(null)
    // Keeps side by side left content ref available across renders without triggering a state update.
    const sideBySideLeftContentRef = useRef<HTMLDivElement | null>(null)
    // Keeps side by side right content ref available across renders without triggering a state update.
    const sideBySideRightContentRef = useRef<HTMLDivElement | null>(null)
    // Keeps syncing side scroll ref available across renders without triggering a state update.
    const syncingSideScrollRef = useRef(false)

    const copyBlockHandlers = disableCopy
        ? {
            onCopy: (e: React.ClipboardEvent) => e.preventDefault(),
            onCut: (e: React.ClipboardEvent) => e.preventDefault(),
        }
        : {}

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [testsLoaded, setTestsLoaded] = useState(false)
    const [payload, setPayload] = useState<TestcasePayload>({ results: [] })
    const [testcaseInputStore, setTestcaseInputStore] = useState<TestcaseInputStore | null>(null)
    const [selectedTestcaseInputId, setSelectedTestcaseInputId] = useState<number | null>(null)
    const [testcaseInputStoreLoaded, setTestcaseInputStoreLoaded] = useState(false)
    const [testcaseInputPurchaseError, setTestcaseInputPurchaseError] = useState('')
    const [inputPurchaseConfirmationOpen, setInputPurchaseConfirmationOpen] = useState(false)
    const [isPurchasingTestcaseInput, setIsPurchasingTestcaseInput] = useState(false)

    // Force a rerender after Prism languages load so Highlight can use the grammar.
    const [, forcePrismRefresh] = useState(0)
    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        let cancelled = false
        ensurePrismLangsLoaded()
            .then(() => { if (!cancelled) forcePrismRefresh((v) => v + 1) })
            .catch(() => { /* Existing grammars remain usable if optional imports fail. */ })
        return () => { cancelled = true }
    }, [])

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [codeFiles, setCodeFiles] = useState<CodeFile[]>([])
    const [selectedCodeFile, setSelectedCodeFile] = useState<string>('')

    const [selectedDiffId, setSelectedDiffId] = useState<string | null>(null)
    const [diffMode, setDiffMode] = useState<DiffMode>('long')
    const [diffLayout, setDiffLayout] = useState<DiffLayout>('side-by-side')

    // Intra-line highlight toggle
    const initialIntraRef = useRef<boolean>(true)
    // Keeps the values that drive this component’s display and user interactions in React state.
    const [intraEnabled, setIntraEnabled] = useState<boolean>(initialIntraRef.current)

    // Track which (submissionId,classId) we've already logged to avoid duplicate logs (React StrictMode)
    const initLogKeyRef = useRef<string | null>(null)
    const currentScope = `${submissionId}:${classId}:${isPractice}:${practiceProblemId}`
    const currentScopeRef = useRef(currentScope)
    currentScopeRef.current = currentScope
    const purchaseInFlightRef = useRef(false)
    const mountedRef = useRef(false)
    useEffect(() => {
        mountedRef.current = true
        purchaseInFlightRef.current = false
        setIsPurchasingTestcaseInput(false)
        return () => { mountedRef.current = false }
    }, [currentScope])

    // Helper for log ui click used by this component.
    const logUiClick = (
        action: UiLogAction,
        startedState?: boolean,
        previousStateLabel?: string,
        nextStateLabel?: string
    ) => {
        const shouldLogStarted = action === 'Diff Finder'
        if (submissionId <= 0 || classId <= 0) return
        axios.post(
            `${import.meta.env.VITE_API_URL}/submissions/log_ui_click`,
            {
                id: submissionId,
                class_id: classId,
                action,
                started_state: shouldLogStarted ? startedState : undefined,
                previous_state_label: previousStateLabel,
                next_state_label: nextStateLabel,
                practice: isPractice,
                practice_problem_id: practiceProblemId,
            },
            { headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` } }
        ).catch(() => { /* Audit logging must not interrupt the view. */ })
    }

    // Keeps the two code panes aligned while the user scrolls.
    const syncSideBySideScroll = (source: 'left' | 'right' | 'bar') => {
        if (syncingSideScrollRef.current) return

        const left = sideBySideLeftRef.current
        const right = sideBySideRightRef.current
        const bar = sideBySideBarRef.current

        if (!left || !right || !bar) return

        syncingSideScrollRef.current = true

        if (source === 'bar') {
            const nextScrollLeft = bar.scrollLeft
            left.scrollLeft = nextScrollLeft
            right.scrollLeft = nextScrollLeft
        } else if (source === 'left') {
            // Keep the shared scrollbar visually aligned to the most recently moved pane,
            // but do not move the other pane.
            bar.scrollLeft = left.scrollLeft
        } else {
            // Keep the shared scrollbar visually aligned to the most recently moved pane,
            // but do not move the other pane.
            bar.scrollLeft = right.scrollLeft
        }

        requestAnimationFrame(() => {
            syncingSideScrollRef.current = false
        })
    }

    // Returns side by side content width for this view.
    const getSideBySideContentWidth = (
        pane: HTMLDivElement | null,
        content: HTMLDivElement | null
    ) => {
        if (!pane) return 0

        const cellWidths = content
            ? Array.from(content.querySelectorAll<HTMLElement>('.sbs-cell')).map((el) =>
                Math.max(el.scrollWidth, el.offsetWidth, el.getBoundingClientRect().width)
            )
            : []

        return Math.max(
            pane.scrollWidth,
            pane.offsetWidth,
            pane.getBoundingClientRect().width,
            content?.scrollWidth ?? 0,
            content?.offsetWidth ?? 0,
            content?.getBoundingClientRect().width ?? 0,
            cellWidths.reduce((largest, width) => Math.max(largest, width), 0)
        )
    }

    // Measures the diff content so the shared horizontal scrollbar matches its width.
    const updateSharedSideScrollMetrics = () => {
        const left = sideBySideLeftRef.current
        const right = sideBySideRightRef.current
        const bar = sideBySideBarRef.current

        if (!left || !right || !bar) return

        const maxPaneScrollWidth = Math.max(
            getSideBySideContentWidth(left, sideBySideLeftContentRef.current),
            getSideBySideContentWidth(right, sideBySideRightContentRef.current)
        )

        const paneClientWidth = Math.min(
            left.clientWidth || Number.POSITIVE_INFINITY,
            right.clientWidth || Number.POSITIVE_INFINITY
        )
        const barClientWidth = bar.clientWidth

        if (!Number.isFinite(paneClientWidth) || paneClientWidth <= 0 || barClientWidth <= 0) return

        const scrollableDistance = Math.max(0, maxPaneScrollWidth - paneClientWidth)
        const nextWidth = Math.max(
            barClientWidth + SHARED_SIDE_SCROLLBAR_EPSILON_PX,
            barClientWidth + scrollableDistance
        )

        bar.style.setProperty('--side-by-side-bar-inner-width', `${nextWidth}px`)

        const inner = bar.firstElementChild as HTMLDivElement | null
        if (inner) inner.style.width = `${nextWidth}px`

        const maxBarScrollLeft = Math.max(0, nextWidth - barClientWidth)
        if (bar.scrollLeft > maxBarScrollLeft) bar.scrollLeft = maxBarScrollLeft
    }

    // Keeps the fetch testcase diff payload callback stable until its dependencies change.
    const fetchTestcaseDiffPayload = useCallback((signal?: AbortSignal) =>
        axios
            .get(
                `${import.meta.env.VITE_API_URL}/submissions/get_testcase_errors?id=${submissionId}&class_id=${classId}` +
                `&practice=${isPractice ? 1 : 0}` +
                (practiceProblemId != null ? `&practice_problem_id=${practiceProblemId}` : ``),
                {
                    headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
                    signal,
                }
            )
            .then((res) => {
                return res.data as TestcasePayload
            }),
        [submissionId, classId, isPractice, practiceProblemId])

    // Loads or refreshes view data when the dependencies below change.
    useEffect(() => {
        setTestsLoaded(false)
        setPayload({ results: [] })
        setSelectedDiffId(null)
        setCodeFiles([])
        setSelectedCodeFile('')

        if (submissionId < 0 || classId < 0) {
            setPayload({ results: [] })
            setTestsLoaded(true)
            return
        }

        let cancelled = false
        const controller = new AbortController()
        fetchTestcaseDiffPayload(controller.signal)
            .then((nextPayload) => {
                if (cancelled) return
                setPayload(nextPayload)
                setTestsLoaded(true)
            })
            .catch((err) => {
                if (cancelled) return
                console.log(err)
                setPayload({ results: [] })
                setTestsLoaded(true)
            })
        return () => { cancelled = true; controller.abort() }
    }, [submissionId, classId, isPractice, practiceProblemId, fetchTestcaseDiffPayload])

    // Loads or refreshes view data when the dependencies below change.
    useEffect(() => {
        setTestcaseInputStore(null)
        setSelectedTestcaseInputId(null)
        setTestcaseInputStoreLoaded(false)
        setTestcaseInputPurchaseError('')
        setInputPurchaseConfirmationOpen(false)

        if (!allowTestcaseInputPurchases || submissionId <= 0 || classId <= 0) {
            setTestcaseInputStoreLoaded(true)
            return
        }

        const controller = new AbortController()
        axios
            .get(
                `${import.meta.env.VITE_API_URL}/submissions/testcase_inputs`,
                {
                    signal: controller.signal,
                    params: {
                        id: submissionId,
                        class_id: classId,
                    },
                    headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
                }
            )
            .then((res) => {
                if (controller.signal.aborted) return
                const store = res.data as TestcaseInputStore
                const testcases = Array.isArray(store?.testcases) ? store.testcases : []
                const normalizedStore = { ...store, testcases }

                const visibleTestcases = testcases.filter(
                    (testcase) => testcase.purchased || testcase.purchase_eligible
                )

                setTestcaseInputStore(normalizedStore)
                setSelectedTestcaseInputId(visibleTestcases[0]?.testcase_id ?? null)
                setTestcaseInputStoreLoaded(true)
            })
            .catch((err) => {
                if (axios.isCancel(err)) return
                setTestcaseInputPurchaseError(
                    err?.response?.data?.message || 'Could not load testcase input options.'
                )
                setTestcaseInputStoreLoaded(true)
            })
        return () => controller.abort()
    }, [allowTestcaseInputPurchases, submissionId, classId])

    // Baseline the toggles on mount per submission/class
    useEffect(() => {
        if (submissionId <= 0 || classId <= 0) return
        const key = `${submissionId}:${classId}`
        if (initLogKeyRef.current === key) return
        initLogKeyRef.current = key
        logUiClick(
            'Diff Mode',
            diffMode === 'long',
            diffModeStateLabel(diffMode),
            diffModeStateLabel(diffMode)
        )
        logUiClick(
            'Diff Layout',
            diffLayout === 'side-by-side',
            diffLayoutStateLabel(diffLayout),
            diffLayoutStateLabel(diffLayout)
        )
        logUiClick(
            'Diff Finder',
            initialIntraRef.current,
            initialIntraRef.current ? 'On' : 'Off',
            initialIntraRef.current ? 'On' : 'Off'
        )
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [submissionId, classId, isPractice, practiceProblemId])

    // Loads or refreshes view data when the dependencies below change.
    useEffect(() => {
        if (submissionId < 0 || classId < 0) {
            setCodeFiles([{ name: 'Submission', content: '' }])
            setSelectedCodeFile('Submission')
            return
        }

        const controller = new AbortController()
        axios
            .get(
                `${import.meta.env.VITE_API_URL}/submissions/codefinder?id=${submissionId}&class_id=${classId}&format=json` +
                `&practice=${isPractice ? 1 : 0}` +
                (practiceProblemId != null ? `&practice_problem_id=${practiceProblemId}` : ``),
                { headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` }, signal: controller.signal }
            )
            .then((res) => {
                if (controller.signal.aborted) return
                const data = res.data as { files?: CodeFile[] }

                if (data && typeof data === 'object' && Array.isArray(data.files)) {
                    const files: CodeFile[] = data.files
                        .filter((f: any) => f && typeof f.name === 'string')
                        .map((f: any) => ({ name: String(f.name), content: String(f.content ?? '') }))

                    setCodeFiles(files)
                    setSelectedCodeFile((prev) =>
                        prev && files.some((ff) => ff.name === prev) ? prev : files[0]?.name ?? ''
                    )
                    return
                }

                setCodeFiles([{ name: 'Submission', content: '' }])
                setSelectedCodeFile('Submission')
            })
            .catch((err) => {
                if (axios.isCancel(err)) return
                console.log(err)
                setCodeFiles([{ name: 'Submission', content: '' }])
                setSelectedCodeFile('Submission')
            })
        return () => controller.abort()
    }, [submissionId, classId, isPractice, practiceProblemId])

    // Recomputes diff files all only when its dependencies change.
    const diffFilesAll: DiffEntry[] = useMemo(() => {
        const raw = Array.isArray(payload?.results) ? payload.results : []
        const entries: DiffEntry[] = []

        raw.forEach((r, idx: number) => {
            const rr = (r ?? {}) as TestcaseResult
            const testName = String(rr.name ?? `Test ${idx + 1}`)
            const passed = Boolean(rr.passed)
            const hidden = Boolean(rr.hidden)
            const shortDiff = String(rr.shortDiff ?? '')
            const longDiff = String(rr.longDiff ?? '')
            const shortDiffSameAsLong = Boolean(rr.shortDiffSameAsLong)
            const parsedOrder = Number(rr.order)
            const order = Number.isFinite(parsedOrder) && parsedOrder > 0
                ? parsedOrder
                : idx + 1
            entries.push({
                id: `${idx}__${testName}`,
                num: idx + 1,
                order,
                test: testName,
                status: passed ? 'Passed' : 'Failed',
                passed,
                skipped: false,
                shortDiff,
                longDiff,
                shortDiffSameAsLong,
                hidden,
            })
        })
        return entries
            .sort((a, b) => a.order - b.order || a.num - b.num)
            .map((entry, index) => ({ ...entry, num: index + 1 }))
    }, [payload])

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (diffFilesAll.length === 0) {
            if (selectedDiffId !== null) setSelectedDiffId(null)
        } else if (!selectedDiffId) {
            setSelectedDiffId(diffFilesAll[0].id)
        } else if (selectedDiffId && diffFilesAll.every((f) => f.id !== selectedDiffId)) {
            setSelectedDiffId(diffFilesAll[0]?.id ?? null)
        }
    }, [diffFilesAll, selectedDiffId])

    // Recomputes selected file only when its dependencies change.
    const selectedFile = useMemo(
        () => diffFilesAll.find((f) => f.id === selectedDiffId) || null,
        [diffFilesAll, selectedDiffId]
    )

    // Recomputes visible testcase input options only when its dependencies change.
    const visibleTestcaseInputOptions = useMemo(
        () =>
            (testcaseInputStore?.testcases ?? []).filter(
                (testcase) => testcase.purchased || testcase.purchase_eligible
            ),
        [testcaseInputStore]
    )

    // Recomputes selected testcase input only when its dependencies change.
    const selectedTestcaseInput = useMemo(
        () =>
            visibleTestcaseInputOptions.find(
                (testcase) => testcase.testcase_id === selectedTestcaseInputId
            ) ?? null,
        [visibleTestcaseInputOptions, selectedTestcaseInputId]
    )

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (
            selectedTestcaseInputId !== null &&
            visibleTestcaseInputOptions.some(
                (testcase) => testcase.testcase_id === selectedTestcaseInputId
            )
        ) {
            return
        }

        setSelectedTestcaseInputId(
            visibleTestcaseInputOptions[0]?.testcase_id ?? null
        )
        setInputPurchaseConfirmationOpen(false)
    }, [visibleTestcaseInputOptions, selectedTestcaseInputId])

    const testcaseInputCost = Math.max(0, Number(testcaseInputStore?.input_cost ?? 0))
    const testcaseInputStarBalance = Math.max(
        0,
        Number(testcaseInputStore?.star_balance ?? 0)
    )
    const testcaseInputStarLabel = testcaseInputCost === 1 ? 'star' : 'stars'

    // Formats star count for this view.
    const formatStarCount = (value: number) =>
        `${value} ${value === 1 ? 'star' : 'stars'}`

    // Helper for purchase selected testcase input used by this component.
    const purchaseSelectedTestcaseInput = () => {
        if (
            !selectedTestcaseInput ||
            selectedTestcaseInput.purchased ||
            !selectedTestcaseInput.purchase_eligible ||
            purchaseInFlightRef.current
        ) {
            return
        }

        const isCurrent = () => mountedRef.current && currentScopeRef.current === currentScope
        purchaseInFlightRef.current = true
        setIsPurchasingTestcaseInput(true)
        setTestcaseInputPurchaseError('')

        axios
            .post(
                `${import.meta.env.VITE_API_URL}/submissions/testcase_inputs`,
                {
                    submission_id: submissionId,
                    class_id: classId,
                    testcase_id: selectedTestcaseInput.testcase_id,
                },
                {
                    headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
                }
            )
            .then((res) => {
                if (!isCurrent()) return;
                const store = res.data as TestcaseInputStore
                setTestcaseInputStore({
                    ...store,
                    testcases: Array.isArray(store?.testcases) ? store.testcases : [],
                })
                setInputPurchaseConfirmationOpen(false)
                fetchTestcaseDiffPayload()
                    .then((nextPayload) => { if (isCurrent()) setPayload(nextPayload) })
                    .catch(() => {
                        if (!isCurrent()) return;
                        setTestcaseInputPurchaseError(
                            'The input was revealed, but the diff could not refresh. Reload the page to view it.'
                        )
                    })
            })
            .catch((err) => {
                if (!isCurrent()) return;
                const nextBalance = Number(err?.response?.data?.star_balance)
                if (Number.isFinite(nextBalance)) {
                    setTestcaseInputStore((current) =>
                        current
                            ? { ...current, star_balance: Math.max(0, nextBalance) }
                            : current
                    )
                }

                setTestcaseInputPurchaseError(
                    err?.response?.data?.message || 'Could not reveal this testcase input.'
                )
                setInputPurchaseConfirmationOpen(false)
            })
            .finally(() => {
                if (!isCurrent()) return;
                purchaseInFlightRef.current = false
                setIsPurchasingTestcaseInput(false)
            })
    }

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (!onActiveTestcaseChange) return
        if (!selectedFile) return
        onActiveTestcaseChange({
            name: selectedFile.test,
            num: selectedFile.num,
            passed: selectedFile.passed,
            longDiff: selectedFile.longDiff ?? '',
            shortDiff: selectedFile.shortDiff ?? '',
        })
    }, [selectedFile, onActiveTestcaseChange])

    // Recomputes show layout toggle only when its dependencies change.
    const showLayoutToggle = useMemo(() => {
        if (!selectedFile || selectedFile.passed) return false
        if (selectedFile.hidden && !revealHiddenOutput) return false
        return true
    }, [selectedFile, revealHiddenOutput])

    // Recomputes show diff mode toggle only when its dependencies change.
    const showDiffModeToggle = useMemo(() => {
        if (!selectedFile || selectedFile.passed) return false
        if (selectedFile.hidden && !revealHiddenOutput) return false
        return !selectedFile.shortDiffSameAsLong
    }, [selectedFile, revealHiddenOutput])

    // If short and long are identical, force long so we never show an empty "short".
    useEffect(() => {
        if (!selectedFile || selectedFile.passed) return
        if (selectedFile.shortDiffSameAsLong && diffMode !== 'long') {
            setDiffMode('long')
        }
    }, [selectedFile, diffMode])

    // Recomputes selected testcase input for diff only when its dependencies change.
    const selectedTestcaseInputForDiff = useMemo(
        () =>
            (testcaseInputStore?.testcases ?? []).find(
                (testcase) => testcase.order === selectedFile?.order
            ) ?? null,
        [testcaseInputStore, selectedFile]
    )

    // Recomputes selected diff text only when its dependencies change.
    const selectedDiffText = useMemo(() => {
        if (!selectedFile) return ''
        if (selectedFile.passed) return ''
        if (selectedFile.hidden && !revealHiddenOutput) return ''
        const diffText = selectedFile.shortDiffSameAsLong
            ? selectedFile.longDiff ?? ''
            : diffMode === 'short'
                ? selectedFile.shortDiff ?? ''
                : selectedFile.longDiff ?? ''

        const canRevealInput =
            !allowTestcaseInputPurchases || Boolean(selectedTestcaseInputForDiff?.purchased)

        return canRevealInput ? diffText : concealInputEvents(diffText)
    }, [
        selectedFile,
        diffMode,
        revealHiddenOutput,
        allowTestcaseInputPurchases,
        selectedTestcaseInputForDiff,
    ])

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (diffLayout !== 'side-by-side') return

        if (sideBySideLeftRef.current) sideBySideLeftRef.current.scrollLeft = 0
        if (sideBySideRightRef.current) sideBySideRightRef.current.scrollLeft = 0
        if (sideBySideBarRef.current) sideBySideBarRef.current.scrollLeft = 0
    }, [diffLayout, selectedDiffId, diffMode])

    // Reuse each character comparison across the toggle and both layouts.
    const intraCache = useMemo(() => ({ values: new Map<string, ReturnType<typeof intralineSegments>>(), deadline: 0 }), [selectedDiffText])
    // Keeps the get intraline segments callback stable until its dependencies change.
    const getIntralineSegments = useCallback((left: string, right: string) => {
        if (left.length + right.length > 4000) return null
        const key = JSON.stringify([left, right])
        if (intraCache.values.has(key)) return intraCache.values.get(key) ?? null
        if (!intraCache.deadline) intraCache.deadline = performance.now() + 100
        const result = performance.now() < intraCache.deadline ? intralineSegments(left, right) : null
        intraCache.values.set(key, result)
        return result
    }, [intraCache])

    // Recomputes has intra in selected only when its dependencies change.
    const hasIntraInSelected = useMemo(() => {
        if (!selectedFile || selectedFile.passed) return false
        if (selectedFile.hidden && !revealHiddenOutput) return false
        const txt = selectedDiffText || ''
        const lines = txt.split('\n')
        for (let i = 0; i < lines.length - 1; i++) {
            const line = lines[i] ?? ''
            if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@')) continue
            const next = lines[i + 1] ?? ''

            const isSingleAdd = line.startsWith('+') && !line.startsWith('+++')
            const isSingleDel = line.startsWith('-') && !line.startsWith('---')
            const nextIsSingleAdd = next.startsWith('+') && !next.startsWith('+++')
            const nextIsSingleDel = next.startsWith('-') && !next.startsWith('---')
            const pairable = (isSingleDel && nextIsSingleAdd) || (isSingleAdd && nextIsSingleDel)

            if (!pairable) continue

            const delText = (isSingleDel ? line : next).slice(1)
            const addText = (isSingleDel ? next : line).slice(1)
            if (getIntralineSegments(delText, addText)) return true
        }
        return false
    }, [selectedFile, selectedDiffText, revealHiddenOutput, getIntralineSegments])

    // Recomputes side by side rows only when its dependencies change.
    const sideBySideRows = useMemo<SideBySideRow[]>(() => {
        const txt = selectedDiffText || ''
        if (!txt.trim()) return []

        const lines = txt.split('\n')
        const rows: SideBySideRow[] = []

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i] ?? ''
            const next = lines[i + 1] ?? ''

            if (line.startsWith('---') && next.startsWith('+++')) {
                rows.push({
                    key: `hdr-${i}`,
                    leftText: line,
                    rightText: next,
                    leftKind: 'del-header',
                    rightKind: 'add-header',
                })
                i++
                continue
            }

            if (line.startsWith('+++') && next.startsWith('---')) {
                rows.push({
                    key: `hdr-${i}`,
                    headerReversed: true,
                    leftText: next,
                    rightText: line,
                    leftKind: 'del-header',
                    rightKind: 'add-header',
                })
                i++
                continue
            }

            if (line.startsWith('---')) {
                rows.push({
                    key: `hdr-left-${i}`,
                    leftText: line,
                    rightText: '',
                    leftKind: 'del-header',
                    rightKind: 'empty',
                })
                continue
            }

            if (line.startsWith('+++')) {
                rows.push({
                    key: `hdr-right-${i}`,
                    leftText: '',
                    rightText: line,
                    leftKind: 'empty',
                    rightKind: 'add-header',
                })
                continue
            }

            if (line.startsWith('@@')) {
                rows.push({
                    key: `meta-${i}`,
                    leftText: line,
                    rightText: line,
                    leftKind: 'meta',
                    rightKind: 'meta',
                })
                continue
            }

            const isSingleAdd = line.startsWith('+') && !line.startsWith('+++')
            const isSingleDel = line.startsWith('-') && !line.startsWith('---')
            const nextIsSingleAdd = next.startsWith('+') && !next.startsWith('+++')
            const nextIsSingleDel = next.startsWith('-') && !next.startsWith('---')
            const pairable = (isSingleDel && nextIsSingleAdd) || (isSingleAdd && nextIsSingleDel)

            if (pairable) {
                const delLine = isSingleDel ? line : next
                const addLine = isSingleDel ? next : line
                const delText = delLine.slice(1)
                const addText = addLine.slice(1)

                const segments = intraEnabled ? getIntralineSegments(delText, addText) : null
                if (segments) {
                    const { a, b } = segments
                    rows.push({
                        key: `pair-${i}`,
                        leftText: delLine,
                        rightText: addLine,
                        leftKind: 'del',
                        rightKind: 'add',
                        leftSegs: a,
                        rightSegs: b,
                    })
                } else {
                    rows.push({
                        key: `pair-${i}`,
                        leftText: delLine,
                        rightText: addLine,
                        leftKind: 'del',
                        rightKind: 'add',
                    })
                }

                i++
                continue
            }

            if (isSingleDel) {
                rows.push({
                    key: `del-${i}`,
                    leftText: line,
                    rightText: '',
                    leftKind: 'del',
                    rightKind: 'empty',
                })
                continue
            }

            if (isSingleAdd) {
                rows.push({
                    key: `add-${i}`,
                    leftText: '',
                    rightText: line,
                    leftKind: 'empty',
                    rightKind: 'add',
                })
                continue
            }

            rows.push({
                key: `ctx-${i}`,
                leftText: line,
                rightText: line,
                leftKind: 'ctx',
                rightKind: 'ctx',
            })
        }

        return rows
    }, [selectedDiffText, intraEnabled, getIntralineSegments])

    useLayoutEffect(() => {
        if (diffLayout !== 'side-by-side') return
        if (sideBySideRows.length === 0) return

        let cancelled = false
        let frame1 = 0
        let frame2 = 0
        const timeouts: number[] = []

        // Refreshes metrics for this view.
        const refreshMetrics = () => {
            if (cancelled) return
            updateSharedSideScrollMetrics()
        }

        refreshMetrics()

        frame1 = requestAnimationFrame(() => {
            refreshMetrics()

            frame2 = requestAnimationFrame(() => {
                refreshMetrics()
            })
        })

            // The side-by-side layout can finish sizing after async data, fonts, and parent panels settle.
            // Rechecking a few times prevents the shared bar from staying at its initial no-overflow width.
            ;[0, 50, 150, 500].forEach((delay) => {
                timeouts.push(window.setTimeout(refreshMetrics, delay))
            })

        const resizeObserver =
            typeof ResizeObserver !== 'undefined' ? new ResizeObserver(refreshMetrics) : null

            ;[
                sideBySideLeftRef.current,
                sideBySideRightRef.current,
                sideBySideBarRef.current,
                sideBySideLeftContentRef.current,
                sideBySideRightContentRef.current,
                sideBySideBarRef.current?.parentElement ?? null,
                sideBySideLeftRef.current?.closest('.diff-code') ?? null,
                sideBySideLeftRef.current?.closest('.diff-pane') ?? null,
            ].forEach((el) => {
                if (el && resizeObserver) resizeObserver.observe(el)
            })

        const mutationObserver =
            typeof MutationObserver !== 'undefined' ? new MutationObserver(refreshMetrics) : null

            ;[sideBySideLeftContentRef.current, sideBySideRightContentRef.current].forEach((el) => {
                if (el && mutationObserver) {
                    mutationObserver.observe(el, { childList: true, subtree: true, characterData: true })
                }
            })

        window.addEventListener('resize', refreshMetrics)

        return () => {
            cancelled = true
            cancelAnimationFrame(frame1)
            cancelAnimationFrame(frame2)
            timeouts.forEach((timeout) => window.clearTimeout(timeout))
            resizeObserver?.disconnect()
            mutationObserver?.disconnect()
            window.removeEventListener('resize', refreshMetrics)
        }
    }, [diffLayout, selectedDiffId, selectedDiffText, intraEnabled, sideBySideRows.length])

    // Recomputes selected code only when its dependencies change.
    const selectedCode = useMemo(() => {
        if (codeFiles.length === 0) return null
        return codeFiles.find((f) => f.name === selectedCodeFile) ?? codeFiles[0]
    }, [codeFiles, selectedCodeFile])

    const codeText = selectedCode?.content ?? ''
    const language =
        selectedCode?.name?.endsWith('.py')
            ? 'python'
            : selectedCode?.name?.endsWith('.java')
                ? 'java'
                : 'clike'

    const isLineClickable = Boolean(onLineMouseDown)

    // Renders side by side cell for this view.
    const renderSideBySideCell = (text: string, kind: DiffCellKind, segs?: Seg[]) => {
        if (kind === 'empty') return <span className="sbs-placeholder">{'\u00A0'}</span>

        if (kind === 'add' || kind === 'del') {
            const rawText = text.slice(1)
            // Renders the interface using the current data and interaction state.
            return (
                <>
                    <span className="diff-sign">{kind === 'add' ? '+' : '-'}</span>
                    {segs
                        ? renderSegs(segs, kind === 'add' ? 'add-ch' : 'del-ch')
                        : renderInputTranscript(rawText, `side-${kind}`)}
                </>
            )
        }

        return renderInputTranscript(text, `side-${kind}`)
    }

    // Renders stacked diff for this view.
    const renderStackedDiff = () => {
        if (sideBySideRows.length === 0) {
            // Renders the interface using the current data and interaction state.
            return <div className="muted">No diff text was provided for this test in {diffMode}.</div>
        }

        // Renders the interface using the current data and interaction state.
        return sideBySideRows.flatMap((row) => {
            // Renders cell for this view.
            const renderCell = (text: string, kind: DiffCellKind, segs?: Seg[]) => {
                const cls = kind === 'del-header' ? 'del header'
                    : kind === 'add-header' ? 'add header'
                    : kind === 'meta' ? 'meta header' : kind
                const paired = row.leftKind === 'del' && row.rightKind === 'add'
                // Renders the interface using the current data and interaction state.
                return (
                    <div key={`${row.key}-${kind}`} className={`diff-line ${cls}`}>
                        {paired ? (
                            <><span className="diff-sign">{kind === 'del' ? '-' : '+'}</span>
                                {segs ? renderSegs(segs, kind === 'del' ? 'del-ch' : 'add-ch')
                                    : renderInputTranscript(text.slice(1), `stacked-${row.key}-${kind}`)}</>
                        ) : renderInputTranscript(text, `stacked-${row.key}-${kind}`)}
                    </div>
                )
            }

            if (row.leftKind === 'ctx' || row.leftKind === 'meta') {
                return [renderCell(row.leftText, row.leftKind)]
            }
            const cells: React.ReactElement[] = []
            if (row.leftKind !== 'empty') cells.push(renderCell(row.leftText, row.leftKind, row.leftSegs))
            if (row.rightKind !== 'empty') cells.push(renderCell(row.rightText, row.rightKind, row.rightSegs))
            return row.headerReversed ? cells.reverse() : cells
        })
    }

    // Renders side by side diff for this view.
    const renderSideBySideDiff = () => {
        if (sideBySideRows.length === 0) {
            // Renders the interface using the current data and interaction state.
            return <div className="muted">No diff text was provided for this test in {diffMode}.</div>
        }

        // Renders the interface using the current data and interaction state.
        return (
            <div className="diff-content side-by-side">
                <div className="diff-side-by-side-shell" role="region" aria-label="Side-by-side testcase diff">
                    <div className="diff-side-pane actual">
                        <div
                            className="sbs-pane-scroll"
                            ref={sideBySideLeftRef}
                            onScroll={() => syncSideBySideScroll('left')}
                        >
                            <div className="sbs-pane-content" ref={sideBySideLeftContentRef}>
                                {sideBySideRows.map((row) => (
                                    <div key={`left-${row.key}`} className={`diff-line sbs-cell ${row.leftKind}`}>
                                        {renderSideBySideCell(row.leftText, row.leftKind, row.leftSegs)}
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>

                    <div className="diff-side-pane expected">
                        <div
                            className="sbs-pane-scroll"
                            ref={sideBySideRightRef}
                            onScroll={() => syncSideBySideScroll('right')}
                        >
                            <div className="sbs-pane-content" ref={sideBySideRightContentRef}>
                                {sideBySideRows.map((row) => (
                                    <div key={`right-${row.key}`} className={`diff-line sbs-cell ${row.rightKind}`}>
                                        {renderSideBySideCell(row.rightText, row.rightKind, row.rightSegs)}
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                </div>

                <div
                    className="diff-side-by-side-bar"
                    ref={sideBySideBarRef}
                    onScroll={() => syncSideBySideScroll('bar')}
                    aria-label="Shared horizontal scrollbar for side-by-side diff"
                >
                    <div className="diff-side-by-side-bar-inner" />
                </div>
            </div>
        )
    }

    // Renders diff view section for this view.
    const renderDiffViewSection = () => (
        <section
            className={`diff-view ${disableCopy ? 'no-user-select' : ''}`}
            {...copyBlockHandlers}
            ref={diffViewRef}
        >
            <aside className="diff-sidebar">
                <ul className="diff-file-list">
                    {!testsLoaded && <li className="muted">Loading…</li>}
                    {testsLoaded && diffFilesAll.length === 0 && <li className="muted">No tests.</li>}
                    {[...diffFilesAll].sort((a, b) => a.num - b.num).map((f) => (
                        <li
                            key={f.id}
                            className={
                                'file-item ' +
                                (f.id === selectedDiffId ? 'selected ' : '') +
                                (f.passed ? 'passed' : 'failed')
                            }
                            onClick={() => setSelectedDiffId(f.id)}
                            title={`Testcase ${f.num}: ${f.test}`}
                        >
                            <div className="testcase-name">
                                <span className="tc-num">{f.num}.</span> {f.test}
                            </div>
                            <div className="testcase-sub">
                                <span className={'status-dot ' + (f.passed ? 'is-pass' : 'is-fail')} />
                                {f.status}
                            </div>
                        </li>
                    ))}
                </ul>

                {allowTestcaseInputPurchases && (
                    <div className="testcase-input-store">
                        <div className="testcase-input-store__heading">
                            <span>Reveal testcase input</span>
                            {testcaseInputStore && (
                                <span className="testcase-input-store__balance">
                                    <FaStar aria-hidden="true" />
                                    {testcaseInputStarBalance}
                                </span>
                            )}
                        </div>

                        {!testcaseInputStoreLoaded && (
                            <div className="testcase-input-store__muted">Loading input options…</div>
                        )}

                        {testcaseInputStoreLoaded && visibleTestcaseInputOptions.length === 0 && (
                            <div className="testcase-input-store__muted">
                                No failing or previously revealed testcase inputs are available.
                            </div>
                        )}

                        {selectedTestcaseInput && (
                            <>
                                <label
                                    className="testcase-input-store__label"
                                    htmlFor="testcase-input-select"
                                >
                                    Testcase
                                </label>
                                <div className="testcase-input-store__select-wrap">
                                    <select
                                        id="testcase-input-select"
                                        value={selectedTestcaseInputId ?? ''}
                                        onChange={(event) => {
                                            setSelectedTestcaseInputId(Number(event.target.value))
                                            setTestcaseInputPurchaseError('')
                                            setInputPurchaseConfirmationOpen(false)
                                        }}
                                    >
                                        {visibleTestcaseInputOptions.map((testcase) => (
                                            <option
                                                key={testcase.testcase_id}
                                                value={testcase.testcase_id}
                                            >
                                                {testcase.order}. {testcase.name}
                                                {testcase.purchased ? ' — Revealed' : ''}
                                            </option>
                                        ))}
                                    </select>
                                    <FaChevronDown aria-hidden="true" />
                                </div>

                                {selectedTestcaseInput.purchased ? (
                                    <div className="testcase-input-store__revealed">
                                        <span>Exact input</span>
                                        {selectedTestcaseInput.input === '' ? (
                                            <div className="testcase-input-store__empty-input">
                                                This testcase has no input.
                                            </div>
                                        ) : (
                                            <pre>{selectedTestcaseInput.input}</pre>
                                        )}
                                    </div>
                                ) : (
                                    <button
                                        type="button"
                                        className="testcase-input-store__purchase"
                                        disabled={
                                            !selectedTestcaseInput.purchase_eligible ||
                                            testcaseInputStarBalance < testcaseInputCost ||
                                            isPurchasingTestcaseInput
                                        }
                                        onClick={() => {
                                            setTestcaseInputPurchaseError('')
                                            setInputPurchaseConfirmationOpen(true)
                                        }}
                                    >
                                        <FaEye aria-hidden="true" />
                                        {testcaseInputStarBalance < testcaseInputCost
                                            ? `Need ${testcaseInputCost} ${testcaseInputStarLabel}`
                                            : `Reveal for ${testcaseInputCost} ${testcaseInputStarLabel}`}
                                    </button>
                                )}
                            </>
                        )}

                        {testcaseInputPurchaseError && (
                            <div className="testcase-input-store__error" role="alert">
                                {testcaseInputPurchaseError}
                            </div>
                        )}
                    </div>
                )}
            </aside>

            <div className="diff-pane">
                <div className="diff-toolbar">
                    <div className="diff-title">
                        {selectedFile ? `Testcase ${selectedFile.num}: ${selectedFile.test}` : 'No selection'}
                    </div>

                    <div className="spacer" />

                    {(showLayoutToggle || showDiffModeToggle) && (
                        <div className="diff-toolbar-mode-group">
                            {showLayoutToggle && (
                                <button
                                    type="button"
                                    className={`btn toggle-mode view-toggle ${diffLayout === 'side-by-side' ? 'on' : 'off'}`}
                                    aria-pressed={diffLayout === 'side-by-side'}
                                    onClick={() => {
                                        const next: DiffLayout = diffLayout === 'stacked' ? 'side-by-side' : 'stacked'
                                        logUiClick(
                                            'Diff Layout',
                                            diffLayout === 'side-by-side',
                                            diffLayoutStateLabel(diffLayout),
                                            diffLayoutStateLabel(next)
                                        )
                                        setDiffLayout(next)
                                    }}
                                    title="Switch between stacked and split diff views"
                                >
                                    <span className="toggle-copy">
                                        <span className="toggle-label">View</span>
                                        <span className="toggle-value">
                                            {diffLayout === 'side-by-side' ? 'Split' : 'Stacked'}
                                        </span>
                                    </span>
                                    <span className="toggle-icon" aria-hidden="true">
                                        {diffLayout === 'side-by-side' ? <FaColumns /> : <FaBars />}
                                    </span>
                                </button>
                            )}

                            {/* Button 1: shortDiff vs longDiff */}
                            {showDiffModeToggle && (
                                <button
                                    type="button"
                                    className={`btn toggle-mode scope-toggle ${diffMode === 'long' ? 'on' : 'off'}`}
                                    aria-pressed={diffMode === 'long'}
                                    onClick={() => {
                                        const next: DiffMode = diffMode === 'short' ? 'long' : 'short'

                                        logUiClick(
                                            'Diff Mode',
                                            diffMode === 'long',
                                            diffModeStateLabel(diffMode),
                                            diffModeStateLabel(next)
                                        )

                                        setDiffMode(next)
                                    }}
                                    title="Switch between changed lines only and all diff lines"
                                >
                                    <span className="toggle-copy">
                                        <span className="toggle-label">Lines</span>
                                        <span className="toggle-value">
                                            {diffMode === 'short' ? 'Differences' : 'All'}
                                        </span>
                                    </span>
                                    <span className="toggle-icon" aria-hidden="true">
                                        {diffMode === 'short' ? <FaGripLines /> : <FaAlignJustify />}
                                    </span>
                                </button>
                            )}
                        </div>
                    )}

                    {/* Button 2: Diff Finder */}
                    {selectedFile && !selectedFile.passed && (!selectedFile.hidden || revealHiddenOutput) && hasIntraInSelected && (
                        <button
                            type="button"
                            className={`btn toggle-intra ${intraEnabled ? 'on' : 'off'}`}
                            aria-pressed={intraEnabled}
                            disabled={!hasIntraInSelected}
                            onClick={() => {
                                const next = !intraEnabled

                                logUiClick(
                                    'Diff Finder',
                                    intraEnabled,
                                    intraEnabled ? 'On' : 'Off',
                                    next ? 'On' : 'Off'
                                )

                                setIntraEnabled(next)
                            }}
                            title={
                                hasIntraInSelected
                                    ? 'Toggle intra-line highlighting'
                                    : 'Intra-line highlighting is not available for this diff'
                            }
                        >
                            <span className="toggle-copy">
                                <span className="toggle-label">Diff Finder</span>
                                <span className="toggle-value">{intraEnabled ? 'On' : 'Off'}</span>
                            </span>
                            <span className="toggle-icon" aria-hidden="true">
                                <FaSearch />
                            </span>
                        </button>
                    )}
                </div>

                <div className={`diff-code ${diffLayout === 'side-by-side' ? 'side-by-side-mode' : ''}`}>
                    {!selectedFile && <div className="muted">Select a test on the left to view its diff.</div>}

                    {selectedFile && selectedFile.hidden && (
                        <div className="diff-content">
                            <div className="diff-empty hidden" role="status" aria-live="polite">
                                <div className="empty-icon" aria-hidden="true">
                                    <FaLock />
                                </div>
                                <div className="empty-text">
                                    <div className="empty-title">Output hidden</div>
                                    <div className="empty-subtitle">
                                        This testcase’s output is hidden. Result: {selectedFile.passed ? 'Passed' : 'Failed'}.
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    {selectedFile && (!selectedFile.hidden || revealHiddenOutput) && selectedFile.passed && (

                        <div className="diff-content">
                            <div className="diff-empty" role="status" aria-live="polite">
                                <div className="empty-icon" aria-hidden="true">
                                    <FaRegCheckSquare />
                                </div>
                                <div className="empty-text">
                                    <div className="empty-title">No differences found</div>
                                    <div className="empty-subtitle">Your program’s output matches the expected output.</div>
                                </div>
                            </div>
                        </div>
                    )}

                    {selectedFile && (!selectedFile.hidden || revealHiddenOutput) && !selectedFile.passed && (
                        diffLayout === 'side-by-side' ? renderSideBySideDiff() : <div className="diff-content">{renderStackedDiff()}</div>
                    )}
                </div>
            </div>
        </section>
    )

    // Renders code section for this view.
    const renderCodeSection = () => (
        <Highlight theme={themes.vsLight} code={codeText} language={language as any}>
            {({ style, tokens, getLineProps, getTokenProps }) => (
                <div
                    className={`code-block code-viewer ${isLineClickable ? 'line-clickable' : ''}`}
                    ref={effectiveCodeContainerRef}
                    onMouseLeave={onLineMouseUp ? () => onLineMouseUp() : undefined}
                    role="region"
                    aria-label="Submitted source code"
                >
                    <ol className="code-list" style={style}>
                        {tokens.map((line, i) => {
                            const lineNo = i + 1
                            const { key: _lineKey, ...lineProps } = getLineProps({ line, key: i })
                            const extraCls = getLineClassName ? getLineClassName(lineNo) : ''
                            // Renders the interface using the current data and interaction state.
                            return (
                                <li
                                    key={lineNo}
                                    ref={(el) => {
                                        if (lineRefs) lineRefs.current[lineNo] = el
                                    }}
                                    {...lineProps}
                                    className={`code-line ${extraCls} ${lineProps.className ?? ''}`}
                                    onMouseDown={onLineMouseDown ? () => onLineMouseDown(lineNo) : undefined}
                                    onMouseEnter={onLineMouseEnter ? () => onLineMouseEnter(lineNo) : undefined}
                                    onMouseLeave={onLineMouseLeave ? () => onLineMouseLeave(lineNo) : undefined}
                                    onMouseUp={onLineMouseUp ? () => onLineMouseUp() : undefined}
                                    title={
                                        onLineMouseDown ? 'Click this line to add or view grading errors' : undefined
                                    }
                                >
                                    <span className="gutter">
                                        <span className="line-number">{lineNo}</span>
                                    </span>
                                    <span className="code-text">
                                        {line.map((token, key) => {
                                            const { key: _tokenKey, ...tokenProps } = getTokenProps({ token, key })
                                            // Renders the interface using the current data and interaction state.
                                            return <span key={key} {...tokenProps} />
                                        })}
                                    </span>
                                </li>
                            )
                        })}
                    </ol>
                </div>
            )}
        </Highlight>
    )

    // Renders the interface using the current data and interaction state.
    return (
        <>
            {rightPanel ? (
                <div className="diff-code-panel">
                    <div className="diff-and-code">
                        {renderDiffViewSection()}

                        {betweenDiffAndCode}

                        {/* ==================== CODE SECTION (BOTTOM) ==================== */}
                        <section className="code-section">
                            <h2 className="section-title">{codeSectionTitle}</h2>
                            {codeFiles.length === 0 && <div className="no-data-message">Fetching submitted code…</div>}

                            {codeFiles.length > 0 && (
                                <>
                                    {codeFiles.length > 1 && (
                                        <div className="code-file-picker">
                                            <label className="section-label" htmlFor="codefile-select">
                                                File Selection
                                            </label>
                                            <div className="select-wrap">
                                                <select
                                                    id="codefile-select"
                                                    className="select"
                                                    value={selectedCodeFile}
                                                    onChange={(e) => setSelectedCodeFile(e.target.value)}
                                                >
                                                    {codeFiles.map((f) => (
                                                        <option key={f.name} value={f.name}>
                                                            {f.name}
                                                        </option>
                                                    ))}
                                                </select>
                                                <FaChevronDown className="select-icon" aria-hidden="true" />
                                            </div>
                                        </div>
                                    )}

                                    {renderCodeSection()}
                                </>
                            )}
                        </section>
                        {belowCode}
                    </div>

                    {rightPanel}
                </div>
            ) : (
                <>
                    {renderDiffViewSection()}

                    {betweenDiffAndCode}

                    {/* ==================== CODE SECTION (BOTTOM) ==================== */}
                    <section className="code-section">
                        <h2 className="section-title">{codeSectionTitle}</h2>
                        {codeFiles.length === 0 && <div className="no-data-message">Fetching submitted code…</div>}

                        {codeFiles.length > 0 && (
                            <>
                                {codeFiles.length > 1 && (
                                    <div className="code-file-picker">
                                        <label className="section-label" htmlFor="codefile-select">
                                            File Selection
                                        </label>
                                        <div className="select-wrap">
                                            <select
                                                id="codefile-select"
                                                className="select"
                                                value={selectedCodeFile}
                                                onChange={(e) => setSelectedCodeFile(e.target.value)}
                                            >
                                                {codeFiles.map((f) => (
                                                    <option key={f.name} value={f.name}>
                                                        {f.name}
                                                    </option>
                                                ))}
                                            </select>
                                            <FaChevronDown className="select-icon" aria-hidden="true" />
                                        </div>
                                    </div>
                                )}

                                {renderCodeSection()}
                            </>
                        )}
                    </section>
                    {belowCode}
                </>
            )}

            {inputPurchaseConfirmationOpen &&
                selectedTestcaseInput?.purchase_eligible &&
                !selectedTestcaseInput.purchased ? (
                <div
                    className="skip-cooldown-confirmation"
                    onMouseDown={(event) => {
                        if (
                            event.target === event.currentTarget &&
                            !isPurchasingTestcaseInput
                        ) {
                            setInputPurchaseConfirmationOpen(false)
                        }
                    }}
                >
                    <div
                        className="skip-cooldown-confirmation__dialog"
                        role="alertdialog"
                        aria-modal="true"
                        aria-labelledby="testcase-input-confirmation-title"
                        aria-describedby="testcase-input-confirmation-description"
                        onKeyDown={(event) => {
                            if (event.key === 'Escape' && !isPurchasingTestcaseInput) {
                                setInputPurchaseConfirmationOpen(false)
                            }
                        }}
                    >
                        <span
                            className="skip-cooldown-confirmation__icon"
                            aria-hidden="true"
                        >
                            <FaStar />
                        </span>
                        <h2 id="testcase-input-confirmation-title">
                            Spend {testcaseInputCost} {testcaseInputStarLabel}?
                        </h2>
                        <p id="testcase-input-confirmation-description">
                            This will permanently reveal the exact input for Testcase{' '}
                            {selectedTestcaseInput.order}: {selectedTestcaseInput.name}. This
                            purchase cannot be undone.
                        </p>

                        <div className="skip-cooldown-confirmation__balance">
                            <span>
                                Current balance
                                <strong>{formatStarCount(testcaseInputStarBalance)}</strong>
                            </span>
                            <FaArrowRight aria-hidden="true" />
                            <span>
                                Balance after
                                <strong>
                                    {formatStarCount(
                                        Math.max(
                                            0,
                                            testcaseInputStarBalance - testcaseInputCost
                                        )
                                    )}
                                </strong>
                            </span>
                        </div>

                        <div className="skip-cooldown-confirmation__actions">
                            <button
                                type="button"
                                className="skip-cooldown-confirmation__cancel"
                                disabled={isPurchasingTestcaseInput}
                                onClick={() => setInputPurchaseConfirmationOpen(false)}
                                autoFocus
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                className="skip-cooldown-confirmation__confirm"
                                disabled={isPurchasingTestcaseInput}
                                onClick={purchaseSelectedTestcaseInput}
                            >
                                <FaEye aria-hidden="true" />
                                {isPurchasingTestcaseInput
                                    ? 'Spending...'
                                    : `Confirm and spend ${testcaseInputCost} ${testcaseInputStarLabel}`}
                            </button>
                        </div>
                    </div>
                </div>
            ) : null}
        </>
    )
}
