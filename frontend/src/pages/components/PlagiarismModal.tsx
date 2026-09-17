// Install at frontend/src/pages/components/PlagiarismModal.tsx.
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import axios from 'axios'
import { diffChars, diffLines } from 'diff'
import '../../styling/PlagiarismModal.scss'

type Source = { name: string; content: string }
type Submission = { id: number; user_id: number; name: string; files: Source[]; warnings: string[]; token_count: number; ast_available: boolean }
type Pair = { left_id: number; right_id: number; overall: number | null; scores: { tokens: number | null; ast: number | null; dependency: number | null }; evidence: string }
type Report = { notice: string; methodology: string; analyzed_count: number; submitted_count: number; pair_count: number; returned_count: number; elapsed_seconds: number; rename_identifiers: boolean; starter_excluded: boolean; submissions: Submission[]; pairs: Pair[]; skipped: { id: number; name: string; reason: string }[] }
type Props = { projectId: number; classId: number; checkpoint: boolean; checkpointId?: number; studentIds: number[]; onClose: () => void }
type Segment = { text: string; changed: boolean }
type Cell = { text: string; line: number; segments?: Segment[] }
type DiffRow = { left?: Cell; right?: Cell; kind: 'same' | 'changed' | 'left-only' | 'right-only' }
const percent = (score: number | null) => score === null ? 'N/A' : `${score.toFixed(1)}%`
const splitLines = (value: string) => value ? value.replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n') : []

function rowsForDiff(left: string, right: string): { rows: DiffRow[]; fallback: boolean } {
    const rows: DiffRow[] = []
    const a = splitLines(left), b = splitLines(right)
    // Preserve whitespace and identifiers; ignore only line-ending style and the final newline.
    const normalize = (lines: string[]) => lines.length ? lines.join('\n') + '\n' : ''
    const changes = diffLines(normalize(a), normalize(b), { timeout: 400 })
    const inlineDeadline = Date.now() + 150
    const append = (left?: Cell, right?: Cell) => {
        const kind = !left ? 'right-only' : !right ? 'left-only' : left.text === right.text ? 'same' : 'changed'
        if (kind === 'changed' && left && right && Date.now() < inlineDeadline && left.text.length + right.text.length <= 10000) {
            const parts = diffChars(left.text, right.text, { timeout: Math.max(1, Math.min(20, inlineDeadline - Date.now())) })
            if (parts) {
                left.segments = parts.filter(part => !part.added).map(part => ({ text: part.value, changed: !!part.removed }))
                right.segments = parts.filter(part => !part.removed).map(part => ({ text: part.value, changed: !!part.added }))
            }
        }
        rows.push({ left, right, kind })
    }
    if (!changes) {
        for (let i = 0; i < Math.max(a.length, b.length); i++) append(
            i < a.length ? { text: a[i], line: i + 1 } : undefined,
            i < b.length ? { text: b[i], line: i + 1 } : undefined,
        )
        return { rows, fallback: true }
    }
    let aLine = 1, bLine = 1
    for (let i = 0; i < changes.length; i++) {
        const part = changes[i]
        if (!part.added && !part.removed) {
            splitLines(part.value).forEach(text => append({ text, line: aLine++ }, { text, line: bLine++ }))
            continue
        }
        const removed: string[] = [], added: string[] = []
        while (i < changes.length && (changes[i].added || changes[i].removed)) {
            const chunk = changes[i]
                ; (chunk.removed ? removed : added).push(...splitLines(chunk.value))
            i++
        }
        i--
        for (let j = 0; j < Math.max(removed.length, added.length); j++) append(
            j < removed.length ? { text: removed[j], line: aLine++ } : undefined,
            j < added.length ? { text: added[j], line: bLine++ } : undefined,
        )
    }
    return { rows, fallback: false }
}

// Shared bands keep the ranked table and comparison statistics consistent.
const scoreBand = (score: number | null) => score === null ? 'unavailable' : score >= 80 ? 'high' : score >= 60 ? 'moderate' : 'low'
const scoreLabel = (score: number | null) => score === null ? 'Unavailable' : `${scoreBand(score)} similarity`
function Score({ value }: { value: number | null }) {
    return <span className={`plag-score plag-score-${scoreBand(value)}`} title={scoreLabel(value)}>{percent(value)}</span>
}

function Stats({ pair }: { pair: Pair }) {
    return <dl className="plag-stats">
        <div className={`plag-stat plag-score-${scoreBand(pair.overall)}`}><dt>Overall similarity</dt><dd>{percent(pair.overall)}</dd></div>
        <div className={`plag-stat plag-score-${scoreBand(pair.scores.tokens)}`}><dt>Tokens · 40%</dt><dd>{percent(pair.scores.tokens)}</dd></div>
        <div className={`plag-stat plag-score-${scoreBand(pair.scores.ast)}`}><dt>AST structure · 40%</dt><dd>{percent(pair.scores.ast)}</dd></div>
        <div className={`plag-stat plag-score-${scoreBand(pair.scores.dependency)}`}><dt>Dependency/control · 20%</dt><dd>{percent(pair.scores.dependency)}</dd></div>
    </dl>
}

function Comparison({ pair, left, right }: { pair: Pair; left: Submission; right: Submission }) {
    const [leftFile, setLeftFile] = useState(0)
    const [rightFile, setRightFile] = useState(0)
    const [activeChange, setActiveChange] = useState(-1)
    const grid = useRef<HTMLDivElement>(null)
    const a = left.files[leftFile], b = right.files[rightFile]
    const { rows, fallback } = useMemo(() => rowsForDiff(a?.content || '', b?.content || ''), [a, b])
    const changeStarts = useMemo(() => rows.reduce<number[]>((starts, row, i) => {
        if (row.kind !== 'same' && (i === 0 || rows[i - 1].kind === 'same')) starts.push(i)
        return starts
    }, []), [rows])
    useEffect(() => {
        if (activeChange < 0) return
        grid.current?.querySelectorAll<HTMLElement>('.plag-code-scroll').forEach(pane => {
            const row = pane.querySelector<HTMLElement>(`[data-row="${changeStarts[activeChange]}"]`)
            if (row) pane.scrollTop += row.getBoundingClientRect().top - pane.getBoundingClientRect().top - pane.clientHeight / 3
        })
    }, [activeChange, changeStarts])
    const moveChange = (direction: number) => {
        if (!changeStarts.length) return
        setActiveChange(current => current < 0 ? (direction > 0 ? 0 : changeStarts.length - 1)
            : (current + direction + changeStarts.length) % changeStarts.length)
    }
    const labels = { same: 'Identical text', changed: 'Changed text', 'left-only': 'Left only', 'right-only': 'Right only' }
    const symbols = { same: '=', changed: '~', 'left-only': '−', 'right-only': '+' }
    return <>
        <h3>{left.name} ↔ {right.name}</h3>
        <Stats pair={pair} />
        <p className="plag-comparison-note">This view compares the actual text in the selected files. Names, comments, spacing, and indentation count as text changes. Scores above measure broader similarity and may stay high even when the text differs.</p>
        {[left, right].map(s => s.warnings.length > 0 && <ul className="plag-warnings" key={s.id}>{s.warnings.map((warning, i) => <li key={i}>{s.name}: {warning}</li>)}</ul>)}
        <div className="plag-file-selectors">
            <label>{left.name} · submission #{left.id}<select value={leftFile} onChange={e => { setLeftFile(Number(e.target.value)); setActiveChange(-1) }}>{left.files.map((f, i) => <option key={i} value={i}>{f.name}</option>)}</select></label>
            <label>{right.name} · submission #{right.id}<select value={rightFile} onChange={e => { setRightFile(Number(e.target.value)); setActiveChange(-1) }}>{right.files.map((f, i) => <option key={i} value={i}>{f.name}</option>)}</select></label>
        </div>
        <div className="plag-match-toolbar">
            <div className="plag-legend"><span className="plag-exact-key">= Identical</span><span className="plag-changed-key">~ Changed</span><span className="plag-left-key">− Left only</span><span className="plag-right-key">+ Right only</span></div>
            <div className="plag-match-navigation">
                <button type="button" disabled={!changeStarts.length} onClick={() => moveChange(-1)} aria-label="Previous text change">←</button>
                <span role="status">{activeChange < 0 ? `${changeStarts.length} changes` : `Change ${activeChange + 1} of ${changeStarts.length}`}</span>
                <button type="button" disabled={!changeStarts.length} onClick={() => moveChange(1)} aria-label="Next text change">→</button>
            </div>
        </div>
        <p className="plag-code-help">Darker marks show text edits within changed lines. Empty gray rows keep insertions and deletions aligned. Line-ending style and a final newline are ignored.</p>
        {fallback && <p className="plag-warnings" role="status">Text alignment timed out. Lines are compared by position; inserted lines may shift later comparisons.</p>}
        {!rows.length && <p>Both selected files are empty.</p>}
        {!!rows.length && !changeStarts.length && <p role="status">The selected files have identical text under the line-ending rules above.</p>}
        <div className="plag-code-panes" ref={grid}>
            {(['left', 'right'] as const).map(side => <div key={side} className="plag-code-scroll" data-pane={side} tabIndex={0} aria-label={`${side === 'left' ? left.name : right.name} code`}
                onScroll={event => {
                    const other = grid.current?.querySelector<HTMLElement>(`[data-pane="${side === 'left' ? 'right' : 'left'}"]`)
                    if (other && other.scrollTop !== event.currentTarget.scrollTop) other.scrollTop = event.currentTarget.scrollTop
                }}>
                <table className="plag-code"><thead><tr><th scope="col" aria-label="Line number">#</th><th scope="col">{side === 'left' ? a?.name : b?.name}</th></tr></thead>
                    <tbody>{rows.map((row, index) => {
                        const cell = row[side]
                        const selected = activeChange >= 0 && index === changeStarts[activeChange]
                        return <tr key={index} data-row={index} data-line={cell?.line} className={`${cell ? `plag-text-${row.kind}` : 'plag-placeholder'} ${selected ? 'plag-selected-change' : ''}`}>
                            <td className="plag-line-number">{cell?.line ?? ''}</td>
                            <td title={cell ? labels[row.kind] : 'No corresponding line'}><div className="plag-code-line">
                                <span className="plag-change-symbol" aria-label={cell ? labels[row.kind] : 'No corresponding line'}>{cell ? symbols[row.kind] : ''}</span>
                                <pre>{cell?.segments ? cell.segments.map((segment, i) => segment.changed
                                    ? <mark key={i} className={`plag-inline-${side}`}>{segment.text}</mark>
                                    : <React.Fragment key={i}>{segment.text}</React.Fragment>) : cell?.text || ' '}</pre>
                            </div></td>
                        </tr>
                    })}</tbody>
                </table>
            </div>)}
        </div>
    </>
}

export default function PlagiarismModal(props: Props) {
    const { onClose } = props
    const [report, setReport] = useState<Report | null>(null)
    const [busy, setBusy] = useState(true)
    const [error, setError] = useState('')
    const [selected, setSelected] = useState<Pair | null>(null)
    const [minimum, setMinimum] = useState(0)
    const dialog = useRef<HTMLDialogElement>(null)
    const requestRef = useRef<AbortController | null>(null)
    const sequence = useRef(0)
    const initialProps = useRef(props)
    const closeRef = useRef(onClose)
    closeRef.current = onClose

    async function run() {
        requestRef.current?.abort()
        const controller = new AbortController()
        requestRef.current = controller
        const runId = ++sequence.current
        setBusy(true); setError(''); setSelected(null); setReport(null)
        const p = initialProps.current
        try {
            const response = await axios.post<Report>(`${import.meta.env.VITE_API_URL}/projects/run-plagiarism`, {
                project_id: p.projectId, class_id: p.classId, checkpoint: p.checkpoint,
                checkpoint_id: p.checkpoint ? p.checkpointId : undefined,
                student_ids: p.studentIds, rename_identifiers: true,
            }, { headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` }, signal: controller.signal, timeout: 60000 })
            if (runId === sequence.current) setReport(response.data)
        } catch (err: unknown) {
            if (runId !== sequence.current || controller.signal.aborted) return
            const message = axios.isAxiosError(err) ? err.response?.data?.message || err.message : 'Unable to run analysis.'
            setError(String(message))
        } finally {
            if (runId === sequence.current && !controller.signal.aborted) setBusy(false)
        }
    }

    useEffect(() => {
        const previousFocus = document.activeElement as HTMLElement | null
        const overflow = document.body.style.overflow
        document.body.style.overflow = 'hidden'
        dialog.current?.showModal()
        void run()
        return () => {
            sequence.current++
            requestRef.current?.abort()
            document.body.style.overflow = overflow
            previousFocus?.focus()
        }
        // The component is mounted once per run-button click; snapshot its scope.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    const byId = useMemo(() => new Map(report?.submissions.map(s => [s.id, s]) || []), [report])
    const pairs = report?.pairs.filter(p => p.overall === null ? minimum === 0 : p.overall >= minimum) || []
    return createPortal(<dialog className="plag-modal" ref={dialog} aria-labelledby="plag-title" onCancel={e => { e.preventDefault(); closeRef.current() }}>
        <header><h2 id="plag-title">Python similarity screening</h2><button type="button" autoFocus onClick={onClose} aria-label="Close plagiarism screening">Close</button></header>
        <p className="plag-notice">Similarity helps identify submissions to review; it is not proof of plagiarism.</p>
        {selected && report ? <>
            <button type="button" className="plag-back" onClick={() => setSelected(null)}>← Back to ranked results</button>
            <Comparison key={`${selected.left_id}-${selected.right_id}`} pair={selected} left={byId.get(selected.left_id)!} right={byId.get(selected.right_id)!} />
        </> : <>
            <div className="plag-introduction">
                <p>We compare each student’s latest Python submission in the current roster. Earlier attempts and comparisons between the same student’s submissions are excluded.</p>
                <p><strong>How scoring works.</strong> The overall score combines code sequences (tokens, 40%), program structure (AST, 40%), and dependencies/control flow (20%). Unavailable measures are excluded from the weighted total. Shared assignment logic and short programs can produce high scores.</p>
            </div>
            {busy && <p role="status">Analyzing Python submissions and ranking pairs…</p>}
            {error && <p className="plag-error" role="alert">{error}</p>}
            {report && <>
                <p role="status">{report.analyzed_count} submissions analyzed · {report.pair_count} pairs compared · Top {report.returned_count} returned</p>
                {report.skipped.length > 0 && <details open className="plag-warnings"><summary>Skipped submissions ({report.skipped.length})</summary><ul>{report.skipped.map(s => <li key={s.id}>{s.name}: {s.reason}</li>)}</ul></details>}
                <div className="plag-filter" role="group" aria-labelledby="plag-minimum-label">
                    <div className="plag-filter-heading">
                        <label id="plag-minimum-label" htmlFor="plag-minimum-range">Minimum similarity</label>
                        <span className="plag-filter-count" role="status">{pairs.length} of {report.returned_count} pairs shown</span>
                    </div>
                    <div className="plag-filter-presets" aria-label="Minimum similarity presets">
                        {[{ value: 0, label: 'All pairs' }, { value: 60, label: '60%+' }, { value: 80, label: '80%+' }, { value: 90, label: '90%+' }].map(preset => <button key={preset.value} type="button" aria-pressed={minimum === preset.value} onClick={() => setMinimum(preset.value)}>{preset.label}</button>)}
                    </div>
                    <div className="plag-filter-controls">
                        <input id="plag-minimum-range" type="range" min={0} max={100} step={1} style={{ background: `linear-gradient(to right, #285f9e ${minimum}%, #dce4ed ${minimum}%)` }} value={minimum} aria-valuetext={`${minimum}%`} aria-describedby="plag-minimum-help" onChange={e => setMinimum(Number(e.target.value))} />
                        <div className="plag-filter-value"><input aria-label="Minimum similarity percentage" type="number" min={0} max={100} step={1} value={minimum} onChange={e => setMinimum(Math.max(0, Math.min(100, Math.round(Number(e.target.value) || 0))))} /><span aria-hidden="true">%</span></div>
                    </div>
                    <p id="plag-minimum-help">Overall similarity of {minimum}% or higher{minimum === 0 ? "; includes unavailable scores" : ""}.</p>
                </div>
                <p className="plag-score-legend" aria-label="Similarity color scale">
                    <span className="plag-score plag-score-low">Below 60% · Low</span>
                    <span className="plag-score plag-score-moderate">60–79.9% · Moderate</span>
                    <span className="plag-score plag-score-high">80–100% · High</span>
                </p>
                {report.analyzed_count < 2 ? <p>At least two students with analyzable Python submissions are needed.</p> : pairs.length === 0 ? <p>No pairs meet this filter.</p> : <div className="plag-results-scroll"><table className="plag-results"><caption>Highest similarity first — review required for every pair</caption>
                    <thead><tr><th>Student A</th><th>Student B</th><th>Overall</th><th>Tokens</th><th>AST</th><th>Dependency/control</th><th>Comparison</th></tr></thead>
                    <tbody>{pairs.map(pair => <tr key={`${pair.left_id}-${pair.right_id}`} className={`plag-row-${scoreBand(pair.overall)}`}>
                        <td>{byId.get(pair.left_id)?.name}<small>#{pair.left_id}</small></td><td>{byId.get(pair.right_id)?.name}<small>#{pair.right_id}</small></td>
                        <td><Score value={pair.overall} /></td><td><Score value={pair.scores.tokens} /></td><td><Score value={pair.scores.ast} /></td><td><Score value={pair.scores.dependency} /></td>
                        <td><button type="button" onClick={() => setSelected(pair)} aria-label={`Compare ${byId.get(pair.left_id)?.name} and ${byId.get(pair.right_id)?.name}`}>Compare</button></td>
                    </tr>)}</tbody>
                </table></div>}
            </>}
        </>}
    </dialog>, document.body)
}
