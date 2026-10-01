// AdminStudentList.tsx: Renders the admin student list interface and coordinates its local data and interactions.
// Replace frontend/src/pages/admin/AdminStudentList.tsx.
import React, { Component, CSSProperties } from 'react'
import axios from 'axios'
import { Helmet } from 'react-helmet'
import { Link, useParams } from 'react-router-dom'
import MenuComponent from '../components/MenuComponent'
import DirectoryBreadcrumbs from '../components/DirectoryBreadcrumbs'
import '../../styling/AdminStudentList.scss'
import PlagiarismModal from '../components/PlagiarismModal'
import { FaClone, FaFileExport, FaDownload, FaEye } from 'react-icons/fa'
// Displays student submission records and links to grading and code views.
const AdminStudentRoster = () => {
    // Reads the school, class, or assignment identifiers from the current route.
    const {
        school_id,
        class_id,
        module_id,
        id,
        checkpoint_id: route_checkpoint_id,
    } = useParams<{
        school_id: string;
        class_id: string;
        module_id: string;
        id: string;
        checkpoint_id?: string;
    }>()
    if (!school_id || !class_id || !module_id || !id) {
        // Renders the interface using the current data and interaction state.
        return <div>Error: school, class, module, or project id missing or invalid</div>
    }
    const project_id = Number(id)
    if (!Number.isSafeInteger(project_id) || project_id <= 0) {
        // Renders the interface using the current data and interaction state.
        return <div>Error: project id missing or invalid</div>
    }
    const parsedCheckpointId = Number(route_checkpoint_id)
    const checkpoint_id = Number.isInteger(parsedCheckpointId) && parsedCheckpointId > 0
        ? parsedCheckpointId
        : undefined
    const isCheckpoint = checkpoint_id !== undefined
    // Renders the interface using the current data and interaction state.
    return (
        <StudentListInternal
            project_id={project_id}
            school_id={school_id}
            class_id={class_id}
            module_id={module_id}
            isCheckpoint={isCheckpoint}
            checkpoint_id={checkpoint_id}
        />
    )
}
export default AdminStudentRoster
// Describes the student list props data expected by this file.
interface StudentListProps {
    project_id: number
    school_id: string
    class_id: string
    module_id: string
    isCheckpoint: boolean
    checkpoint_id?: number
}
class Row {
    constructor() {
        this.id = 0
        this.Lname = ''
        this.Fname = ''
        this.numberOfSubmissions = 0
        this.date = ''
        this.isPassing = false
        this.subid = 0
        this.lecture_number = 0
        this.lab_number = 0
        this.classId = ''
        this.grade = 0
        this.StudentNumber = ""
        this.IsLocked = false
        this.hidden = false
    }
    id: number
    Lname: string
    Fname: string
    numberOfSubmissions: number
    date: string
    isPassing: boolean
    subid: number
    lecture_number: number
    lab_number: number
    classId: string
    grade: number
    StudentNumber: string
    IsLocked: boolean
    hidden: boolean
}
// Describes the option data expected by this file.
interface Option {
    key: number
    text: string
    value: number
}
// Describes the student list state data expected by this file.
interface StudentListState {
    plagiarismOpen: boolean
    rows: Array<Row>
    lecture_numbers: Array<Option>
    lab_numbers: Array<Option>
    projectName: string
    selectedLecture: number
    selectedLab: number
    errorMessage: string
    sortBy: 'lastname' | 'lastsubmitted'
}
class StudentListInternal extends Component<StudentListProps, StudentListState> {
    constructor(props: StudentListProps) {
        super(props)
        this.state = {
            plagiarismOpen: false,
            rows: [],
            lecture_numbers: [{ key: -1, text: 'All', value: -1 }],
            lab_numbers: [{ key: -1, text: 'All', value: -1 }],
            projectName: '',
            selectedLecture: -1,
            selectedLab: -1,
            errorMessage: '',
            sortBy: 'lastname',
        }
        this.handleClick = this.handleClick.bind(this)
        this.handleLectureChange = this.handleLectureChange.bind(this)
        this.handleLabChange = this.handleLabChange.bind(this)
        this.handleSortChange = this.handleSortChange.bind(this)
        this.handleUnlockClick = this.handleUnlockClick.bind(this)
        this.downloadStudentCode = this.downloadStudentCode.bind(this)
        this.downloadProjectGrades = this.downloadProjectGrades.bind(this)
    }
    private formatDate12h(value: string): string {
        if (!value || value === 'N/A') return 'N/A'
        const d = new Date(value)
        if (Number.isNaN(d.getTime())) return value
        return new Intl.DateTimeFormat('en-US', {
            year: 'numeric',
            month: 'short',
            day: '2-digit',
            hour: 'numeric',
            minute: '2-digit',
            hour12: true,
        }).format(d)
    }
    private getProjectBaseUrl(): string {
        const baseUrl = `/admin/school/${this.props.school_id}/class/${this.props.class_id}/module/${this.props.module_id}/project/${this.props.project_id}`
        return this.props.isCheckpoint && this.props.checkpoint_id
            ? `${baseUrl}/checkpoint/${this.props.checkpoint_id}`
            : baseUrl
    }
    async downloadProjectGrades() {
        try {
            const url = `${import.meta.env.VITE_API_URL}/submissions/export_project_grades?project_id=${this.props.project_id}${this.props.isCheckpoint ? `&checkpoint=true${this.props.checkpoint_id ? `&checkpoint_id=${this.props.checkpoint_id}` : ''}` : ''}`
            // Fetches the server data needed for this operation.
            const res = await axios.get<Blob>(url, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
                responseType: 'blob',
            })
            const cd = String((res.headers as any)?.['content-disposition'] ?? '')
            const match = /filename\*?=(?:UTF-8''|")?([^\";]+)\"?/i.exec(cd)
            const fname = match
                ? decodeURIComponent(match[1])
                : `${res.headers['project-name']}-grades.csv`
            const a = document.createElement('a')
            a.href = URL.createObjectURL(res.data)
            a.download = fname
            document.body.appendChild(a)
            a.click()
            document.body.removeChild(a)
            URL.revokeObjectURL(a.href)
        } catch (_e) {
            window.alert('Failed to export to D2L. Please try again.')
        }
    }
    async downloadStudentCode(row: Row) {
        try {
            if (row.subid === -1) return
            const url = `${import.meta.env.VITE_API_URL}/submissions/codefinder?id=${row.subid}&class_id=${row.classId}`
            // Fetches the server data needed for this operation.
            const res = await axios.get<Blob>(url, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
                responseType: 'blob',
            })
            const cd = String((res.headers as any)?.['content-disposition'] ?? '')
            const match = /filename\*?=(?:UTF-8''|")?([^\";]+)\"?/i.exec(cd)
            const headerName = match ? decodeURIComponent(match[1]) : ''
            // Helper for safe used by this component.
            const safe = (s: string) => (s || '').replace(/\s+/g, '_')
            const fallback = `${safe(row.Fname)}_${safe(row.Lname)}_${row.subid}_submission.zip`
            const fname = headerName || fallback
            const a = document.createElement('a')
            a.href = URL.createObjectURL(res.data)
            a.download = fname
            document.body.appendChild(a)
            a.click()
            document.body.removeChild(a)
            URL.revokeObjectURL(a.href)
        } catch (_e) {
            window.alert('Failed to download code. Please try again.')
        }
    }
    private loadController: AbortController | null = null
    componentDidMount() {
        this.loadStudentData()
    }
    componentDidUpdate(previous: StudentListProps) {
        if (previous.project_id !== this.props.project_id ||
            previous.class_id !== this.props.class_id ||
            previous.isCheckpoint !== this.props.isCheckpoint ||
            previous.checkpoint_id !== this.props.checkpoint_id) {
            this.setState({ rows: [], projectName: '', errorMessage: '', selectedLecture: -1, selectedLab: -1 })
            this.loadStudentData()
        }
    }
    componentWillUnmount() {
        this.loadController?.abort()
    }
    private loadStudentData() {
        this.loadController?.abort()
        const controller = new AbortController()
        this.loadController = controller
        const projectId = this.props.project_id
        // Sends this operation and its payload to the server.
        const submissionsRequest = axios.post(
            import.meta.env.VITE_API_URL + `/submissions/recentsubproject`,
            {
                project_id: this.props.project_id,
                checkpoint: this.props.isCheckpoint,
                checkpoint_id: this.props.checkpoint_id ?? null,
                include_test_user: true,
            },
            {
                signal: controller.signal,
                headers: {
                    Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                },
            }
        );
        // Fetches the server data needed for this operation.
        const projectInfoRequest = axios.get(
            import.meta.env.VITE_API_URL +
            `/assignment_tracking/get_project?id=${this.props.project_id}` +
            `${this.props.isCheckpoint && this.props.checkpoint_id ? `&checkpoint_id=${this.props.checkpoint_id}` : ''}`,
            {
                signal: controller.signal,
                headers: {
                    Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                },
            }
        );
        Promise.all([submissionsRequest, projectInfoRequest])
            .then(([submissionsRes, projectInfoRes]) => {
                if (controller.signal.aborted) return
                const data = submissionsRes.data
                const projectInfo = projectInfoRes.data as Record<string, unknown[]>;
                const projectName = String(projectInfo[projectId]?.[0] ?? '').trim();
                const rows: Array<Row> = []
                const lectureSet = new Set<number>([-1])
                const labSet = new Set<number>([-1])
                Object.entries(data).forEach(([key, value]) => {
                    if (!Array.isArray(value)) return
                    const row = new Row()
                    const student_output_data = value as Array<any>
                    row.id = parseInt(key, 10)
                    row.Lname = String(student_output_data[0] ?? '')
                    row.Fname = String(student_output_data[1] ?? '')
                    const lectureNumber = parseInt(String(student_output_data[2] ?? ''), 10)
                    const labNumber = parseInt(String(student_output_data[3] ?? ''), 10)
                    row.lecture_number = Number.isFinite(lectureNumber) ? lectureNumber : -1
                    row.lab_number = Number.isFinite(labNumber) ? labNumber : -1
                    lectureSet.add(row.lecture_number)
                    labSet.add(row.lab_number)
                    row.numberOfSubmissions = Number(student_output_data[4]) || 0
                    row.date = String(student_output_data[5] ?? '')
                    const passRaw = String(student_output_data[6] ?? '').toLowerCase().trim()
                    row.isPassing =
                        passRaw === 'true' ||
                        passRaw === '1' ||
                        passRaw === 'pass' ||
                        passRaw === 'passed' ||
                        passRaw === 'ok' ||
                        passRaw === 'success'
                    const off = student_output_data[7] === 'N/A' ? 1 : 0
                    const submissionId = Number(student_output_data[7 + off])
                    row.subid = Number.isSafeInteger(submissionId) && submissionId > 0 ? submissionId : -1
                    row.classId = String(student_output_data[8 + off] ?? '')
                    row.grade = Number(student_output_data[9 + off]) || 0
                    row.StudentNumber = String(student_output_data[10 + off] ?? '')
                    const lockRaw = String(student_output_data[11 + off] ?? '').toLowerCase().trim()
                    row.IsLocked = lockRaw === 'true' || lockRaw === '1' || lockRaw === 'locked'
                    rows.push(row)
                })
                const lecture_numbers: Option[] = Array.from(lectureSet)
                    .filter((v) => v !== -1)
                    .sort((a, b) => a - b)
                    .map((v) => ({ key: v, text: String(v), value: v }))
                lecture_numbers.unshift({ key: -1, text: 'All', value: -1 })
                const lab_numbers: Option[] = Array.from(labSet)
                    .filter((v) => v !== -1)
                    .sort((a, b) => a - b)
                    .map((v) => ({ key: v, text: String(v), value: v }))
                lab_numbers.unshift({ key: -1, text: 'All', value: -1 })
                rows.sort((a, b) => a.Lname.localeCompare(b.Lname))
                this.setState({ rows, lecture_numbers, lab_numbers, projectName, errorMessage: '' })
            })
            .catch((error) => {
                if (controller.signal.aborted) return
                this.setState({ errorMessage: error?.response?.data?.message || 'Could not load students.' })
            })
    }
    handleLectureChange(ev: React.ChangeEvent<HTMLSelectElement>) {
        const value = parseInt(ev.target.value, 10)
        this.setState({ selectedLecture: value }, this.applyFilters)
    }
    handleLabChange(ev: React.ChangeEvent<HTMLSelectElement>) {
        const value = parseInt(ev.target.value, 10)
        this.setState({ selectedLab: value }, this.applyFilters)
    }
    handleSortChange(ev: React.ChangeEvent<HTMLSelectElement>) {
        const value = ev.target.value as 'lastname' | 'lastsubmitted'
        this.setState({ sortBy: value })
    }
    handleClick() {
        this.setState({ plagiarismOpen: true })
    }
    applyFilters = () => {
        const { selectedLecture, selectedLab } = this.state
        const new_rows = this.state.rows.map((row) => {
            const lectureOk = selectedLecture === -1 || row.lecture_number === selectedLecture
            const labOk = selectedLab === -1 || row.lab_number === selectedLab
            return { ...row, hidden: row.id !== -1 && !(lectureOk && labOk) }
        })
        this.setState({ rows: new_rows })
    }
    handleUnlockClick = (UserId: number) => {
        axios
            .post(
                import.meta.env.VITE_API_URL + `/assignment_tracking/unlockStudentAccount`,
                { UserId },
                {
                    headers: {
                        Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                    },
                }
            )
            .then((_res) => {
                window.location.reload()
            })
    }
    render() {
        const rowsForView = (() => {
            const visible = this.state.rows.filter((r) => !r.hidden)
            if (this.state.sortBy === 'lastsubmitted') {
                // Helper for time val used by this component.
                const timeVal = (r: Row) => {
                    const t = Date.parse(r.date)
                    return isNaN(t) ? -Infinity : t
                }
                return [...visible].sort((a, b) =>
                    a.id === -1 ? -1 : b.id === -1 ? 1 : timeVal(b) - timeVal(a))
            }
            return [...visible].sort((a, b) =>
                a.id === -1 ? -1 : b.id === -1 ? 1 : a.Lname.localeCompare(b.Lname) || a.Fname.localeCompare(b.Fname))
        })()
        const studentRows = rowsForView.filter((row) => row.id !== -1)
        const totalStudents = studentRows.length
        const submittedStudents = studentRows.filter((row) => row.subid !== -1).length
        const passingStudents = studentRows.filter((row) => row.subid !== -1 && row.isPassing).length
        const submittedPercent = totalStudents > 0 ? Math.round((submittedStudents / totalStudents) * 100) : 0
        const passingPercent = totalStudents > 0 ? Math.round((passingStudents / totalStudents) * 100) : 0
        const moduleListUrl = `/admin/school/${this.props.school_id}/class/${this.props.class_id}/modules`
        const moduleOverviewUrl = `/admin/school/${this.props.school_id}/class/${this.props.class_id}/module/${this.props.module_id}/overview`
        const projectBaseUrl = this.getProjectBaseUrl()
        // Renders the interface using the current data and interaction state.
        return (
            <div>
                {this.state.plagiarismOpen && (
                    <PlagiarismModal
                        projectId={this.props.project_id}
                        classId={Number(this.props.class_id)}
                        checkpoint={this.props.isCheckpoint}
                        checkpointId={this.props.checkpoint_id}
                        studentIds={studentRows.map(row => row.id)}
                        onClose={() => this.setState({ plagiarismOpen: false })}
                    />
                )}
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
                        { label: 'School Selection', to: '/schools' },
                        { label: 'Class Selection', to: `/admin/school/${this.props.school_id}/classes` },
                        { label: 'Admin Menu', to: `/admin/school/${this.props.school_id}/class/${this.props.class_id}/menu` },
                        { label: 'Module List', to: moduleListUrl },
                        { label: 'Module Details', to: moduleOverviewUrl },
                        { label: 'Student List' },
                    ]}
                />
                <div className="pageTitle">
                    {this.state.projectName
                        ? `${this.state.projectName}`
                        : 'Student List'}
                </div>
                {this.state.errorMessage && <div className="pageMessage" role="alert">{this.state.errorMessage}</div>}
                <div className="student-stats-panel" aria-label="Student submission statistics">
                    <div className="student-stat-card">
                        <div className="student-stat-copy">
                            <div className="student-stat-label">Students Submitted</div>
                            <div className="student-stat-value">
                                {submittedStudents} / {totalStudents}
                            </div>
                            <div className="student-stat-subtext">{submittedPercent}% submitted</div>
                        </div>
                        <div
                            className="student-stat-progress-ring"
                            style={
                                {
                                    "--progress-percent": `${submittedPercent}%`,
                                } as CSSProperties
                            }
                            aria-label={`${submittedPercent}% submitted`}
                        >
                            <span>{submittedPercent}%</span>
                        </div>
                    </div>
                    <div className="student-stat-card">
                        <div className="student-stat-copy">
                            <div className="student-stat-label">Passing All Testcases</div>
                            <div className="student-stat-value">
                                {passingStudents} / {totalStudents}
                            </div>
                            <div className="student-stat-subtext">{passingPercent}% passing</div>
                        </div>
                        <div
                            className="student-stat-progress-ring"
                            style={
                                {
                                    "--progress-percent": `${passingPercent}%`,
                                } as CSSProperties
                            }
                            aria-label={`${passingPercent}% passing`}
                        >
                            <span>{passingPercent}%</span>
                        </div>
                    </div>
                </div>
                <div className="main-grid">
                    <>
                        <div className="admin-project-config-container">
                            <div className="student-sub-panel">
                                <div className="filter-bar">
                                    {this.state.lecture_numbers.length > 1 && (
                                        <>
                                            <label className="filter-label" htmlFor="lectureFilter">
                                                Lecture:
                                            </label>
                                            <select
                                                id="lectureFilter"
                                                className="filter-select lecture-filter"
                                                onChange={this.handleLectureChange}
                                                value={this.state.selectedLecture}
                                            >
                                                {this.state.lecture_numbers.map((opt) => (
                                                    <option className="lecture-option" key={`lec-${opt.key}`} value={opt.value}>
                                                        {opt.text}
                                                    </option>
                                                ))}
                                            </select>
                                        </>
                                    )}
                                    {this.state.lab_numbers.length > 1 && (
                                        <>
                                            <label className="filter-label" htmlFor="labFilter">
                                                Lab:
                                            </label>
                                            <select
                                                id="labFilter"
                                                className="filter-select lab-filter"
                                                onChange={this.handleLabChange}
                                                value={this.state.selectedLab}
                                            >
                                                {this.state.lab_numbers.map((opt) => (
                                                    <option className="lab-option" key={`lab-${opt.key}`} value={opt.value}>
                                                        {opt.text}
                                                    </option>
                                                ))}
                                            </select>
                                        </>
                                    )}
                                    <>
                                        <button
                                            type="button"
                                            className="btn plagiarism-btn"
                                            onClick={this.handleClick}
                                            aria-label="Run Plagiarism Detector"
                                            title="Run Plagiarism Detector"
                                        >
                                            <FaClone aria-hidden="true" />
                                            Run Plagiarism Detector
                                        </button>
                                        <button
                                            type="button"
                                            className="btn export-btn"
                                            onClick={() => this.downloadProjectGrades()}
                                            aria-label="Export Student Grades"
                                            title="Export Student Grades"
                                        >
                                            <FaFileExport aria-hidden="true" />
                                            Export Grades to D2L
                                        </button>
                                    </>
                                    <div className="sort-control-group">
                                        <label className="filter-label" htmlFor="sortSelect">
                                            Sort by:
                                        </label>
                                        <select
                                            id="sortSelect"
                                            className="filter-select sort-select"
                                            value={this.state.sortBy}
                                            onChange={this.handleSortChange}
                                        >
                                            <option value="lastname">Last name (A→Z)</option>
                                            <option value="lastsubmitted">Last submitted (newest)</option>
                                        </select>
                                    </div>
                                </div>
                                <div className="table-scroll" role="region" aria-label="Student submissions" tabIndex={0}>
                                    <table className="students-table">
                                        <thead className="table-head">
                                            <tr className="table-row">
                                                <th className="col-student-name">Student</th>
                                                <th className="col-lecture-number">Lecture</th>
                                                <th className="col-lab-number">Lab</th>
                                                <th className="col-submissions">Submissions</th>
                                                <th className="col-date">Last Submitted</th>
                                                <th className="col-status">Status</th>
                                                <th className="col-view">View</th>
                                                <th className="col-download">Download</th>
                                                <th className="col-grade">Grade</th>
                                            </tr>
                                        </thead>
                                        <tbody className="table-body">
                                            {rowsForView.map((row) => {
                                                if (row.hidden) return null
                                                // Renders student name for this view.
                                                const renderStudentName = () => (
                                                    <td className="student-name-cell">
                                                        {row.Fname + ' ' + row.Lname}{' '}
                                                        {row.id === -1 && <span> (admin testing)</span>}
                                                        {row.IsLocked === true && (
                                                            <button className="btn unlock-btn" onClick={() => this.handleUnlockClick(row.id)}>
                                                                Unlock
                                                            </button>
                                                        )}
                                                    </td>
                                                );
                                                if (row.subid === -1) {
                                                    // Renders the interface using the current data and interaction state.
                                                    return (
                                                        <tr className="student-row student-row--no-submission" key={`row-${row.id}-na`}>
                                                            {renderStudentName()}
                                                            <td className="lecture-number-cell">
                                                                {row.lecture_number >= 0 ? row.lecture_number : 'N/A'}
                                                            </td>
                                                            <td className="lab-number-cell">
                                                                {row.lab_number >= 0 ? row.lab_number : 'N/A'}
                                                            </td>
                                                            <td className="submissions-cell">N/A</td>
                                                            <td className="date-cell">N/A</td>
                                                            <td className="status-cell">N/A</td>
                                                            <td className="view-cell">N/A</td>
                                                            <td className="download-cell">N/A</td>
                                                            <td className="grade-cell">
                                                                <input
                                                                    className="grade-input"
                                                                    type="text"
                                                                    placeholder="optional"
                                                                    value={row.grade}
                                                                    disabled
                                                                />
                                                                <Link
                                                                    to={`${projectBaseUrl}/grade/${row.subid}`}
                                                                    className="btn grade-btn"
                                                                    rel="noreferrer"
                                                                >
                                                                    Grade
                                                                </Link>
                                                            </td>
                                                        </tr>
                                                    )
                                                }
                                                // Renders the interface using the current data and interaction state.
                                                return (
                                                    <tr className="student-row" key={`row-${row.id}`}>
                                                        {renderStudentName()}
                                                        <td className="lecture-number-cell">
                                                            {row.lecture_number >= 0 ? row.lecture_number : 'N/A'}
                                                        </td>
                                                        <td className="lab-number-cell">
                                                            {row.lab_number >= 0 ? row.lab_number : 'N/A'}
                                                        </td>
                                                        <td className="submissions-cell">{row.numberOfSubmissions}</td>
                                                        <td className="date-cell">{this.formatDate12h(row.date)}</td>
                                                        <td className={row.isPassing ? 'status-cell status passed' : 'status-cell status failed'}>
                                                            {row.isPassing ? 'PASSED' : 'FAILED'}
                                                        </td>
                                                        <td className="view-cell">
                                                            <Link
                                                                className="view-link"
                                                                to={`${projectBaseUrl}/codeview/${row.subid}`}
                                                                rel="noreferrer"
                                                            >
                                                                <FaEye aria-hidden="true" /> View
                                                            </Link>
                                                        </td>
                                                        <td className="download-cell">
                                                            <button
                                                                className="btn download-btn"
                                                                onClick={() => this.downloadStudentCode(row)}
                                                                aria-label="Download code"
                                                                title="Download code"
                                                            >
                                                                <FaDownload aria-hidden="true" />
                                                            </button>
                                                        </td>
                                                        <td className="grade-cell">
                                                            <input
                                                                className="grade-input"
                                                                type="text"
                                                                placeholder="optional"
                                                                value={row.grade}
                                                                disabled
                                                            />
                                                            <Link
                                                                to={`${projectBaseUrl}/grade/${row.subid}`}
                                                                className="btn grade-btn"
                                                                rel="noreferrer"
                                                            >
                                                                Grade
                                                            </Link>
                                                        </td>
                                                    </tr>
                                                )
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                    </>
                </div>
            </div>
        )
    }
}
