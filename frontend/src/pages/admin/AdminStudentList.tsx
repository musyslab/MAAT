// Replace frontend/src/pages/admin/AdminStudentList.tsx.
import React, { Component, CSSProperties } from 'react'
import axios from 'axios'
import { Helmet } from 'react-helmet'
import { Link, useLocation, useParams } from 'react-router-dom'
import MenuComponent from '../components/MenuComponent'
import DirectoryBreadcrumbs from '../components/DirectoryBreadcrumbs'
import '../../styling/AdminStudentList.scss'
import PlagiarismModal from '../components/PlagiarismModal'

import { FaClone, FaFileExport, FaDownload, FaEye, FaHandPaper } from 'react-icons/fa'

const AdminStudentRoster = () => {
    const {
        school_id,
        class_id,
        module_id,
        id,
        checkpoint_id: route_checkpoint_id,
        practice_problem_id: route_practice_problem_id,
    } = useParams<{
        school_id: string;
        class_id: string;
        module_id: string;
        id: string;
        checkpoint_id?: string;
        practice_problem_id?: string;
    }>()

    const { search } = useLocation()

    if (!school_id || !class_id || !module_id || !id) {
        return <div>Error: school, class, module, or project id missing or invalid</div>
    }

    const project_id = parseInt(id, 10)
    if (Number.isNaN(project_id)) {
        return <div>Error: project id missing or invalid</div>
    }

    const params = new URLSearchParams(search)
    const truthyValues = ['1', 'true', 'yes', 'y', 'on']
    const checkpointParam = (params.get('checkpoint') || params.get('practice') || '').toLowerCase()
    const isCheckpoint = !!route_checkpoint_id || !!route_practice_problem_id || truthyValues.includes(checkpointParam)

    const checkpointIdParam = (
        route_checkpoint_id ||
        route_practice_problem_id ||
        params.get('checkpoint_id') ||
        params.get('practice_problem_id') ||
        ''
    ).trim()
    const parsedCheckpointId = parseInt(checkpointIdParam, 10)
    const checkpoint_id =
        isCheckpoint && !Number.isNaN(parsedCheckpointId) && parsedCheckpointId > 0 ? parsedCheckpointId : undefined

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
        this.StudentNumber = 0
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
    StudentNumber: number
    IsLocked: boolean
    hidden: boolean
    attendedOfficeHours: boolean = false
}

interface Option {
    key: number
    text: string
    value: number
}

interface StudentListState {
    plagiarismOpen: boolean
    rows: Array<Row>
    isLoading: boolean
    lecture_numbers: Array<Option>
    lab_numbers: Array<Option>
    projectName: string
    selectedStudent: number
    modalIsLoading: boolean
    modalIsOpen: boolean
    selectedStudentData: any[]
    selectedStudentCode: string
    selectedStudentTestResults: any[]
    selectedStudentName: string
    selectedStudentGrade: number | undefined
    exportModalIsOpen: boolean
    selectedLecture: number
    selectedLab: number
    projectLanguage: string

    activeView: 'table' | 'diff'
    selectedDiffId: string | null
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
            isLoading: false,
            projectName: '',
            selectedStudent: -1,
            modalIsLoading: false,
            modalIsOpen: false,
            selectedStudentData: [],
            selectedStudentCode: '',
            selectedStudentTestResults: [],
            selectedStudentName: '',
            selectedStudentGrade: 0,
            exportModalIsOpen: false,
            selectedLecture: -1,
            selectedLab: -1,
            projectLanguage: '',

            activeView: 'table',
            selectedDiffId: null,
            sortBy: 'lastname',
        }

        this.handleClick = this.handleClick.bind(this)

        this.handleLectureChange = this.handleLectureChange.bind(this)
        this.handleLabChange = this.handleLabChange.bind(this)
        this.handleSortChange = this.handleSortChange.bind(this)

        this.handleUnlockClick = this.handleUnlockClick.bind(this)
        this.submitGrades = this.submitGrades.bind(this)
        this.exportGrades = this.exportGrades.bind(this)
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

    private getCheckpointQuery(): string {
        return this.props.isCheckpoint
            ? `?checkpoint=true${this.props.checkpoint_id ? `&checkpoint_id=${this.props.checkpoint_id}` : ''}`
            : ''
    }

    private getProjectBaseUrl(): string {
        const baseUrl = `/admin/school/${this.props.school_id}/class/${this.props.class_id}/module/${this.props.module_id}/project/${this.props.project_id}`
        return this.props.isCheckpoint && this.props.checkpoint_id
            ? `${baseUrl}/checkpoint/${this.props.checkpoint_id}`
            : baseUrl
    }

    async downloadProjectGrades() {
        try {
            const url = `${import.meta.env.VITE_API_URL}/submissions/exportprojectgrades?project_id=${this.props.project_id}${this.props.isCheckpoint ? `&checkpoint=true${this.props.checkpoint_id ? `&checkpoint_id=${this.props.checkpoint_id}` : ''}` : ''}`
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
            const res = await axios.get<Blob>(url, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
                responseType: 'blob',
            })

            const cd = String((res.headers as any)?.['content-disposition'] ?? '')
            const match = /filename\*?=(?:UTF-8''|")?([^\";]+)\"?/i.exec(cd)
            const headerName = match ? decodeURIComponent(match[1]) : ''
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

    componentDidMount() {
        const submissionsRequest = axios.post(
            import.meta.env.VITE_API_URL + `/submissions/recentsubproject`,
            {
                project_id: this.props.project_id,
                checkpoint: this.props.isCheckpoint,
                checkpoint_id: this.props.checkpoint_id ?? null,
            },
            {
                headers: {
                    Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                },
            }
        );

        const ohVisitsRequest = axios.post(
            import.meta.env.VITE_API_URL + `/submissions/get_oh_visits_by_projectId`,
            { project_id: this.props.project_id, checkpoint: this.props.isCheckpoint, checkpoint_id: this.props.checkpoint_id ?? null },
            {
                headers: {
                    Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                },
            }
        ).catch(() => ({ data: [] }));

        const projectInfoRequest = axios.get(
            import.meta.env.VITE_API_URL +
            `/projects/get_project_id?id=${this.props.project_id}` +
            `${this.props.isCheckpoint && this.props.checkpoint_id ? `&checkpoint_id=${this.props.checkpoint_id}` : ''}`,
            {
                headers: {
                    Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                },
            }
        );

        Promise.all([submissionsRequest, ohVisitsRequest, projectInfoRequest])
            .then(([submissionsRes, officeHoursRes, projectInfoRes]) => {
                const data = submissionsRes.data

                let projectName = ''
                try {
                    const parsed =
                        typeof projectInfoRes.data === 'string'
                            ? JSON.parse(projectInfoRes.data || '{}')
                            : projectInfoRes.data || {}

                    const firstEntry = Object.values(parsed as Record<string, any>)[0]

                    if (Array.isArray(firstEntry)) {
                        projectName = String(firstEntry[0] ?? firstEntry[1] ?? '').trim()
                    } else {
                        projectName = String((parsed as any)?.Name ?? '').trim()
                    }
                } catch (_e) {
                    projectName = ''
                }

                const officeHoursData = Array.isArray(officeHoursRes.data) ? officeHoursRes.data : []
                const officeHoursAttendees = new Set(officeHoursData.map((value: any) => Number(value)));
                const rows: Array<Row> = []
                const lectureSet = new Set<number>([-1])
                const labSet = new Set<number>([-1])

                Object.entries(data).map(([key, value]) => {
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

                    row.numberOfSubmissions = parseInt(String(student_output_data[4] ?? '0'), 10)
                    row.date = String(student_output_data[5] ?? '')

                    const passRaw = String(student_output_data[6] ?? '').toLowerCase().trim()
                    row.isPassing =
                        passRaw === 'true' ||
                        passRaw === '1' ||
                        passRaw === 'pass' ||
                        passRaw === 'passed' ||
                        passRaw === 'ok' ||
                        passRaw === 'success'

                    const hasSub = String(student_output_data[7] ?? 'N/A') !== 'N/A'
                    const off = hasSub ? 0 : 1
                    row.subid = parseInt(String(student_output_data[7 + off] ?? '-1'), 10)
                    row.classId = String(student_output_data[8 + off] ?? '')
                    row.grade = parseInt(String(student_output_data[9 + off] ?? '0'), 10)
                    row.StudentNumber = parseInt(String(student_output_data[10 + off] ?? '0'), 10)
                    const lockRaw = String(student_output_data[11 + off] ?? '').toLowerCase().trim()
                    row.IsLocked = lockRaw === 'true' || lockRaw === '1' || lockRaw === 'locked'

                    row.attendedOfficeHours = officeHoursAttendees.has(row.id);

                    rows.push(row)
                    return row
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

                this.setState({ rows, lecture_numbers, lab_numbers, projectName })
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
            return { ...row, hidden: !(lectureOk && labOk) }
        })
        this.setState({ rows: new_rows })
    }

    handleGradeChange = (e: React.ChangeEvent<HTMLInputElement>, row: Row) => {
        const newValue = parseFloat(e.target.value)
        if (!isNaN(newValue)) {
            const updatedRows = this.state.rows.map((r) => (r.id === row.id ? { ...r, grade: newValue } : r))
            this.setState({ rows: updatedRows })
        }
    }

    handleUnlockClick = (UserId: number) => {
        axios
            .post(
                import.meta.env.VITE_API_URL + `/projects/unlockStudentAccount`,
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

    submitGrades(UserId: number, grade: string) {
        const intGrade = Number.isFinite(Number(grade)) ? parseInt(grade, 10) : 0
        this.setState({ isLoading: true })

        axios
            .post(
                import.meta.env.VITE_API_URL + `/submissions/submitgrades`,
                { userId: UserId, grade: intGrade, projectID: this.props.project_id, checkpoint: this.props.isCheckpoint, checkpoint_id: this.props.checkpoint_id ?? null },
                {
                    headers: {
                        Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                    },
                }
            )
            .then((_res) => {
                this.setState((prev) => ({
                    rows: prev.rows.map((r) => (r.id === UserId ? { ...r, grade: intGrade } : r)),
                    modalIsOpen: false,
                    modalIsLoading: false,
                    isLoading: false,
                    selectedStudent: -1,
                    selectedStudentName: '',
                    selectedStudentGrade: 0,
                    activeView: 'table',
                    selectedDiffId: null,
                }))
            })
            .catch((_exc) => {
                this.setState({ isLoading: false })
                window.alert('Failed to submit grade. Please try again.')
            })
    }

    exportGrades() {
        axios
            .get(import.meta.env.VITE_API_URL + `/submissions/getprojectscores?projectID=${this.props.project_id}${this.props.isCheckpoint ? `&checkpoint=true${this.props.checkpoint_id ? `&checkpoint_id=${this.props.checkpoint_id}` : ''}` : ''}`, {
                headers: {
                    Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                },
            })
            .then((res) => {
                const projectname = res.data.projectName
                const csvContent: String[][] = []
                let selectedRow: any

                csvContent.push(['OrgDefinedId', projectname + ' Points Grade', 'End-of-Line Indicator'])

                for (const value of res.data.studentData) {
                    selectedRow = this.state.rows.find((row) => row.id === value[2])
                    if (this.state.selectedLecture === -1) {
                        csvContent.push([value[0].toString(), value[1].toString(), '#'])
                    } else {
                        if (selectedRow && selectedRow.lecture_number === this.state.selectedLecture) {
                            csvContent.push([value[0].toString(), value[1].toString(), '#'])
                        }
                    }
                }

                const csvRows = csvContent.map((row) => row.join(','))
                const csvString = csvRows.join('\n')
                const blob = new Blob([csvString], { type: 'text/csv' })
                const url = URL.createObjectURL(blob)

                const a = document.createElement('a')
                a.href = url
                a.download = `${projectname}.csv`
                document.body.appendChild(a)
                a.click()

                document.body.removeChild(a)
                URL.revokeObjectURL(url)
                this.setState({ exportModalIsOpen: false })
            })
            .catch((_exc) => { })
    }

    openGradingModule(UserId: number) {
        this.setState({ modalIsLoading: true, activeView: 'table', selectedDiffId: null })

        if (UserId === -1) {
            const first = this.state.rows[0]
            if (!first) {
                this.setState({ modalIsOpen: false, modalIsLoading: false })
                return
            }
            UserId = first.id
            this.setState({
                selectedStudentName: first.Fname + ' ' + first.Lname,
                selectedStudent: UserId,
                selectedStudentGrade: first.grade,
            })
        } else {
            const selectedRow = this.state.rows.find((row) => row.id === UserId)
            if (selectedRow === undefined) {
                this.setState({ modalIsOpen: false, modalIsLoading: false })
                return
            }
            this.setState({
                selectedStudentName: selectedRow.Fname + ' ' + selectedRow.Lname,
                selectedStudent: UserId,
                selectedStudentGrade: selectedRow.grade,
            })
        }

        axios
            .post(
                import.meta.env.VITE_API_URL + `/projects/ProjectGrading`,
                {
                    userID: UserId,
                    ProjectId: this.props.project_id,
                    checkpoint: this.props.isCheckpoint,
                    checkpoint_id: this.props.checkpoint_id ?? null,
                },
                {
                    headers: {
                        Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                    },
                }
            )
            .then((res) => {
                this.setState({
                    selectedStudentData: res.data.GradingData,
                    modalIsLoading: false,
                    modalIsOpen: true,
                    selectedStudentCode: res.data.Code,
                    selectedStudentTestResults: res.data.TestResults,
                    projectLanguage: res.data.Language,
                })
            })
            .catch((_exc) => {
                this.setState({ modalIsLoading: false })
            })
    }

    render() {
        const rowsForView = (() => {
            const visible = this.state.rows.filter((r) => !r.hidden)
            if (this.state.sortBy === 'lastsubmitted') {
                const timeVal = (r: Row) => {
                    const t = Date.parse(r.date)
                    return isNaN(t) ? -Infinity : t
                }
                return [...visible].sort((a, b) => timeVal(b) - timeVal(a))
            }
            return [...visible].sort((a, b) => a.Lname.localeCompare(b.Lname) || a.Fname.localeCompare(b.Fname))
        })()

        const totalStudents = rowsForView.length
        const submittedStudents = rowsForView.filter((row) => row.subid !== -1).length
        const passingStudents = rowsForView.filter((row) => row.subid !== -1 && row.isPassing).length

        const submittedPercent = totalStudents > 0 ? Math.round((submittedStudents / totalStudents) * 100) : 0
        const passingPercent = totalStudents > 0 ? Math.round((passingStudents / totalStudents) * 100) : 0

        const moduleListUrl = `/admin/school/${this.props.school_id}/class/${this.props.class_id}/modules`
        const moduleOverviewUrl = `/admin/school/${this.props.school_id}/class/${this.props.class_id}/module/${this.props.module_id}/overview`
        const projectBaseUrl = this.getProjectBaseUrl()
        const checkpointQuery = this.getCheckpointQuery()

        return (
            <div>
                {this.state.plagiarismOpen && (
                    <PlagiarismModal
                        projectId={this.props.project_id}
                        classId={Number(this.props.class_id)}
                        checkpoint={this.props.isCheckpoint}
                        checkpointId={this.props.checkpoint_id}
                        studentIds={rowsForView.map(row => row.id)}
                        onClose={() => this.setState({ plagiarismOpen: false })}
                    />
                )}
                <Helmet>
                    <title>[Admin] MAAT</title>
                </Helmet>

                <MenuComponent
                    showUpload={false}
                    showAdminUpload={true}
                    showHelp={false}
                    showCreate={false}
                    showLast={false}
                    showReviewButton={false}
                />

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
                        <div className={`admin-project-config-container${this.state.modalIsOpen ? ' blurred' : ''}`}>

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
                                            disabled={this.state.isLoading}
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
                                            disabled={this.state.isLoading}
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
                                                const renderStudentName = () => (
                                                    <td className="student-name-cell">
                                                        {row.Fname + ' ' + row.Lname}{' '}

                                                        {row.attendedOfficeHours && (
                                                            <span
                                                                className="office-hours-indicator"
                                                                data-tooltip="Attended Office Hours for this project"
                                                            >
                                                                <FaHandPaper aria-hidden="true" />
                                                            </span>
                                                        )}

                                                        {row.IsLocked === true && (
                                                            <button className="btn unlock-btn" onClick={() => this.handleUnlockClick(row.id)}>
                                                                Unlock
                                                            </button>
                                                        )}
                                                    </td>
                                                );
                                                if (row.subid === -1) {
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
                                                                    onChange={(e) => this.handleGradeChange(e, row)}
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
                                                                to={`${projectBaseUrl}/codeview/${row.subid}${checkpointQuery}`}
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
                                                                onChange={(e) => this.handleGradeChange(e, row)}
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
