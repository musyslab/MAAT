// AdminUpload.tsx: Renders the admin upload interface and coordinates its local data and interactions.
import React, { Component } from 'react'
import axios from 'axios'
import { useNavigate, NavigateFunction, useParams } from 'react-router-dom'
import MenuComponent from '../components/MenuComponent'
import { Helmet } from 'react-helmet'
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs"
import { FaAlignJustify, FaCloudUploadAlt, FaCode, FaExchangeAlt, FaRegFile, FaTimes } from 'react-icons/fa'
import LoadingAnimation from '../components/LoadingAnimation'
import '../../styling/AdminUploadPage.scss'
import '../../styling/FileUploadCommon.scss'

// Describes the student data expected by this file.
interface Student {
    name: string
    mscsnet: string
    id: number
}

// Describes the drop down option data expected by this file.
interface DropDownOption {
    key: number
    value: number
    text: string
}

// Describes the module object data expected by this file.
interface ModuleObject {
    Id: number
    ClassId: number
    Name: string
    Start: string
    End: string
    MainProjectId: number | null
    MainProjectName: string
    TotalSubmissions: number
    CheckpointTotalSubmissions: number
    CheckpointsEnabled?: boolean
    MainCompleted?: boolean
}

// Describes the checkpoint option data expected by this file.
interface CheckpointOption {
    id: number
    number: number
    name: string
    enabled: boolean
    completed: boolean
    available: boolean
}

// Describes the upload targets response data expected by this file.
interface UploadTargetsResponse {
    checkpoints?: CheckpointOption[]
    mainAvailable?: boolean
}

// Describes the class access response data expected by this file.
interface ClassAccessResponse {
    id?: number
    name?: string
    school_id?: number
    school_name?: string
}

// Describes the upload page state data expected by this file.
interface UploadPageState {
    files: File[]
    mainJavaFileName: string
    isLoading: boolean
    isUploading: boolean
    error_message: string
    isErrorMessageHidden: boolean
    school_id: number
    class_id: number
    className: string
    module_id: number
    project_id: number
    student_id: number
    studentList: Array<DropDownOption>
    modules: Array<DropDownOption>
    moduleMainProjectById: Record<number, number>
    moduleCheckpointsEnabledById: Record<number, boolean>
    checkpointsByProjectId: Record<number, CheckpointOption[]>
    selectedCheckpointId: number
    mainTargetAvailable: boolean
    targetAvailabilityLoaded: boolean
    isTargetLoading: boolean
}

// Describes the admin upload page props data expected by this file.
interface AdminUploadPageProps {
    navigate: NavigateFunction
    schoolIdFromUrl: string
    classIdFromUrl: string
}

// Renders the admin upload page wrapper interface and coordinates its local data and interactions.
const AdminUploadPageWrapper: React.FC = () => {
    const navigate = useNavigate()
    // Reads the school, class, or assignment identifiers from the current route.
    const { school_id, class_id } = useParams<{
        school_id: string
        class_id: string
    }>()

    // Renders the interface using the current data and interaction state.
    return (
        <AdminUploadPage
            navigate={navigate}
            schoolIdFromUrl={school_id || ""}
            classIdFromUrl={class_id || ""}
        />
    )
}

class AdminUpload extends Component<{}, {}> {
    render() {
        // Renders the interface using the current data and interaction state.
        return (
            <div className="admin-upload-root">
                {/* Sets the page title and document metadata. */}
                <Helmet>
                    <title>[Admin] MAAT</title>
                </Helmet>

                {/* Displays the navigation and actions available on this page. */}
                <MenuComponent
                />

                <div className="main-grid admin-upload-grid">
                    <div className="admin-upload-center">
                        <AdminUploadPageWrapper />
                    </div>
                </div>
            </div>
        )
    }
}

class AdminUploadPage extends Component<AdminUploadPageProps, UploadPageState> {
    private static readonly JAVA_MAIN_RE = /\bpublic\s+static\s+void\s+main\s*\(/
    private static isJavaFileName = (n: string) => /\.java$/i.test(n)

    private static pickMainJavaFile(allJavaNames: string[], namesWithMain: string[]): string {
        if (namesWithMain.length === 1) return namesWithMain[0]
        const mainDotJava = allJavaNames.find((n) => n.toLowerCase() === 'main.java')
        if (mainDotJava) return mainDotJava
        return namesWithMain[0] || ''
    }

    private async computeMainJavaFromLocal(localFiles: File[]): Promise<string> {
        const javaFiles = localFiles.filter((f) => AdminUploadPage.isJavaFileName(f.name))
        if (javaFiles.length <= 1) return ''

        const withMain: string[] = []

        for (const f of javaFiles) {
            try {
                const txt = await f.text()
                if (AdminUploadPage.JAVA_MAIN_RE.test(txt)) withMain.push(f.name)
            } catch {
                // ignore read failures
            }
        }

        return AdminUploadPage.pickMainJavaFile(
            javaFiles.map((f) => f.name),
            withMain
        )
    }

    constructor(props: AdminUploadPageProps) {
        super(props)
        this.state = {
            isLoading: false,
            isUploading: false,
            error_message: '',
            isErrorMessageHidden: true,
            school_id: 0,
            class_id: 0,
            className: '',
            module_id: 0,
            project_id: 0,
            student_id: 0,
            studentList: [],
            modules: [],
            moduleMainProjectById: {},
            moduleCheckpointsEnabledById: {},
            checkpointsByProjectId: {},
            selectedCheckpointId: 0,
            mainTargetAvailable: false,
            targetAvailabilityLoaded: false,
            isTargetLoading: false,
            files: [],
            mainJavaFileName: '',
        }

        this.handleSubmit = this.handleSubmit.bind(this)
        this.handleStudentIdChange = this.handleStudentIdChange.bind(this)
        this.handleModuleIdChange = this.handleModuleIdChange.bind(this)
        this.handleFilesChange = this.handleFilesChange.bind(this)
    }

    private authHeaders() {
        return {
            Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
        }
    }

    private clearError() {
        this.setState({
            error_message: '',
            isErrorMessageHidden: true,
        })
    }

    private setError(message: string) {
        this.setState({
            error_message: message,
            isErrorMessageHidden: false,
        })
    }

    private resetForClassContext(nextSchoolId: number, nextClassId: number) {
        this.setState({
            school_id: nextSchoolId,
            class_id: nextClassId,
            className: '',
            module_id: 0,
            project_id: 0,
            student_id: 0,
            studentList: [],
            modules: [],
            moduleMainProjectById: {},
            moduleCheckpointsEnabledById: {},
            checkpointsByProjectId: {},
            selectedCheckpointId: 0,
            mainTargetAvailable: false,
            targetAvailabilityLoaded: false,
            isTargetLoading: false,
            files: [],
            mainJavaFileName: '',
            isUploading: false,
        })
    }

    private resetFromStudentDown(nextStudentId: number) {
        this.setState({
            student_id: nextStudentId,
            module_id: 0,
            project_id: 0,
            selectedCheckpointId: 0,
            mainTargetAvailable: false,
            targetAvailabilityLoaded: false,
            isTargetLoading: false,
            files: [],
            mainJavaFileName: '',
            isUploading: false,
        })
    }

    private async loadClassNameForClass(schoolId: number, classId: number) {
        if (!(schoolId > 0) || !(classId > 0)) return

        try {
            // Fetches the server data needed for this operation.
            const res = await axios.get<ClassAccessResponse>(
                import.meta.env.VITE_API_URL +
                    `/classes/validate_class_access/${classId}?school_id=${schoolId}&role_context=admin`,
                { headers: this.authHeaders() }
            )

            this.setState({
                className: res.data?.name || '',
            })
        } catch (err) {
            console.error(err)
            this.setState({
                className: '',
            })
        }
    }

    private async loadStudentsForClass(classId: number) {
        if (!(classId > 0)) return

        try {
            // Fetches the server data needed for this operation.
            const res = await axios.get(
                import.meta.env.VITE_API_URL + `/upload/total_students?class_id=${classId}`,
                { headers: this.authHeaders() }
            )

            const students = res.data as Array<Student>

            // Helper for last name of used by this component.
            const lastNameOf = (full: string) => {
                const n = (full || '').trim()
                if (!n) return ''
                if (n.includes(',')) return n.split(',')[0]!.trim()
                const parts = n.split(/\s+/)
                return parts[parts.length - 1]!
            }

            // Checks test student for this view.
            const isTestStudent = (name: string) => (name || '').trim().toLowerCase() === 'test user'

            const sorted = [...students].sort((a, b) => {
                const aTest = isTestStudent(a.name)
                const bTest = isTestStudent(b.name)

                if (aTest && !bTest) return -1
                if (!aTest && bTest) return 1

                const lnCmp = lastNameOf(a.name).localeCompare(lastNameOf(b.name), undefined, { sensitivity: 'base' })
                if (lnCmp !== 0) return lnCmp

                const nameCmp = (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' })
                if (nameCmp !== 0) return nameCmp

                return a.id - b.id
            })

            const studentsDropdown: Array<DropDownOption> = sorted.map((s) => ({
                key: s.id,
                text: `${s.name} (${s.mscsnet})`,
                value: s.id,
            }))

            this.setState({ studentList: studentsDropdown })
        } catch (err: any) {
            this.setError(err.response?.data?.message ?? 'Error loading students')
        }
    }

    private async loadModulesForClass(classId: number) {
        if (!(classId > 0)) return

        try {
            // Fetches the server data needed for this operation.
            const res = await axios.get(
                import.meta.env.VITE_API_URL + `/assignment_tracking/get_modules_by_class_id?id=${classId}`,
                { headers: this.authHeaders() }
            )

            const modules = res.data as Array<ModuleObject>

            const moduleDropdown: Array<DropDownOption> = modules
                .filter((m) => Number(m.MainProjectId ?? 0) > 0)
                .map((m) => ({
                    key: Number(m.Id),
                    text: String(m.Name),
                    value: Number(m.Id),
                }))
                .sort((a, b) => a.text.localeCompare(b.text))

            const mainProjectMap: Record<number, number> = {}
            const checkpointsEnabledMap: Record<number, boolean> = {}

            for (const moduleObj of modules) {
                const moduleId = Number(moduleObj.Id)
                const mainProjectId = Number(moduleObj.MainProjectId ?? 0)

                if (moduleId > 0 && mainProjectId > 0) {
                    mainProjectMap[moduleId] = mainProjectId
                    checkpointsEnabledMap[moduleId] = moduleObj.CheckpointsEnabled !== false
                }
            }

            this.setState({
                modules: moduleDropdown,
                moduleMainProjectById: mainProjectMap,
                moduleCheckpointsEnabledById: checkpointsEnabledMap,
            })
        } catch (err: any) {
            this.setError(err.response?.data?.message ?? 'Error loading modules')
        }
    }

    private async loadAvailableTargets(projectId: number, studentId: number) {
        if (!(projectId > 0) || !(studentId !== 0)) return

        this.setState({
            isTargetLoading: true,
            targetAvailabilityLoaded: false,
            mainTargetAvailable: false,
        })

        try {
            // Fetches the server data needed for this operation.
            const res = await axios.get<UploadTargetsResponse>(
                import.meta.env.VITE_API_URL + `/upload/available_targets`,
                {
                    headers: this.authHeaders(),
                    params: {
                        class_id: this.state.class_id,
                        project_id: projectId,
                        student_id: studentId,
                    },
                }
            )

            if (
                this.state.project_id !== projectId ||
                this.state.student_id !== studentId
            ) {
                return
            }

            const problems = Array.isArray(res.data?.checkpoints) ? res.data.checkpoints : []
            const rows: CheckpointOption[] = problems.map((checkpoint: any, idx: number) => ({
                id: Number(checkpoint?.id),
                number: Number(checkpoint?.number ?? idx + 1),
                name: String(checkpoint?.name ?? `Checkpoint ${idx + 1}`),
                enabled: checkpoint?.enabled !== false,
                completed: checkpoint?.completed === true,
                available: checkpoint?.available === true,
            }))

            const nextIncomplete = rows.find(
                (checkpoint) => checkpoint.available && !checkpoint.completed
            )
            const mainAvailable = res.data?.mainAvailable === true

            this.setState((prev) => ({
                checkpointsByProjectId: {
                    ...prev.checkpointsByProjectId,
                    [projectId]: rows,
                },
                selectedCheckpointId: nextIncomplete?.id ?? 0,
                mainTargetAvailable: mainAvailable,
                targetAvailabilityLoaded: true,
                isTargetLoading: false,
            }))
        } catch (err: any) {
            if (
                this.state.project_id !== projectId ||
                this.state.student_id !== studentId
            ) {
                return
            }

            this.setState((prev) => ({
                checkpointsByProjectId: {
                    ...prev.checkpointsByProjectId,
                    [projectId]: [],
                },
                selectedCheckpointId: 0,
                mainTargetAvailable: false,
                targetAvailabilityLoaded: false,
                isTargetLoading: false,
            }))
            this.setError(err.response?.data?.message ?? 'Error loading available assignments')
        }
    }

    private loadClassContextFromUrl() {
        const schoolId = Number(this.props.schoolIdFromUrl)
        const classId = Number(this.props.classIdFromUrl)

        if (!(schoolId > 0) || Number.isNaN(schoolId) || !(classId > 0) || Number.isNaN(classId)) {
            this.props.navigate("/schools", { replace: true })
            return
        }

        this.resetForClassContext(schoolId, classId)
        this.clearError()
        this.setState({ isLoading: true })

        Promise.all([
            this.loadClassNameForClass(schoolId, classId),
            this.loadStudentsForClass(classId),
            this.loadModulesForClass(classId),
        ]).finally(() => {
            this.setState({ isLoading: false })
        })
    }

    componentDidMount() {
        this.loadClassContextFromUrl()
    }

    componentDidUpdate(prevProps: AdminUploadPageProps) {
        if (
            prevProps.schoolIdFromUrl === this.props.schoolIdFromUrl &&
            prevProps.classIdFromUrl === this.props.classIdFromUrl
        ) {
            return
        }

        this.loadClassContextFromUrl()
    }

    handleStudentIdChange(e: React.ChangeEvent<HTMLSelectElement>) {
        const value = parseInt(e.target.value, 10)
        this.resetFromStudentDown(Number.isNaN(value) ? 0 : value)
    }

    handleModuleIdChange(e: React.ChangeEvent<HTMLSelectElement>) {
        const value = parseInt(e.target.value, 10)
        const nextModuleId = Number.isNaN(value) ? 0 : value
        const nextProjectId = this.state.moduleMainProjectById[nextModuleId] ?? 0

        this.setState(
            {
                module_id: nextModuleId,
                project_id: nextProjectId,
                selectedCheckpointId: 0,
                mainTargetAvailable: false,
                targetAvailabilityLoaded: false,
                files: [],
                mainJavaFileName: '',
            },
            () => {
                if (nextProjectId > 0 && this.state.student_id !== 0) {
                    this.loadAvailableTargets(nextProjectId, this.state.student_id)
                }
            }
        )
    }

    private getFileIcon(filename: string): React.ReactElement {
        const CODE_ICON_RE = /\.(java|py|c|h|rkt|scm)$/i
        const TEXT_ICON_RE = /\.(txt|doc|docx|pdf)$/i

        if (CODE_ICON_RE.test(filename)) return <FaCode className="file-language-icon" aria-hidden="true" />
        if (TEXT_ICON_RE.test(filename)) return <FaAlignJustify className="file-language-icon" aria-hidden="true" />
        // Renders the interface using the current data and interaction state.
        return <FaTimes className="file-language-icon" aria-hidden="true" />
    }

    handleFilesChange(e: React.ChangeEvent<HTMLInputElement>) {
        const files = e.target.files
        const fileArr = files ? Array.from(files) : []

        // Checks java file for this view.
        const isJavaFile = (f: File) => f.name.toLowerCase().endsWith('.java')

        if (fileArr.length > 1 && fileArr.every((f) => !isJavaFile(f))) {
            this.setState({
                files: [],
                mainJavaFileName: '',
                isErrorMessageHidden: false,
                error_message: 'Multi-file upload is only available for Java (.java) files.',
            })
            return
        }

        this.setState({
            files: fileArr,
            mainJavaFileName: '',
            isErrorMessageHidden: true,
        })

        if (fileArr.length > 1 && fileArr.every(isJavaFile)) {
            this.computeMainJavaFromLocal(fileArr)
                .then((main) => this.setState({ mainJavaFileName: main }))
                .catch(() => this.setState({ mainJavaFileName: '' }))
        }
    }

    handleSubmit(e: React.FormEvent<HTMLFormElement>) {
        e.preventDefault()

        const uploadDisabled =
            this.state.isLoading ||
            !(this.state.school_id > 0) ||
            !(this.state.class_id > 0) ||
            !(this.state.student_id !== 0) ||
            !(this.state.module_id > 0) ||
            !(this.state.project_id > 0) ||
            !this.state.targetAvailabilityLoaded ||
            this.state.isTargetLoading

        if (uploadDisabled) {
            this.setState({
                isErrorMessageHidden: false,
                error_message: 'Please select a student and an available assignment before uploading.',
            })
            return
        }

        const selectedCheckpoint = this.state.selectedCheckpointId > 0
            ? (this.state.checkpointsByProjectId[this.state.project_id] ?? []).find(
                (checkpoint) => checkpoint.id === this.state.selectedCheckpointId
            )
            : null
        const selectedTargetAvailable = selectedCheckpoint
            ? selectedCheckpoint.available
            : this.state.mainTargetAvailable

        if (!selectedTargetAvailable) {
            this.setState({
                isErrorMessageHidden: false,
                error_message: 'That assignment is locked. Complete or skip the earlier checkpoints first.',
            })
            return
        }

        // Checks java file for this view.
        const isJavaFile = (f: File) => f.name.toLowerCase().endsWith('.java')

        if (this.state.files.length > 1 && this.state.files.every((f) => !isJavaFile(f))) {
            this.setState({
                files: [],
                isErrorMessageHidden: false,
                error_message: 'Multi-file upload is only available for Java (.java) files.',
            })
            return
        }

        if (this.state.files.length === 0) {
            this.setState({
                isErrorMessageHidden: false,
                error_message: 'Please choose one or more files to upload.',
            })
            return
        }

        this.setState({ isErrorMessageHidden: true, isLoading: true, isUploading: true })

        const formData = new FormData()
        this.state.files.forEach((f) => formData.append('files', f, f.name))
        formData.append('student_id', String(this.state.student_id))
        formData.append('project_id', String(this.state.project_id))
        formData.append('class_id', String(this.state.class_id))

        const isCheckpoint = this.state.selectedCheckpointId > 0
        formData.append('checkpoint', isCheckpoint ? '1' : '0')

        if (isCheckpoint) {
            formData.append('checkpoint_id', String(this.state.selectedCheckpointId))
        }

        axios
            .post(import.meta.env.VITE_API_URL + `/upload/file_upload`, formData, {
                headers: this.authHeaders(),
            })
            .then((res) => {
                const codeViewBase = isCheckpoint
                    ? `/admin/school/${this.state.school_id.toString()}` +
                    `/class/${this.state.class_id.toString()}` +
                    `/module/${this.state.module_id.toString()}` +
                    `/project/${this.state.project_id.toString()}` +
                    `/checkpoint/${this.state.selectedCheckpointId.toString()}` +
                    `/codeview/${res.data.sid.toString()}`
                    : `/admin/school/${this.state.school_id.toString()}` +
                    `/class/${this.state.class_id.toString()}` +
                    `/module/${this.state.module_id.toString()}` +
                    `/project/${this.state.project_id.toString()}` +
                    `/codeview/${res.data.sid.toString()}`

                window.location.href = `${codeViewBase}?from=admin-upload`
            })
            .catch((err) => {
                this.setState({
                    error_message: err.response?.data?.message ?? 'Upload failed',
                    isErrorMessageHidden: false,
                    isLoading: false,
                    isUploading: false,
                })
            })
    }

    render() {
        const studentChosen = this.state.student_id !== 0
        const moduleChosen = this.state.module_id > 0
        const projectChosen = this.state.project_id > 0

        const disableStudent = this.state.isLoading || this.state.studentList.length === 0
        const disableModule = !studentChosen || this.state.isLoading || this.state.modules.length === 0
        const checkpointRows = projectChosen
            ? this.state.checkpointsByProjectId[this.state.project_id] ?? []
            : []

        const selectedCheckpoint = this.state.selectedCheckpointId > 0
            ? checkpointRows.find(
                (checkpoint) => checkpoint.id === this.state.selectedCheckpointId
            )
            : null
        const selectedTargetAvailable = selectedCheckpoint
            ? selectedCheckpoint.available
            : this.state.mainTargetAvailable

        const disableUpload =
            !moduleChosen ||
            !projectChosen ||
            !studentChosen ||
            this.state.isLoading ||
            this.state.isTargetLoading ||
            !this.state.targetAvailabilityLoaded ||
            !selectedTargetAvailable

        const checkpointsEnabledForModule =
            moduleChosen && this.state.moduleCheckpointsEnabledById[this.state.module_id] !== false

        const enabledCheckpointRows = checkpointsEnabledForModule
            ? checkpointRows.filter((checkpoint) => !!checkpoint.enabled)
            : []

        // Renders the interface using the current data and interaction state.
        return (
            <>
                {/* Shows progress while this view is waiting for data. */}
                <LoadingAnimation show={this.state.isUploading} message="Uploading..." />

                {/* Shows the current location and links back to parent pages. */}
                <DirectoryBreadcrumbs
                    items={[
                        { label: 'School Selection', to: '/schools' },
                        {
                            label: 'Class Selection',
                            to: `/admin/school/${this.state.school_id || this.props.schoolIdFromUrl}/classes`,
                        },
                        {
                            label: 'Admin Menu',
                            to: `/admin/school/${this.state.school_id || this.props.schoolIdFromUrl}/class/${this.state.class_id || this.props.classIdFromUrl}/menu`,
                        },
                        { label: 'Admin Upload' },
                    ]}
                />

                <div className="pageTitle">
                    {this.state.className ? `${this.state.className} Admin Upload` : 'Admin Upload'}
                </div>

                <div className="admin-upload-stack">
                    <div className="admin-upload-page">
                        <div className="selection-section">
                            <p className="section-label">Please select a student</p>
                            <select
                                className="select student-select"
                                value={this.state.student_id || ''}
                                onChange={this.handleStudentIdChange}
                                disabled={disableStudent}
                            >
                                <option value="" disabled>
                                    Select student
                                </option>
                                {this.state.studentList.map((opt) => (
                                    <option key={opt.key} value={opt.value}>
                                        {opt.text}
                                    </option>
                                ))}
                            </select>

                            <div className="spacer" aria-hidden="true">
                                &nbsp;
                            </div>

                            <p className="section-label">Please select a module</p>
                            <select
                                className="select module-select"
                                value={this.state.module_id || ''}
                                onChange={this.handleModuleIdChange}
                                disabled={disableModule}
                            >
                                <option value="" disabled>
                                    Select module
                                </option>
                                {this.state.modules.map((opt) => (
                                    <option key={opt.key} value={opt.value}>
                                        {opt.text}
                                    </option>
                                ))}
                            </select>

                            {moduleChosen && (
                                <>
                                    <div className="spacer" aria-hidden="true">
                                        &nbsp;
                                    </div>

                                    <p className="section-label">Submit to</p>
                                    <select
                                        className="select checkpoint-target-select"
                                        value={
                                            this.state.selectedCheckpointId > 0
                                                ? `checkpoint:${this.state.selectedCheckpointId}`
                                                : 'main'
                                        }
                                        onChange={(e) => {
                                            const v = e.target.value || 'main'

                                            if (v === 'main') {
                                                this.setState({
                                                    selectedCheckpointId: 0,
                                                    files: [],
                                                    mainJavaFileName: '',
                                                })
                                                return
                                            }

                                            if (v.startsWith('checkpoint:')) {
                                                const idStr = v.split(':', 2)[1] || ''
                                                const checkpointId = parseInt(idStr, 10)

                                                this.setState({
                                                    selectedCheckpointId: Number.isNaN(checkpointId) ? 0 : checkpointId,
                                                    files: [],
                                                    mainJavaFileName: '',
                                                })
                                            }
                                        }}
                                        disabled={
                                            this.state.isLoading ||
                                            this.state.isTargetLoading ||
                                            !this.state.targetAvailabilityLoaded
                                        }
                                    >
                                        <option value="main" disabled={!this.state.mainTargetAvailable}>
                                            {this.state.mainTargetAvailable
                                                ? 'Main Problem'
                                                : 'Main Problem (locked)'}
                                        </option>
                                        {enabledCheckpointRows.map((checkpoint) => (
                                            <option
                                                key={checkpoint.id}
                                                value={`checkpoint:${checkpoint.id}`}
                                                disabled={!checkpoint.available}
                                            >
                                                {`Checkpoint ${checkpoint.number}: ${checkpoint.name}${
                                                    checkpoint.completed
                                                        ? ' (completed)'
                                                        : checkpoint.available
                                                            ? ''
                                                            : ' (locked)'
                                                }`}
                                            </option>
                                        ))}
                                    </select>
                                </>
                            )}

                            <div className="spacer" aria-hidden="true">
                                &nbsp;
                            </div>

                            <form className="upload-form" onSubmit={this.handleSubmit}>
                                <h1 className="upload-title">Upload Assignment</h1>

                                <div className="file-section">
                                    <div className="info-segment">
                                        <div
                                            className={`file-drop-area${disableUpload ? ' is-disabled' : ''}`}
                                            aria-disabled={disableUpload}
                                            onDragOver={(e) => {
                                                e.preventDefault()
                                            }}
                                            onDrop={(e) => {
                                                e.preventDefault()

                                                if (disableUpload) return

                                                const files = e.dataTransfer.files
                                                if (files && files.length > 0) {
                                                    this.handleFilesChange({ target: { files } } as any)
                                                }
                                            }}
                                        >
                                            {this.state.files.length === 0 ? (
                                                <>
                                                    <input
                                                        type="file"
                                                        className="file-input"
                                                        required
                                                        multiple
                                                        onChange={this.handleFilesChange}
                                                        disabled={disableUpload}
                                                    />
                                                    <div className="file-drop-content">
                                                        <FaCloudUploadAlt className="file-drop-cloud-icon" aria-hidden="true" />
                                                        <div className="file-drop-message">
                                                            Drag &amp; drop your file here or{' '}
                                                            <span className="browse-text">browse</span>
                                                        </div>
                                                    </div>
                                                </>
                                            ) : (
                                                <div className={`file-preview${disableUpload ? ' is-disabled' : ''}`}>
                                                    <button
                                                        type="button"
                                                        className="exchange-icon"
                                                        onClick={() => this.setState({ files: [], mainJavaFileName: '' })}
                                                        aria-label="Clear selected files"
                                                        title="Clear selected files"
                                                        disabled={disableUpload}
                                                    >
                                                        <FaExchangeAlt aria-hidden="true" />
                                                    </button>

                                                    <div className="file-preview-list" title="Selected files">
                                                        {(() => {
                                                            // Checks java file for this view.
                                                            const isJavaFile = (f: File) => f.name.toLowerCase().endsWith('.java')
                                                            const showMainTag =
                                                                this.state.files.length > 1 &&
                                                                this.state.files.every(isJavaFile) &&
                                                                !!this.state.mainJavaFileName

                                                            // Renders the interface using the current data and interaction state.
                                                            return this.state.files.map((f) => (
                                                                <div key={f.name} className="file-preview-row solution-file-card">
                                                                    <span className="file-icon-wrapper" aria-hidden="true">
                                                                        <FaRegFile className="file-outline-icon" aria-hidden="true" />
                                                                        {this.getFileIcon(f.name)}
                                                                    </span>
                                                                    <span className="file-name">
                                                                        {f.name}
                                                                        {showMainTag && f.name === this.state.mainJavaFileName && (
                                                                            <span className="main-indicator">Main</span>
                                                                        )}
                                                                    </span>
                                                                </div>
                                                            ))
                                                        })()}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </div>

                                <div className="spacer" aria-hidden="true">
                                    &nbsp;
                                </div>

                                <button
                                    className="button upload-button"
                                    type="submit"
                                    disabled={disableUpload || this.state.isLoading || this.state.isUploading}
                                >
                                    {this.state.isUploading ? 'Uploading…' : 'Upload'}
                                </button>
                            </form>

                            {!this.state.isErrorMessageHidden && (
                                <p className="error-message" role="alert">
                                    {this.state.error_message}
                                </p>
                            )}
                        </div>
                    </div>
                </div>
            </>
        )
    }
}

export default AdminUpload
