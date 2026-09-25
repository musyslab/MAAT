import React, { useEffect, useMemo, useState } from 'react'
import MenuComponent from '../components/MenuComponent'
import { Helmet } from 'react-helmet'
import { useParams, useSearchParams } from 'react-router-dom'
import { eachDayOfInterval } from 'date-fns'
import axios from 'axios'
import '../../styling/AdminProjectManage.scss'
import '../../styling/FileUploadCommon.scss'
import DirectoryBreadcrumbs from '../components/DirectoryBreadcrumbs'
import LoadingAnimation from '../components/LoadingAnimation'

import { TbJson } from 'react-icons/tb'
import {
    FaAlignJustify,
    FaArrowDown,
    FaArrowUp,
    FaCircleNotch,
    FaCloudUploadAlt,
    FaCode,
    FaDownload,
    FaEdit,
    FaExchangeAlt,
    FaFileAlt,
    FaKeyboard,
    FaPlusCircle,
    FaRegFile,
    FaTimes,
    FaUpload,
} from 'react-icons/fa'

class Testcase {
    constructor() {
        this.id = 0
        this.name = ''
        this.input = ''
        this.output = ''
        this.hidden = false
        this.order = 0
    }

    id: number
    name: string
    input: string
    output: string
    hidden: boolean
    order: number
}

const INPUT_EVENT_PATTERN = /\[\[\[MAAT_INPUT_B64:([A-Za-z0-9_-]*)\]\]\]/g

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

function InputTranscript({ text }: { text: string }) {
    if (!text || !text.includes('[[[MAAT_INPUT_B64:')) return <>{text}</>

    const parts: React.ReactNode[] = []
    let cursor = 0
    let eventIndex = 0

    for (const match of text.matchAll(INPUT_EVENT_PATTERN)) {
        const start = match.index ?? 0
        if (start > cursor) parts.push(text.slice(cursor, start))

        const value = decodeInputEvent(match[1] ?? '')
        parts.push(
            <span
                key={`project-input-${eventIndex}`}
                className="input-event"
                aria-label={`Program input: ${value || 'empty input'}`}
                title="The solution paused here and read one line of input"
            >
                <span className="input-event__label">
                    <FaKeyboard aria-hidden="true" /> Input
                </span>
                <span className={`input-event__value ${value === '' ? 'is-empty' : ''}`}>
                    {value === '' ? 'empty input' : value}
                </span>
            </span>
        )
        cursor = start + match[0].length
        eventIndex++
    }

    if (cursor < text.length) parts.push(text.slice(cursor))
    return <>{parts}</>
}

type AdminProjectManageProps = {
    practiceMode?: boolean
}

const AdminProjectManage = ({ practiceMode = false }: AdminProjectManageProps) => {

    const { id, school_id, class_id, module_id, practice_problem_id, checkpoint_id } = useParams()

    const [searchParams] = useSearchParams()

    const schoolId = Number(school_id)
    const project_id = Number(id)
    const classId = Number(class_id)
    const moduleId = Number(module_id)

    if (Number.isNaN(project_id) || Number.isNaN(schoolId) || Number.isNaN(classId) || Number.isNaN(moduleId)) {
        return <div>Error: Missing or invalid school, class, module, or project ID.</div>
    }

    const moduleListUrl = `/admin/school/${schoolId}/class/${classId}/modules`
    const moduleOverviewUrl = `/admin/school/${schoolId}/class/${classId}/module/${moduleId}/overview`
    const projectBaseUrl = `/admin/school/${schoolId}/class/${classId}/module/${moduleId}/project/${project_id}`

    const [testcases, setTestcases] = useState<Array<Testcase>>([])
    const [ProjectName, setProjectName] = useState<string>('')
    const [ProjectLanguage, setProjectLanguage] = useState<string>('')
    const [serverProjectNameSnapshot, setServerProjectNameSnapshot] = useState<string>('')
    const [serverProjectLanguageSnapshot, setServerProjectLanguageSnapshot] = useState<string>('')
    const [SubmitButton, setSubmitButton] = useState<string>('Submit file changes')
    const [SubmitJSON, setSubmitJSON] = useState<string>('Submit JSON file')
    const [getJSON, setGetJSON] = useState<string>('Export test cases to JSON')
    const [SolutionFiles, setSolutionFiles] = useState<File[]>([])
    const [serverSolutionFileNames, setServerSolutionFileNames] = useState<string[]>([])
    const [serverSolutionFileNamesSnapshot, setServerSolutionFileNamesSnapshot] = useState<string[]>([])
    const [JsonFile, setJsonFile] = useState<File | undefined>(undefined)
    const [AssignmentDesc, setDesc] = useState<File>()
    const [edit, setEdit] = useState<boolean>(false)
    const [selectedAddFiles, setSelectedAddFiles] = useState<File[]>([])
    const [modalOpen, setModalOpen] = useState<boolean>(false)
    const [selectedTestCaseId, setSelectedTestCaseId] = useState<number>(-4)
    const [solutionFileNames, setSolutionFileNames] = useState<string[]>([])
    const [descfileName, setDescFileName] = useState<string>('')
    const [serverDescFileName, setServerDescFileName] = useState<string>('')
    const [serverProjectStartSnapshot, setServerProjectStartSnapshot] = useState<string>('')
    const [serverProjectEndSnapshot, setServerProjectEndSnapshot] = useState<string>('')
    const [jsonfilename, setjsonfilename] = useState<string>('')
    const [activeStep, setActiveStep] = useState<'files' | 'testcases'>('files')
    const [submittingProject, setSubmittingProject] = useState<boolean>(false)
    const [submittingTestcase, setSubmittingTestcase] = useState<boolean>(false)
    const [submittingJson, setSubmittingJson] = useState<boolean>(false)
    const [reorderingTestcases, setReorderingTestcases] = useState<boolean>(false)
    const [loadingProjectState, setLoadingProjectState] = useState<boolean>(true)
    const [modalDraft, setModalDraft] = useState<Testcase | null>(null)
    const [previewOpen, setPreviewOpen] = useState(false)
    const [previewTitle, setPreviewTitle] = useState('')
    const [previewText, setPreviewText] = useState('')
    const [serverFiles, setServerFiles] = useState<string[] | null>(null)
    const [showAdditionalFile, setShowAdditionalFile] = useState<boolean>(false)
    const [additionalFileNames, setAdditionalFileNames] = useState<string[]>([])
    const [serverShowAdditionalFileSnapshot, setServerShowAdditionalFileSnapshot] = useState<boolean>(false)
    const [serverAdditionalFileNamesSnapshot, setServerAdditionalFileNamesSnapshot] = useState<string[]>([])
    const [removedAdditionalFiles, setRemovedAdditionalFiles] = useState<string[]>([])
    const [mainJavaFileName, setMainJavaFileName] = useState<string>('')
    const [practiceProblemNumber, setPracticeProblemNumber] = useState<number | null>(null)

    useEffect(() => {
        const requestedStep = searchParams.get('step')

        if (requestedStep === 'testcases' || requestedStep === 'files') {
            setActiveStep(requestedStep)
        }
    }, [searchParams])

    useEffect(() => {
        document.body.style.overflow = ''
        return () => {
            document.body.style.overflow = ''
        }
    }, [])

    // Lock page scroll whenever either modal is open
    useEffect(() => {
        const prev = document.body.style.overflow
        if (modalOpen || previewOpen) {
            document.body.style.overflow = 'hidden'
        } else {
            document.body.style.overflow = ''
        }
        return () => {
            document.body.style.overflow = prev
        }
    }, [modalOpen, previewOpen])

    const API = import.meta.env.VITE_API_URL
    const authHeader = { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` }
    const isPractice = !!practiceMode
    const practiceProblemId = isPractice && (checkpoint_id || practice_problem_id) ? Number(checkpoint_id || practice_problem_id) : null
    const practiceProblemQuery =
        isPractice && practiceProblemId ? `&checkpoint_id=${practiceProblemId}` : ''

    const hasSavedSolution = serverSolutionFileNamesSnapshot.length > 0
    const hasSavedAssignmentDescription = serverDescFileName.trim() !== ''
    const hasSavedProjectFiles = hasSavedSolution && hasSavedAssignmentDescription

    const hasSolution =
        SolutionFiles.length > 0 || serverSolutionFileNames.length > 0

    const hasTestcases = testcases.some((tc) => tc.id > 0)

    const filesNeedSetup = !hasSavedProjectFiles
    const testcasesNeedSetup = !hasTestcases

    const pageTitleText =
        ProjectName.trim()
        || serverProjectNameSnapshot.trim()
        || (isPractice
            ? `Practice Problem${practiceProblemNumber ? ` ${practiceProblemNumber}` : ''}`
            : 'Project')

    const SUPPORTED_RE = /\.(py|c|h|java|rkt|scm|cpp)$/i
    const SOLUTION_ALLOWED_RE = /\.(py|java|c|h|rkt|scm)$/i
    const DESC_ALLOWED_RE = /\.(pdf|docx?|txt)$/i
    const SOLUTION_ACCEPT = '.py,.java,.c,.h,.rkt,.scm'
    const DESC_ACCEPT = '.pdf,.doc,.docx,.txt'

    const ADD_ALLOWED_RE = /\.(txt)$/i
    const ADD_ACCEPT = '.txt'

    const JAVA_MAIN_RE = /\bpublic\s+static\s+void\s+main\s*\(/
    const isJavaFileName = (n: string) => /\.java$/i.test(n)

    const basename = (p: string) => (p || '').split(/[\\/]/).pop() || ''

    const normalizeNameList = (names: string[]) =>
        [...names.map(basename).filter(Boolean)].sort((a, b) => a.localeCompare(b))


    const parseHidden = (v: any): boolean => {
        if (typeof v === 'boolean') return v
        if (typeof v === 'number') return v !== 0
        if (typeof v === 'string') {
            const s = v.trim().toLowerCase()
            return s === '1' || s === 'true' || s === 'yes' || s === 'y'
        }
        return false
    }

    const parseTestcaseRows = (data: any): Array<Testcase> => {
        let parsedData = data
        if (typeof data === 'string') {
            try {
                parsedData = JSON.parse(data)
            } catch {
                parsedData = []
            }
        }

        const rawRows = Array.isArray(parsedData)
            ? parsedData
            : parsedData && typeof parsedData === 'object'
                ? Object.values(parsedData)
                : []

        const rows = rawRows
            .map((value: any, index: number) => {
                const values = Array.isArray(value) ? value : []
                const testcase = new Testcase()
                testcase.id = Number(values[0] ?? 0)
                testcase.name = String(values[1] ?? '')
                testcase.input = String(values[2] ?? '')
                testcase.output = String(values[3] ?? '')
                testcase.hidden = parseHidden(values[4])
                const parsedOrder = Number(values[5])
                testcase.order = Number.isFinite(parsedOrder) && parsedOrder > 0
                    ? parsedOrder
                    : index + 1
                return testcase
            })
            .filter(testcase => testcase.id > 0)
            .sort((a, b) => a.order - b.order || a.id - b.id)
            .map((testcase, index) => ({ ...testcase, order: index + 1 } as Testcase))

        const blank = new Testcase()
        blank.id = -1
        blank.order = rows.length + 1
        return [...rows, blank]
    }

    type SolutionLang = 'java' | 'python' | 'c' | 'racket'
    const solutionLangFor = (name: string): SolutionLang | null => {
        const lower = name.toLowerCase()
        if (lower.endsWith('.java')) return 'java'
        if (lower.endsWith('.py')) return 'python'
        if (lower.endsWith('.c') || lower.endsWith('.h')) return 'c'
        if (lower.endsWith('.rkt') || lower.endsWith('.scm')) return 'racket'
        return null
    }

    function pickMainJavaFile(allJavaNames: string[], namesWithMain: string[]): string {
        if (namesWithMain.length === 1) return namesWithMain[0]
        const mainDotJava = allJavaNames.find(n => n.toLowerCase() === 'main.java')
        if (mainDotJava) return mainDotJava
        return namesWithMain[0] || ''
    }

    async function computeMainJavaFromLocal(files: File[]) {
        const javaFiles = files.filter(f => isJavaFileName(f.name))
        if (javaFiles.length <= 1) {
            setMainJavaFileName('')
            return
        }
        const withMain: string[] = []
        for (const f of javaFiles) {
            try {
                const txt = await f.text()
                if (JAVA_MAIN_RE.test(txt)) withMain.push(f.name)
            } catch {
                // ignore read failures
            }
        }
        setMainJavaFileName(pickMainJavaFile(javaFiles.map(f => f.name), withMain))
    }

    async function computeMainJavaFromServer(names: string[]) {
        const javaNames = names.filter(isJavaFileName)
        if (javaNames.length <= 1) {
            setMainJavaFileName('')
            return
        }
        const withMain: string[] = []
        await Promise.all(
            javaNames.map(async (name) => {
                try {
                    const url = new URL(`${API}/projects/get_source_file`)
                    url.searchParams.set('project_id', String(project_id))
                    url.searchParams.set('relpath', name)
                    url.searchParams.set('practice', isPractice ? 'true' : 'false')
                    if (isPractice && practiceProblemId) url.searchParams.set('checkpoint_id', String(practiceProblemId))
                    const res = await fetch(url, { headers: authHeader })
                    if (!res.ok) return
                    const txt = await res.text()
                    if (JAVA_MAIN_RE.test(txt)) withMain.push(name)
                } catch {
                    // ignore fetch failures
                }
            })
        )
        setMainJavaFileName(pickMainJavaFile(javaNames, withMain))
    }

    useEffect(() => {
        let cancelled = false
            ; (async () => {
                if (ProjectLanguage !== 'java') {
                    if (!cancelled) setMainJavaFileName('')
                    return
                }

                if (SolutionFiles.length > 0) {
                    await computeMainJavaFromLocal(SolutionFiles)
                    return
                }
                if (edit && serverSolutionFileNames.length > 0) {
                    await computeMainJavaFromServer(serverSolutionFileNames)
                    return
                }
                if (!cancelled) setMainJavaFileName('')
            })()
        return () => {
            cancelled = true
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [ProjectLanguage, SolutionFiles, serverSolutionFileNames, edit, project_id])

    const JSON_ICON_RE = /\.json$/i
    const CODE_ICON_RE = /\.(java|py|c|h|rkt|scm)$/i
    const TEXT_ICON_RE = /\.(txt|doc|docx|pdf)$/i

    function getFileIcon(filename: string): React.ReactNode {
        if (JSON_ICON_RE.test(filename)) return <TbJson className="file-language-icon json" aria-hidden="true" />
        if (CODE_ICON_RE.test(filename)) return <FaCode className="file-language-icon" aria-hidden="true" />
        if (TEXT_ICON_RE.test(filename)) return <FaAlignJustify className="file-language-icon" aria-hidden="true" />
        return <FaTimes className="file-language-icon" aria-hidden="true" />
    }

    async function openLocalPreview(file: File) {
        if (!SUPPORTED_RE.test(file.name)) {
            window.alert('Preview supports .py .c .h .java .rkt .scm (.cpp optional)')
            return
        }
        const text = await file.text()
        setPreviewTitle(file.name)
        setPreviewText(text)
        setPreviewOpen(true)
    }

    async function openAllSolutionPreview() {
        const isLocal = SolutionFiles.length > 0
        const names = isLocal ? SolutionFiles.map(f => f.name) : serverSolutionFileNames
        if (names.length === 0) return

        try {
            if (isLocal) {
                const parts = await Promise.all(
                    SolutionFiles.map(async (f) => `// ===== ${f.name} =====\n${await f.text()}`)
                )
                setPreviewTitle('Solution Files')
                setPreviewText(parts.join('\n\n'))
                setPreviewOpen(true)
                return
            }

            const parts = await Promise.all(
                serverSolutionFileNames.map(async (name) => {
                    const url = new URL(`${API}/projects/get_source_file`)
                    url.searchParams.set('project_id', String(project_id))
                    url.searchParams.set('relpath', name)
                    url.searchParams.set('practice', isPractice ? 'true' : 'false')
                    if (isPractice && practiceProblemId) url.searchParams.set('checkpoint_id', String(practiceProblemId))
                    const res = await fetch(url, { headers: authHeader })
                    const text = res.ok ? await res.text() : '[Could not load file from server]'
                    return `// ===== ${name} =====\n${text}`
                })
            )
            setPreviewTitle('Solution Files')
            setPreviewText(parts.join('\n\n'))
            setPreviewOpen(true)
        } catch (e) {
            console.log(e)
            window.alert('Could not preview solution files.')
        }
    }

    const languageLabel = (() => {
        if (!ProjectLanguage) return 'Not detected yet'
        if (ProjectLanguage === 'java') return 'Java'
        if (ProjectLanguage === 'python') return 'Python'
        if (ProjectLanguage === 'c') return 'C'
        if (ProjectLanguage === 'racket') return 'Racket'
        return ProjectLanguage
    })()

    async function openServerPreview(relpath?: string) {
        const url = new URL(`${API}/projects/get_source_file`)
        url.searchParams.set('project_id', String(project_id))
        if (relpath) url.searchParams.set('relpath', relpath)

        url.searchParams.set('practice', isPractice ? 'true' : 'false')
        if (isPractice && practiceProblemId) url.searchParams.set('checkpoint_id', String(practiceProblemId))

        const res = await fetch(url, { headers: authHeader })
        if (!res.ok) return
        const text = await res.text()
        setPreviewTitle(relpath || 'source')
        setPreviewText(text)
        setPreviewOpen(true)
    }

    const defaultStart = (() => {
        const d = new Date()
        d.setHours(23, 59, 0, 0)
        return d
    })()

    const defaultEnd = (() => {
        const d = new Date()
        d.setDate(d.getDate() + 7)
        d.setHours(23, 59, 0, 0)
        return d
    })()


    const [ProjectStartDate, setProjectStartDate] = useState<Date | null>(null)
    const [ProjectEndDate, setProjectEndDate] = useState<Date | null>(null)

    const [overlapError, setOverlapError] = useState<boolean>(false)

    const highlightDates: Date[] =
        ProjectStartDate && ProjectEndDate ? eachDayOfInterval({ start: ProjectStartDate, end: ProjectEndDate }) : []

    const [projects, setProjects] = useState([])
    useEffect(() => {
        let cancelled = false
        const fetchProjects = async () => {
            try {
                const response = await axios.get(
                    `${import.meta.env.VITE_API_URL}/projects/get_projects_by_class_id?id=${classId}&dates_only=true`,
                    {
                        headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
                    }
                )
                const otherProjects = response.data.filter((pStr) => {
                    const p = JSON.parse(pStr)
                    return p.Id !== project_id
                })
                if (!cancelled) setProjects(otherProjects)
            } catch (err) {
                console.log(err)
                if (!cancelled) setProjects([])
            }
        }

        if (classId > 0) fetchProjects()
        return () => {
            cancelled = true
        }
    }, [classId, project_id])

    const blockedDates = useMemo(() => {
        const dates = [];

        if (projects && projects.length > 0) {
            projects.forEach((projectStr) => {
                const project = JSON.parse(projectStr);
                const projectStart = new Date(project.Start);
                const projectEnd = new Date(project.End);

                let currentDay = new Date(projectStart);
                currentDay.setHours(0, 0, 0, 0);

                const lastDay = new Date(projectEnd);
                lastDay.setHours(0, 0, 0, 0);

                while (currentDay <= lastDay) {

                    const dayStart = new Date(currentDay);
                    dayStart.setHours(0, 0, 0, 0); // 00:00:00

                    const dayEnd = new Date(currentDay);
                    dayEnd.setHours(23, 59, 0, 0); // 23:59

                    const isFullyOccupied = (projectStart <= dayStart) && (projectEnd >= dayEnd);

                    if (isFullyOccupied) {
                        dates.push(new Date(currentDay));
                    }

                    currentDay.setDate(currentDay.getDate() + 1);
                }
            });
        }
        return dates;
    }, [projects]);

    const projectRanges = useMemo(() => {
        if (!projects) return [];
        return projects.map((pStr) => {
            const p = JSON.parse(pStr);
            return {
                start: new Date(p.Start),
                end: new Date(p.End)
            };
        });
    }, [projects]);


    const handleTimeColors = (time) => {
        const isBlocked = projectRanges.some((range) => {
            // Allow boundary-touching (end == start) without marking as blocked
            return time > range.start && time < range.end;
        });

        return isBlocked ? "react-datepicker__time--highlighted-red" : null;
    };

    const getInjectedTimes = (dateValue) => {
        if (!dateValue) return [];

        const endOfDay = new Date(dateValue);

        endOfDay.setHours(23, 59, 0, 0);

        return [endOfDay];
    };

    useEffect(() => {
        let cancelled = false

        const resetForSwitch = () => {
            setLoadingProjectState(true)

            setServerProjectNameSnapshot('')
            setServerProjectStartSnapshot('')
            setServerProjectEndSnapshot('')
            setServerAdditionalFileNamesSnapshot([])
            setServerShowAdditionalFileSnapshot(false)

            // Clear UI that frequently shows stale values during practice <-> main switches
            setTestcases([])
            setProjectName('')
            setProjectLanguage('')
            setServerProjectLanguageSnapshot('')
            setSolutionFiles([])
            setSolutionFileNames([])
            setServerSolutionFileNames([])
            setServerSolutionFileNamesSnapshot([])
            setServerFiles(null)
            setDesc(undefined)
            setDescFileName('')
            setServerDescFileName('')
            setAdditionalFileNames([])
            setRemovedAdditionalFiles([])
            setSelectedAddFiles([])
            setShowAdditionalFile(false)
            setMainJavaFileName('')
            setEdit(false)
            setSubmitButton('Submit file changes')
        }

        const load = async () => {
            if (!project_id || project_id === 0) {
                if (!cancelled) setLoadingProjectState(false)
                return
            }

            resetForSwitch()

            try {
                const [tcRes, projRes, filesRes, solutionsRes] = await Promise.all([
                    axios.get(
                        `${import.meta.env.VITE_API_URL}/projects/get_testcases?id=${project_id}&practice=${isPractice ? 'true' : 'false'}${practiceProblemQuery}`,
                        { headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` } }
                    ),
                    axios.get(
                        `${import.meta.env.VITE_API_URL}/projects/get_project_id?id=${project_id}&practice=${isPractice ? 'true' : 'false'}${practiceProblemQuery}`,
                        { headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` } }
                    ),
                    axios.get(
                        `${API}/projects/list_source_files?project_id=${project_id}&practice=${isPractice ? 'true' : 'false'}${practiceProblemQuery}`,
                        { headers: authHeader }
                    ).catch(() => ({ data: { files: [] } })),
                    axios.get(
                        `${API}/projects/list_solution_files?id=${project_id}&practice=${isPractice ? 'true' : 'false'}${practiceProblemQuery}`,
                        { headers: authHeader }
                    ).catch(() => ({ data: [] })),
                ])

                if (cancelled) return

                // Testcases
                {
                    setTestcases(parseTestcaseRows(tcRes.data))
                }

                // Project info
                {
                    const data = projRes.data
                    const projectName = data[project_id][0] || ''
                    const projectStart = new Date(data[project_id][2])
                    const projectEnd = new Date(data[project_id][3])

                    setProjectName(projectName)
                    setServerProjectNameSnapshot(projectName)

                    setProjectStartDate(projectStart)
                    setServerProjectStartSnapshot(formatDateTimeLocal(projectStart))

                    setProjectEndDate(projectEnd)
                    setServerProjectEndSnapshot(formatDateTimeLocal(projectEnd))

                    setProjectLanguage(data[project_id][4])
                    setServerProjectLanguageSnapshot(data[project_id][4])
                    const pnRaw = (data[project_id] as any)[9]
                    const pn = typeof pnRaw === 'number' ? pnRaw : Number(pnRaw)
                    setPracticeProblemNumber(isPractice && pn > 0 ? pn : null)

                    const serverDesc = (data[project_id][6] || '') as string
                    setDescFileName(serverDesc)
                    setServerDescFileName(serverDesc)

                    const rawAdd = data[project_id][7] ?? []
                    let addList: string[] = []
                    if (Array.isArray(rawAdd)) {
                        addList = rawAdd as string[]
                    } else if (typeof rawAdd === 'string') {
                        try {
                            const parsed = JSON.parse(rawAdd)
                            addList = Array.isArray(parsed) ? parsed : (rawAdd ? [rawAdd] : [])
                        } catch {
                            addList = rawAdd ? [rawAdd] : []
                        }
                    }

                    const normalizedAdditionalFiles = addList.map(basename).filter(Boolean)
                    setAdditionalFileNames(normalizedAdditionalFiles)
                    setServerAdditionalFileNamesSnapshot(normalizedAdditionalFiles)
                    setShowAdditionalFile(normalizedAdditionalFiles.length > 0)
                    setServerShowAdditionalFileSnapshot(normalizedAdditionalFiles.length > 0)

                    setEdit(true)
                    setSubmitButton('Submit file changes')
                }

                const fileList = Array.isArray(filesRes.data?.files)
                    ? filesRes.data.files.map((file: { relpath: string }) => file.relpath)
                    : []
                const solutionNames = Array.isArray(solutionsRes.data) ? solutionsRes.data : []
                setServerFiles(fileList)
                setServerSolutionFileNames(solutionNames)
                setServerSolutionFileNamesSnapshot(solutionNames)

            } catch (err) {
                console.log(err)
            } finally {
                if (!cancelled) setLoadingProjectState(false)
            }
        }

        load()
        return () => {
            cancelled = true
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [project_id, isPractice])

    function handleNameChange(testcase_id: number, name: string) {
        setModalDraft(prev => {
            if (prev && prev.id === testcase_id) return { ...prev, name }
            return prev
        })
    }

    function handleInputChange(testcase_id: number, input_data: string) {
        setModalDraft(prev => {
            if (prev && prev.id === testcase_id) return { ...prev, input: input_data }
            return prev
        })
    }

    function handleHiddenChange(testcase_id: number, hidden: boolean) {
        setModalDraft(prev => {
            if (prev && prev.id === testcase_id) return { ...prev, hidden }
            return prev
        })
    }

    function buttonhandleTrashClick(testcase: number) {
        let test: Testcase = new Testcase()
        for (let i = 0; i < testcases.length; i++) {
            if (testcases[i].id === testcase) {
                test = testcases[i]
                break
            }
        }

        const formData = new FormData()
        formData.append('id', test.id.toString())

        axios
            .post(import.meta.env.VITE_API_URL + `/projects/remove_testcase`, formData, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })
            .then(function (response) {
                reloadtests()
            })
            .catch(function (error) {
                console.log(error)
            })

        setModalOpen(false)
    }

    function reloadtests() {
        return axios
            .get(import.meta.env.VITE_API_URL + `/projects/get_testcases?id=${project_id}&practice=${isPractice ? 'true' : 'false'}${practiceProblemQuery}`, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })
            .then(res => {
                setTestcases(parseTestcaseRows(res.data))
            })
            .catch(err => {
                console.log(err)
            })
    }

    function setDate(date: Date | null, isStart: boolean) {
        let finalDate = date;

        const previousDate = isStart ? ProjectStartDate : ProjectEndDate;
        const isNewDay = !previousDate || (date && date.toDateString() !== previousDate.toDateString());

        if (finalDate && isNewDay) {
            // Allow boundary-touching (end == start) without treating it as blocked
            const isBlocked = projectRanges.some(range =>
                finalDate > range.start && finalDate < range.end
            );

            if (isBlocked) {
                let candidate = new Date(finalDate);
                candidate.setHours(0, 0, 0, 0);

                let foundSafeTime = false;

                const checkTime = (t) => projectRanges.some(r => t > r.start && t < r.end);

                while (candidate.getDate() === finalDate.getDate()) {
                    if (!checkTime(candidate)) {
                        finalDate = candidate;
                        foundSafeTime = true;
                        break;
                    }
                    candidate.setMinutes(candidate.getMinutes() + 15);
                }

                //checks 11:59 if loop failed
                if (!foundSafeTime) {
                    let endOfDay = new Date(finalDate);
                    endOfDay.setHours(23, 59, 0, 0);
                    if (!checkTime(endOfDay)) {
                        finalDate = endOfDay;
                    }
                }
            }
        }

        if (isStart) {
            setProjectStartDate(finalDate);
        } else {
            setProjectEndDate(finalDate);
        }

        if (!finalDate) {
            setOverlapError(false);
            return;
        }


        const startToCheck = isStart ? finalDate : ProjectStartDate;
        const endToCheck = isStart ? ProjectEndDate : finalDate;

        let overlap = false;
        for (const project of projectRanges) {


            if (finalDate > project.start && finalDate < project.end) {
                overlap = true;
                break;
            }

            if (startToCheck && endToCheck) {
                if (startToCheck < project.end && endToCheck > project.start) {
                    overlap = true;
                    break;
                }
            }
        }

        setOverlapError(overlap);
    }

    async function handleJsonSubmit() {
        try {
            setSubmittingJson(true)
            if (!hasSavedProjectFiles) {
                window.alert('Save both the solution file(s) and assignment description before uploading test cases.')
                return
            }
            const formData = new FormData()
            formData.append('file', JsonFile!)
            formData.append('project_id', project_id.toString())
            formData.append('class_id', classId.toString())
            formData.append('module_id', moduleId.toString())
            formData.append('practice_problems_enabled', 'true')
            formData.append('practice', isPractice ? 'true' : 'false')
            if (isPractice && practiceProblemId) {
                formData.append('checkpoint_id', String(practiceProblemId))
            }

            await axios.post(import.meta.env.VITE_API_URL + `/projects/json_add_testcases`, formData, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })

            await reloadtests()

            setjsonfilename('')
            const jsonInput = document.getElementById('jsonFile') as HTMLInputElement | null
            if (jsonInput) jsonInput.value = ''
        } catch (error) {
            console.log(error)
        } finally {
            setSubmittingJson(false)
        }
    }

    async function handleNewSubmit() {
        if (!ProjectName || !ProjectStartDate || !ProjectEndDate || !ProjectLanguage) {
            window.alert('Please fill out all fields')
            return
        }
        if (SolutionFiles.length === 0 || !AssignmentDesc) {
            window.alert('Please upload your solution file(s) and the assignment description.')
            return
        }
        if (ProjectStartDate.getTime() >= ProjectEndDate.getTime()) {
            window.alert('End date must be after start date.')
            return
        }

        try {
            setSubmittingProject(true)
            const conflictCheck = await axios.post(
                `${import.meta.env.VITE_API_URL}/projects/check_time_conflict`,
                {
                    project_id: project_id,
                    class_id: classId,
                    start_date: formatDateTimeLocal(ProjectStartDate),
                    end_date: formatDateTimeLocal(ProjectEndDate),
                },
                { headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` } }
            )

            if (conflictCheck?.data?.conflict) {
                const first = conflictCheck.data.conflicts?.[0]
                const s = first?.start ? new Date(first.start).toLocaleString() : ''
                const e = first?.end ? new Date(first.end).toLocaleString() : ''
                window.alert(
                    `The selected dates overlap with an existing assignment "${first?.name ?? 'Unknown'}" (${s} - ${e}). Please adjust your dates.`
                )
                return
            }

            const formData = new FormData()
            SolutionFiles.forEach(f => formData.append('solutionFiles', f))
            formData.append('assignmentdesc', AssignmentDesc)
            selectedAddFiles.forEach(f => formData.append('additionalFiles', f))
            if (selectedAddFiles.length === 0 && additionalFileNames.length === 0) {
                formData.append('clearAdditionalFiles', 'true')
            }
            formData.append('name', ProjectName)
            formData.append('start_date', formatDateTimeLocal(ProjectStartDate))
            formData.append('end_date', formatDateTimeLocal(ProjectEndDate))
            formData.append('language', ProjectLanguage)
            formData.append('class_id', classId.toString())
            formData.append('module_id', moduleId.toString())
            formData.append('practice_problems_enabled', 'true')

            const res = await axios.post(`${import.meta.env.VITE_API_URL}/projects/create_project`, formData, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })
            const newId = res.data

            window.alert('Your project has been created! Next, open the "Test Cases" tab to add test cases.')
            window.location.href = `/admin/school/${schoolId}/class/${classId}/module/${moduleId}/project/${newId}/manage`
        } catch (error) {
            console.log(error)
        }
        finally {
            setSubmittingProject(false)
        }
    }

    async function handleEditSubmit() {
        try {
            if (!ProjectLanguage) {
                window.alert('Please upload a valid solution file so the project language can be detected.')
                return
            }

            const safeProjectName = ProjectName || serverProjectNameSnapshot || 'Project'
            const safeProjectStart = ProjectStartDate || (serverProjectStartSnapshot ? new Date(serverProjectStartSnapshot) : defaultStart)
            const safeProjectEnd = ProjectEndDate || (serverProjectEndSnapshot ? new Date(serverProjectEndSnapshot) : defaultEnd)

            setSubmittingProject(true)

            const formData = new FormData()
            formData.append('id', project_id.toString())

            if (SolutionFiles.length > 0) {
                SolutionFiles.forEach(f => formData.append('solutionFiles', f))
            }
            if (AssignmentDesc) formData.append('assignmentdesc', AssignmentDesc)

            selectedAddFiles.forEach(f => formData.append('additionalFiles', f))

            if (removedAdditionalFiles.length > 0) {
                formData.append('removeAdditionalFiles', JSON.stringify(removedAdditionalFiles))
            }
            if (selectedAddFiles.length === 0 && additionalFileNames.length === 0) {
                formData.append('clearAdditionalFiles', 'true')
            }

            formData.append('name', safeProjectName)
            formData.append('start_date', formatDateTimeLocal(safeProjectStart))
            formData.append('end_date', formatDateTimeLocal(safeProjectEnd))
            formData.append('language', ProjectLanguage)
            formData.append('class_id', classId.toString())
            formData.append('module_id', moduleId.toString())
            formData.append('practice_problems_enabled', 'true')

            await axios.post(`${import.meta.env.VITE_API_URL}/projects/edit_project`, formData, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })

            window.alert('Project files saved. Next, go to the "Test Cases" tab to create or update test cases.')
            window.location.href = `${projectBaseUrl}/manage`
        } catch (error) {
            console.log(error)
        }
        finally {
            setSubmittingProject(false)
        }
    }

    function handleOpenModal(TestCaseId: number) {
        setModalOpen(true)
        setSelectedTestCaseId(TestCaseId)

        if (TestCaseId === -1) {
            const t = new Testcase()
            t.id = -1
            t.name = ''
            t.input = ''
            t.output = ''
            t.hidden = false
            t.order = testcases.filter(tc => tc.id > 0).length + 1
            setModalDraft(t)
        } else {
            const source = testcases.find(tc => tc.id === TestCaseId)
            let draft: Testcase | null = null
            if (source) draft = { ...source } as Testcase
            setModalDraft(draft)
        }
    }

    async function setHiddenFromRow(tc: Testcase, hidden: boolean) {
        if (!hasSavedProjectFiles) {
            window.alert('Save both the solution file(s) and assignment description before editing test cases.')
            return
        }
        const formData = new FormData()
        formData.append('id', tc.id.toString())
        formData.append('name', tc.name)
        formData.append('project_id', project_id.toString())
        formData.append('class_id', classId.toString())
        formData.append('input', tc.input.toString())
        formData.append('output', tc.output.toString())
        formData.append('hidden', hidden ? 'true' : 'false')
        formData.append('order', tc.order.toString())
        formData.append('practice', isPractice ? 'true' : 'false')
        if (isPractice && practiceProblemId) {
            formData.append('checkpoint_id', String(practiceProblemId))
        }

        try {
            setSubmittingTestcase(true)
            await axios.post(import.meta.env.VITE_API_URL + `/projects/add_or_update_testcase`, formData, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })
            await reloadtests()
        } catch (error) {
            console.log(error)
        } finally {
            setSubmittingTestcase(false)
        }
    }

    function handleSolutionFilesChange(event: React.FormEvent) {
        const target = event.target as HTMLInputElement
        const files = target.files

        if (!files || files.length === 0) {
            setSolutionFiles([])
            setSolutionFileNames([])
            setProjectLanguage(edit ? (serverProjectLanguageSnapshot || ProjectLanguage) : '')
            return
        }
        const arr = Array.from(files)

        if (arr.some(f => !SOLUTION_ALLOWED_RE.test(f.name))) {
            window.alert('Solution files must be Python (.py), Java (.java), C (.c/.h), or Racket (.rkt/.scm).')
            target.value = ''
            setSolutionFiles([])
            setSolutionFileNames([])
            setProjectLanguage(edit ? (serverProjectLanguageSnapshot || ProjectLanguage) : '')
            return
        }

        const langs = new Set(arr.map(f => solutionLangFor(f.name)))
        langs.delete(null)

        if (langs.size > 1) {
            window.alert('Do not mix solution file types. Upload files for only ONE language at a time.')
            target.value = ''
            setSolutionFiles([])
            setSolutionFileNames([])
            setProjectLanguage(edit ? (serverProjectLanguageSnapshot || ProjectLanguage) : '')
            return
        }

        const onlyLang = Array.from(langs)[0] as SolutionLang | undefined
        if (arr.length > 1 && onlyLang !== 'java') {
            window.alert('Multiple solution files are only supported for Java. Upload a single file for other languages.')
            target.value = ''
            setSolutionFiles([])
            setSolutionFileNames([])
            setProjectLanguage(edit ? (serverProjectLanguageSnapshot || ProjectLanguage) : '')
            return
        }

        setSolutionFiles(arr)
        setSolutionFileNames(arr.map(f => f.name))
        setProjectLanguage(onlyLang ?? '')
    }

    function handleJsonFileChange(event: React.FormEvent) {
        const target = event.target as HTMLInputElement
        const files = target.files

        if (files != null && files.length === 1) {
            if (!/\.json$/i.test(files[0].name)) {
                window.alert('Test case upload must be a JSON file (.json).')
                target.value = ''
                setJsonFile(undefined)
                setjsonfilename('')
                return
            }
            setJsonFile(files[0])
            setjsonfilename(files[0].name)
        } else {
            setJsonFile(undefined)
        }
    }

    function handleDescFileChange(event: React.FormEvent) {
        const target = event.target as HTMLInputElement
        const files = target.files

        if (files != null && files.length === 1) {
            if (!DESC_ALLOWED_RE.test(files[0].name)) {
                window.alert('Description file must be a Word document (.doc/.docx), PDF (.pdf), or text file (.txt).')
                target.value = ''
                setDescFileName('')
                setDesc(undefined)
                return
            }
            setDesc(files[0])
            setDescFileName(files[0].name)
        } else {
            setDesc(undefined)
        }
    }

    async function downloadAssignmentDescription() {
        try {

            const url =
                `${import.meta.env.VITE_API_URL}/projects/getAssignmentDescription?project_id=${project_id}` +
                `&practice=${isPractice ? 'true' : 'false'}${practiceProblemQuery}`

            const res = await axios.get(url, {
                responseType: 'blob',
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })
            const extMatch = (descfileName || '').match(/\.[^.]+$/)
            const ext = extMatch ? extMatch[0] : '.pdf'
            const filename = descfileName && descfileName.trim() ? descfileName : `assignment_description${ext}`
            const blob = new Blob([res.data])
            const a = document.createElement('a')
            a.href = URL.createObjectURL(blob)
            a.download = filename
            document.body.appendChild(a)
            a.click()
            URL.revokeObjectURL(a.href)
            a.remove()
        } catch (err) {
            console.log(err)
            window.alert('Could not download the assignment description.')
        }
    }

    async function handlePracticeFilesSubmit() {
        if (!isPractice) return
        if (!practiceProblemId) {
            window.alert('Missing practice problem id.')
            return
        }
        if (!ProjectName || !ProjectName.trim()) {
            window.alert('Please enter a practice problem name.')
            return
        }

        const hasAnyFileChange =
            SolutionFiles.length > 0 || !!AssignmentDesc || selectedAddFiles.length > 0

        // Rename-only: allow changing Practice Problem Name without re-uploading files
        if (!hasAnyFileChange) {
            try {
                setSubmittingProject(true)
                await axios.post(
                    `${import.meta.env.VITE_API_URL}/projects/rename_checkpoint`,
                    {
                        project_id,
                        checkpoint_id: practiceProblemId,
                        name: ProjectName,
                    },
                    { headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` } }
                )
                window.alert('Practice problem name saved.')
                window.location.reload()
            } catch (e) {
                console.log(e)
                window.alert('Could not save practice problem name.')
            } finally {
                setSubmittingProject(false)
            }
            return
        }

        // File change path: still require both solution + description for the practice upload endpoint
        if (SolutionFiles.length === 0 || !AssignmentDesc) {
            window.alert('Please upload practice solution file(s) and a practice assignment description.')
            return
        }

        try {
            setSubmittingProject(true)
            const formData = new FormData()
            formData.append('project_id', project_id.toString())
            SolutionFiles.forEach(f => formData.append('solutionFiles', f))
            formData.append('assignmentdesc', AssignmentDesc)
            selectedAddFiles.forEach(f => formData.append('additionalFiles', f))
            formData.append('checkpoint_id', String(practiceProblemId))
            formData.append('name', ProjectName)
            formData.append('language', ProjectLanguage)

            await axios.post(`${import.meta.env.VITE_API_URL}/projects/edit_checkpoint_project_files`, formData, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })

            window.alert('Practice files saved.')
            window.location.reload()
        } catch (e) {
            console.log(e)
            window.alert('Could not save practice files.')
        } finally {
            setSubmittingProject(false)
        }
    }

    function handleAdditionalFileChange(event: React.FormEvent) {
        const target = event.target as HTMLInputElement
        const files = target.files
        if (Array.from(files).some(f => !ADD_ALLOWED_RE.test(f.name))) {
            window.alert('Additional files must be .txt')
            target.value = ''
            return
        }
        setSelectedAddFiles(prev => {
            const existing = new Set(prev.map(f => f.name))
            const merged = [...prev]
            Array.from(files).forEach(f => {
                if (!existing.has(f.name)) merged.push(f)
            })
            return merged
        })
    }

    function removeSelectedAdditional(name: string) {
        setSelectedAddFiles(prev => prev.filter(f => f.name !== name))
    }

    function removeServerAdditional(name: string) {
        setRemovedAdditionalFiles(prev => (prev.includes(name) ? prev : [...prev, name]))
        setAdditionalFileNames(prev => prev.filter(n => n !== name))
    }

    function formatDateTimeLocal(date: Date): string {
        const pad = (n: number) => n.toString().padStart(2, '0')
        return [
            date.getFullYear(),
            '-',
            pad(date.getMonth() + 1),
            '-',
            pad(date.getDate()),
            'T',
            pad(date.getHours()),
            ':',
            pad(date.getMinutes()),
        ].join('')
    }

    const hasUnsavedProjectChanges = useMemo(() => {
        if (loadingProjectState) return false

        if (!edit) {
            return (
                ProjectName.trim() !== '' ||
                !!ProjectStartDate ||
                !!ProjectEndDate ||
                ProjectLanguage !== '' ||
                SolutionFiles.length > 0 ||
                !!AssignmentDesc ||
                selectedAddFiles.length > 0 ||
                showAdditionalFile
            )
        }

        if (!ProjectStartDate || !ProjectEndDate) return false

        const currentSolutionNames = normalizeNameList(
            SolutionFiles.length > 0 ? SolutionFiles.map(f => f.name) : serverSolutionFileNames
        )
        const originalSolutionNames = normalizeNameList(serverSolutionFileNamesSnapshot)
        const currentAdditionalNames = normalizeNameList(additionalFileNames)
        const originalAdditionalNames = normalizeNameList(serverAdditionalFileNamesSnapshot)

        return (
            ProjectName.trim() !== serverProjectNameSnapshot.trim() ||
            formatDateTimeLocal(ProjectStartDate) !== serverProjectStartSnapshot ||
            formatDateTimeLocal(ProjectEndDate) !== serverProjectEndSnapshot ||
            ProjectLanguage !== serverProjectLanguageSnapshot ||
            showAdditionalFile !== serverShowAdditionalFileSnapshot ||
            !!AssignmentDesc ||
            SolutionFiles.length > 0 ||
            selectedAddFiles.length > 0 ||
            removedAdditionalFiles.length > 0 ||
            descfileName !== serverDescFileName ||
            JSON.stringify(currentSolutionNames) !== JSON.stringify(originalSolutionNames) ||
            JSON.stringify(currentAdditionalNames) !== JSON.stringify(originalAdditionalNames)
        )
    }, [
        loadingProjectState,
        edit,
        ProjectName,
        ProjectStartDate,
        ProjectEndDate,
        ProjectLanguage,
        SolutionFiles,
        AssignmentDesc,
        selectedAddFiles,
        showAdditionalFile,
        serverProjectNameSnapshot,
        serverProjectStartSnapshot,
        serverProjectEndSnapshot,
        serverProjectLanguageSnapshot,
        serverSolutionFileNames,
        serverSolutionFileNamesSnapshot,
        serverAdditionalFileNamesSnapshot,
        serverShowAdditionalFileSnapshot,
        additionalFileNames,
        removedAdditionalFiles,
        descfileName,
        serverDescFileName,
    ])

    async function buttonhandleClick(testcase: number) {
        if (!modalDraft) return

        if (!hasSavedProjectFiles) {
            window.alert('Save both the solution file(s) and assignment description before creating or editing test cases.')
            return
        }

        const formData = new FormData()
        formData.append('id', modalDraft.id.toString())
        formData.append('name', modalDraft.name)
        formData.append('project_id', project_id.toString())
        formData.append('class_id', classId.toString())
        formData.append('input', modalDraft.input.toString())
        formData.append('output', modalDraft.output.toString())
        formData.append('hidden', modalDraft.hidden ? 'true' : 'false')
        formData.append('order', modalDraft.order.toString())
        formData.append('practice', isPractice ? 'true' : 'false')

        if (isPractice && practiceProblemId) {
            formData.append('checkpoint_id', String(practiceProblemId))
        }

        if (modalDraft.name.trim() === '' || modalDraft.input === '') {
            window.alert('Please provide a testcase name and input')
            return
        }

        try {
            setSubmittingTestcase(true)
            await axios.post(import.meta.env.VITE_API_URL + `/projects/add_or_update_testcase`, formData, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })
            await reloadtests()
            setModalOpen(false)
            setModalDraft(null)
        } catch (error) {
            console.log(error)
        } finally {
            setSubmittingTestcase(false)
        }
    }

    function get_testcase_json() {
        axios
            .get(import.meta.env.VITE_API_URL + `/projects/get_testcases?id=${project_id}&practice=${isPractice ? 'true' : 'false'}${practiceProblemQuery}`, {
                headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` },
            })
            .then(res => {
                const rows = parseTestcaseRows(res.data)
                    .filter(testcase => testcase.id > 0)
                    .map(testcase => ({
                        name: testcase.name,
                        input: testcase.input,
                        output: testcase.output,
                        hidden: testcase.hidden,
                        order: testcase.order,
                    }))

                const fileContent = JSON.stringify(rows, null, 2)
                const fileName = ProjectName + '.json'
                const blob = new Blob([fileContent], { type: 'text/plain' })
                const url = URL.createObjectURL(blob)
                const link = document.createElement('a')
                link.href = url
                link.download = fileName
                document.body.appendChild(link)
                link.click()
                document.body.removeChild(link)
            })
            .catch(error => {
                console.error(error)
            })
    }

    const selectedTestCase = modalDraft
    const orderedTestcases = useMemo(
        () => testcases
            .filter(testcase => testcase.id > 0)
            .sort((a, b) => a.order - b.order || a.id - b.id),
        [testcases]
    )

    async function moveTestcase(testcaseId: number, direction: -1 | 1) {
        if (reorderingTestcases) return

        const currentIndex = orderedTestcases.findIndex(testcase => testcase.id === testcaseId)
        const nextIndex = currentIndex + direction
        if (currentIndex < 0 || nextIndex < 0 || nextIndex >= orderedTestcases.length) return

        const reordered = orderedTestcases.map(testcase => ({ ...testcase } as Testcase))
        const [moved] = reordered.splice(currentIndex, 1)
        reordered.splice(nextIndex, 0, moved)
        reordered.forEach((testcase, index) => {
            testcase.order = index + 1
        })

        const existingBlank = testcases.find(testcase => testcase.id === -1)
        const blank = existingBlank ? ({ ...existingBlank } as Testcase) : new Testcase()
        blank.id = -1
        blank.order = reordered.length + 1
        setTestcases([...reordered, blank])

        const formData = new FormData()
        formData.append('project_id', project_id.toString())
        formData.append('testcase_ids', JSON.stringify(reordered.map(testcase => testcase.id)))
        if (isPractice && practiceProblemId) {
            formData.append('checkpoint_id', String(practiceProblemId))
        }

        try {
            setReorderingTestcases(true)
            await axios.post(
                import.meta.env.VITE_API_URL + `/projects/reorder_testcases`,
                formData,
                { headers: { Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}` } }
            )
        } catch (error) {
            console.log(error)
            await reloadtests()
            window.alert('The testcase order could not be saved.')
        } finally {
            setReorderingTestcases(false)
        }
    }

    type DirEntry = { key: string; name: string; status: 'none' | 'add' | 'remove'; kind: 'server' | 'local' | 'other' }
    const baseName = (p: string) => (p || '').split(/[\\/]/).pop()!

    const serverNames = [
        ...(serverFiles ?? []),
        ...(serverSolutionFileNamesSnapshot ?? []),
        ...additionalFileNames,
        ...(serverDescFileName ? [serverDescFileName] : []),
    ].map(baseName)

    const localNames = [
        ...solutionFileNames,
        ...selectedAddFiles.map(f => f.name),
        ...(AssignmentDesc ? [AssignmentDesc.name] : []),
    ].map(baseName)

    const removedNames = removedAdditionalFiles.map(baseName)

    type Flag = { server: boolean; local: boolean; removed: boolean }
    const flags = new Map<string, Flag>()

    const mkEntry = (key: string, name: string, status: DirEntry['status'], kind: DirEntry['kind']): DirEntry => ({
        key,
        name,
        status,
        kind,
    })

    const mark = (names: string[], k: 'server' | 'local' | 'removed') => {
        names.forEach(n => {
            if (!n) return
            const cur = flags.get(n) ?? { server: false, local: false, removed: false }
            if (k === 'server') cur.server = true
            if (k === 'local') cur.local = true
            if (k === 'removed') cur.removed = true
            flags.set(n, cur)
        })
    }

    mark(serverNames, 'server')
    mark(localNames, 'local')
    mark(removedNames, 'removed')

    if (edit && AssignmentDesc && serverDescFileName) {
        mark([baseName(serverDescFileName)], 'removed')
    }

    if (edit && SolutionFiles.length > 0 && serverSolutionFileNamesSnapshot.length > 0) {
        mark(serverSolutionFileNamesSnapshot.map(baseName), 'removed')
    }

    const directoryEntries: DirEntry[] = Array.from(flags.entries())
        .flatMap<DirEntry>(([name, f]) => {
            if (f.server && f.local) {
                return [
                    mkEntry(`${name}__server_remove`, name, 'remove', 'server'),
                    mkEntry(`${name}__local_add`, name, 'add', 'local'),
                ]
            }
            if (f.local) return [mkEntry(`${name}__add`, name, 'add', 'local')]
            if (f.server || f.removed) {
                const status: DirEntry['status'] = f.removed ? 'remove' : 'none'
                return [mkEntry(`${name}__server`, name, status, 'server')]
            }
            return [mkEntry(`${name}__other`, name, 'none', 'other')]
        })
        .sort((a, b) => {
            const rank = (s: DirEntry['status']) => (s === 'remove' ? 0 : s === 'add' ? 1 : 2)
            return rank(a.status) - rank(b.status) || a.name.localeCompare(b.name) || a.kind.localeCompare(b.kind)
        })

    const fsUniqueJavaNames = Array.from(new Set(directoryEntries.map(e => e.name).filter(isJavaFileName)))
    const fsShowMainTag = ProjectLanguage === 'java' && fsUniqueJavaNames.length > 1 && !!mainJavaFileName
    const showFullScreenLoader = loadingProjectState || submittingProject || submittingTestcase || submittingJson
    const loaderMessage =
        (loadingProjectState && (isPractice ? 'Loading practice problem...' : 'Loading assignment...')) ||
        (submittingProject && (edit ? 'Saving assignment...' : 'Creating assignment...')) ||
        (submittingTestcase && 'Submitting test case...') ||
        (submittingJson && 'Uploading test cases...') ||
        'Loading...'

    return (
        <div>
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
                    { label: 'Class Selection', to: `/admin/school/${schoolId}/classes` },
                    { label: 'Admin Menu', to: `/admin/school/${schoolId}/class/${classId}/menu` },
                    { label: 'Module List', to: moduleListUrl },
                    {
                        label: 'Module Details',
                        to: project_id > 0 ? moduleOverviewUrl : moduleListUrl,
                    },
                    { label: 'Project Manage' },
                ]}
            />

            <div className="main-grid">
                <>
                    <div className={`admin-project-config-container${modalOpen ? ' blurred' : ''}`}>
                        <div className="pageTitle">{pageTitleText}</div>

                        <div className="step-menu-shell">
                            <div className="step-menu" role="tablist" aria-label="Project management pages">
                                <button
                                    type="button"
                                    role="tab"
                                    aria-selected={activeStep === 'files'}
                                    aria-controls="project-files-panel"
                                    className={`step-menu-item ${activeStep === 'files' ? 'active' : ''}${filesNeedSetup ? ' needs-setup' : ''}`}
                                    onClick={() => setActiveStep('files')}
                                >
                                    <span className="step-title">
                                        Files
                                        {filesNeedSetup && <span className="step-needs-indicator">Needs setup</span>}
                                    </span>
                                    <span className="step-state">{activeStep === 'files' ? 'Current page' : 'Go to page'}</span>
                                </button>
                                <button
                                    type="button"
                                    role="tab"
                                    aria-selected={activeStep === 'testcases'}
                                    aria-controls="project-testcases-panel"
                                    className={`step-menu-item ${activeStep === 'testcases' ? 'active' : ''}${testcasesNeedSetup ? ' needs-setup' : ''}`}
                                    onClick={() => setActiveStep('testcases')}
                                    title={
                                        !hasSavedProjectFiles
                                            ? 'Upload and save both the solution file(s) and assignment description first.'
                                            : testcasesNeedSetup
                                                ? 'Create or upload test cases to finish setup.'
                                                : undefined
                                    }
                                >
                                    <span className="step-title">
                                        Test Cases
                                        {testcasesNeedSetup && <span className="step-needs-indicator">Needs setup</span>}
                                    </span>
                                    <span className="step-state">{activeStep === 'testcases' ? 'Current page' : 'Go to page'}</span>
                                </button>
                            </div>
                        </div>

                        <div className="step-content">
                            {activeStep === 'files' && (
                                <div
                                    id="project-files-panel"
                                    role="tabpanel"
                                    className={`pane-project-settings wizard-step-${activeStep}`}
                                >
                                    <form className="form-project-settings">
                                        <div className="segment-main">
                                            {hasUnsavedProjectChanges && (
                                                <div className="unsaved-project-warning" role="status" aria-live="polite">
                                                    You have unsaved file or testcase settings. They will not be saved until you click "{SubmitButton}".
                                                </div>
                                            )}

                                            <div className="file-section">
                                                <div className="info-segment">
                                                    <h1 className="info-title">
                                                        {edit ? 'Preview or Change Solution Files' : 'Upload solution files'}
                                                    </h1>
                                                    <div
                                                        className="file-drop-area"
                                                        onDragOver={e => e.preventDefault()}
                                                        onDrop={e => {
                                                            e.preventDefault()
                                                            const files = e.dataTransfer.files
                                                            if (files && files.length > 0) {
                                                                handleSolutionFilesChange({ target: { files } } as any)
                                                            }
                                                        }}
                                                    >
                                                        {SolutionFiles.length === 0 && (!edit || serverSolutionFileNames.length === 0) ? (
                                                            <>
                                                                <input
                                                                    type="file"
                                                                    className="file-input"
                                                                    multiple
                                                                    accept={SOLUTION_ACCEPT}
                                                                    onChange={handleSolutionFilesChange}
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
                                                            <div className="file-preview">
                                                                {(() => {
                                                                    const shownNames =
                                                                        SolutionFiles.length > 0
                                                                            ? SolutionFiles.map(f => f.name)
                                                                            : serverSolutionFileNames
                                                                    const showMainTag =
                                                                        ProjectLanguage === 'java' &&
                                                                        shownNames.filter(isJavaFileName).length > 1 &&
                                                                        !!mainJavaFileName
                                                                    return (
                                                                        <>
                                                                            <button
                                                                                type="button"
                                                                                className="exchange-icon"
                                                                                title={
                                                                                    SolutionFiles.length > 0
                                                                                        ? 'Clear selected solution files'
                                                                                        : 'Replace server solution files'
                                                                                }
                                                                                aria-label={
                                                                                    SolutionFiles.length > 0
                                                                                        ? 'Clear selected solution files'
                                                                                        : 'Replace server solution files'
                                                                                }
                                                                                onClick={(e) => {
                                                                                    e.preventDefault()
                                                                                    if (SolutionFiles.length > 0) {
                                                                                        setSolutionFiles([])
                                                                                        setSolutionFileNames([])
                                                                                    } else if (edit && serverSolutionFileNames.length > 0) {
                                                                                        setServerSolutionFileNames([])
                                                                                    }
                                                                                }}
                                                                            >
                                                                                <FaExchangeAlt aria-hidden="true" />
                                                                            </button>

                                                                            <div
                                                                                className="file-preview-list"
                                                                                role={shownNames.length > 1 ? 'button' : undefined}
                                                                                tabIndex={shownNames.length > 1 ? 0 : undefined}
                                                                                title={
                                                                                    shownNames.length > 1
                                                                                        ? 'Click to preview ALL solution files'
                                                                                        : undefined
                                                                                }
                                                                                onClick={(e) => {
                                                                                    if (shownNames.length > 1) {
                                                                                        e.preventDefault()
                                                                                        openAllSolutionPreview()
                                                                                    }
                                                                                }}
                                                                                onKeyDown={(e) => {
                                                                                    if (shownNames.length > 1 && (e.key === 'Enter' || e.key === ' ')) {
                                                                                        e.preventDefault()
                                                                                        openAllSolutionPreview()
                                                                                    }
                                                                                }}
                                                                            >
                                                                                {shownNames.map((name) => (
                                                                                    <div key={name} className="file-preview-row solution-file-card">
                                                                                        <span className="file-icon-wrapper" aria-hidden="true">
                                                                                            <FaRegFile className="file-outline-icon" aria-hidden="true" />
                                                                                            {getFileIcon(name)}
                                                                                        </span>

                                                                                        {shownNames.length > 1 ? (
                                                                                            <span className="file-name">
                                                                                                {name}
                                                                                                {showMainTag && isJavaFileName(name) && name === mainJavaFileName && (
                                                                                                    <span className="main-indicator">Main</span>
                                                                                                )}
                                                                                            </span>
                                                                                        ) : (
                                                                                            <button
                                                                                                type="button"
                                                                                                className="file-name"
                                                                                                title="Click to preview"
                                                                                                onClick={(e) => {
                                                                                                    e.preventDefault()
                                                                                                    if (SolutionFiles.length > 0) {
                                                                                                        const file = SolutionFiles.find(x => x.name === name)
                                                                                                        if (file) openLocalPreview(file)
                                                                                                    } else {
                                                                                                        openServerPreview(name)
                                                                                                    }
                                                                                                }}
                                                                                            >
                                                                                                {name}
                                                                                                {showMainTag && isJavaFileName(name) && name === mainJavaFileName && (
                                                                                                    <span className="main-indicator">Main</span>
                                                                                                )}
                                                                                            </button>
                                                                                        )}
                                                                                    </div>
                                                                                ))}
                                                                            </div>
                                                                        </>
                                                                    )
                                                                })()}
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>

                                                <div className="info-segment">
                                                    <h1 className="info-title">
                                                        {edit
                                                            ? 'Download or Change Assignment Description File'
                                                            : 'Upload assignment description'}
                                                    </h1>
                                                    <div
                                                        className="file-drop-area"
                                                        onDragOver={e => e.preventDefault()}
                                                        onDrop={e => {
                                                            e.preventDefault()
                                                            const files = e.dataTransfer.files
                                                            if (files && files.length > 0) {
                                                                handleDescFileChange({ target: { files } } as any)
                                                            }
                                                        }}
                                                    >
                                                        {!descfileName ? (
                                                            <>
                                                                <input
                                                                    type="file"
                                                                    className="file-input"
                                                                    id="descFile"
                                                                    accept={DESC_ACCEPT}
                                                                    onChange={handleDescFileChange}
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
                                                            <div className="file-preview">
                                                                <button
                                                                    type="button"
                                                                    className="exchange-icon"
                                                                    onClick={() => {
                                                                        setDescFileName('')
                                                                        setDesc(undefined)
                                                                        const el = document.getElementById('descFile') as HTMLInputElement | null
                                                                        if (el) el.value = ''
                                                                    }}
                                                                >
                                                                    <FaExchangeAlt aria-hidden="true" />
                                                                </button>

                                                                <div className="file-preview-list">
                                                                    <div className="file-preview-row solution-file-card">
                                                                        <span className="file-icon-wrapper" aria-hidden="true">
                                                                            <FaRegFile className="file-outline-icon" aria-hidden="true" />
                                                                            {getFileIcon(descfileName)}
                                                                        </span>
                                                                        <button
                                                                            type="button"
                                                                            className="file-name"
                                                                            title="Click to download"
                                                                            onClick={(e) => {
                                                                                e.preventDefault()
                                                                                const lower = (descfileName || '').toLowerCase()
                                                                                if (/\.(pdf|docx?)$/.test(lower) && edit && !AssignmentDesc) {
                                                                                    downloadAssignmentDescription()
                                                                                }
                                                                            }}
                                                                        >
                                                                            {descfileName}
                                                                        </button>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>

                                                <div className="filesystem-segment">
                                                    <h1 className="info-title">Project Filesystem View</h1>
                                                    {directoryEntries.length > 0 ? (
                                                        <div className="directory-tree" role="tree" aria-label="Current directory">
                                                            <div className="tree-rail" aria-hidden="true"></div>
                                                            <ul className="tree-list">
                                                                {directoryEntries.map((entry) => {
                                                                    const name = entry.name
                                                                    const isAdded = !!selectedAddFiles.find(f => f.name === name)
                                                                    const isServer = additionalFileNames.includes(name)

                                                                    return (
                                                                        <li className="tree-row" role="treeitem" key={entry.key}>
                                                                            <span className="tree-icon" aria-hidden="true">
                                                                                <span className="fs-file-icon-wrapper">
                                                                                    <FaRegFile className="fs-file-outline-icon" aria-hidden="true" />
                                                                                    <span className="fs-file-language-icon" aria-hidden="true">
                                                                                        {getFileIcon(name)}
                                                                                    </span>
                                                                                </span>
                                                                            </span>
                                                                            <span className="tree-name">
                                                                                {name}
                                                                                {fsShowMainTag && isJavaFileName(name) && name === mainJavaFileName && (
                                                                                    <span className="main-indicator">Main</span>
                                                                                )}
                                                                            </span>
                                                                            {entry.status === 'remove' && (
                                                                                <span className="file-status removed">Submit changes to remove</span>
                                                                            )}
                                                                            {entry.status === 'add' && (
                                                                                <span className="file-status added">Submit changes to add</span>
                                                                            )}
                                                                            {isServer ? (
                                                                                <button
                                                                                    type="button"
                                                                                    className="tree-remove-button from-server"
                                                                                    onClick={() => removeServerAdditional(name)}
                                                                                >
                                                                                    Remove
                                                                                </button>
                                                                            ) : isAdded ? (
                                                                                <button
                                                                                    type="button"
                                                                                    className="tree-remove-button from-selected"
                                                                                    onClick={() => removeSelectedAdditional(name)}
                                                                                >
                                                                                    Remove
                                                                                </button>
                                                                            ) : null}
                                                                        </li>
                                                                    )
                                                                })}
                                                            </ul>
                                                        </div>
                                                    ) : (
                                                        <div className="filesystem-empty">No files yet.</div>
                                                    )}
                                                </div>

                                                <div className="feature-toggle-row single-column">
                                                    <div className={`feature-toggle-card additional-files-toggle-card ${showAdditionalFile ? 'enabled' : 'disabled'} full-width`}>
                                                        <button
                                                            type="button"
                                                            className="feature-toggle-button"
                                                            aria-pressed={showAdditionalFile}
                                                            onClick={() => setShowAdditionalFile(prev => !prev)}
                                                        >
                                                            <span className="feature-toggle-button-content">
                                                                <span className="feature-toggle-icon" aria-hidden="true">
                                                                    <FaFileAlt />
                                                                </span>
                                                                <span className="feature-toggle-copy">
                                                                    <span className="feature-toggle-heading-row">
                                                                        <span className="feature-toggle-label">Optional Additional Text File</span>
                                                                        <span className={`feature-toggle-state ${showAdditionalFile ? 'enabled' : 'disabled'}`}>
                                                                            {showAdditionalFile ? 'Enabled' : 'Disabled'}
                                                                        </span>
                                                                    </span>
                                                                    <span className="feature-toggle-description">
                                                                        Allow optional .txt support files to be uploaded with this project.
                                                                    </span>
                                                                </span>
                                                            </span>
                                                        </button>
                                                    </div>
                                                </div>

                                                {showAdditionalFile && (
                                                    <div className="info-segment optional-additional-file-segment">
                                                        <h1 className="info-title">Optional Additional Files</h1>
                                                        <div
                                                            className="file-drop-area optional-additional-file-drop"
                                                            onDragOver={e => e.preventDefault()}
                                                            onDrop={e => {
                                                                e.preventDefault()
                                                                const files = e.dataTransfer.files
                                                                if (files && files.length > 0) {
                                                                    handleAdditionalFileChange({ target: { files } } as any)
                                                                }
                                                            }}
                                                        >
                                                            <input
                                                                type="file"
                                                                className="file-input optional-additional-file-input"
                                                                multiple
                                                                accept={ADD_ACCEPT}
                                                                onChange={handleAdditionalFileChange}
                                                            />
                                                            <div className="file-drop-content">
                                                                <FaCloudUploadAlt className="file-drop-cloud-icon" aria-hidden="true" />
                                                                <div className="file-drop-message">
                                                                    Drag &amp; drop your file(s) here or{' '}
                                                                    <span className="browse-text">browse</span>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </div>
                                                )}
                                            </div>

                                            <button
                                                type="button"
                                                className="submit-button"
                                                onClick={
                                                    isPractice
                                                        ? handlePracticeFilesSubmit
                                                        : (edit ? handleEditSubmit : handleNewSubmit)
                                                }

                                                disabled={submittingProject}
                                            >
                                                {submittingProject ? (
                                                    <>
                                                        <FaCircleNotch className="spin" aria-hidden="true" />
                                                        &nbsp;{edit ? 'Saving...' : 'Creating...'}
                                                    </>
                                                ) : (
                                                    SubmitButton
                                                )}
                                            </button>
                                        </div>
                                    </form>
                                </div>
                            )}

                            {activeStep === 'testcases' && (
                                <div id="project-testcases-panel" role="tabpanel" className="pane-testcases">
                                    <div className="testcase-management-group">
                                        {!hasSavedProjectFiles ? (
                                            <div style={{ padding: 16 }}>
                                                Test cases are disabled until both the solution file(s) and assignment description are saved.
                                                <div style={{ marginTop: 12 }}>
                                                    Go to Step 1: Files, upload both required files, save the project,
                                                    then return to this step.
                                                </div>
                                            </div>
                                        ) : (
                                            <>
                                                <div className="form-testcases-overview">
                                                    <table className="testcases-table">
                                                        <thead>
                                                            <tr>
                                                                <th>Order</th>
                                                                <th>Name</th>
                                                                <th>Input</th>
                                                                <th>Output</th>
                                                                <th>Actions</th>
                                                            </tr>
                                                        </thead>
                                                        <tbody>
                                                            {orderedTestcases
                                                                .map((tc, index) => (
                                                                    <tr
                                                                        key={tc.id}
                                                                        className={tc.hidden ? 'hidden-testcase' : undefined}
                                                                        aria-label={tc.hidden ? 'Hidden test case' : undefined}
                                                                    >
                                                                        <td>
                                                                            <div className="testcase-order-controls">
                                                                                <span className="testcase-order-number">{index + 1}</span>
                                                                                <button
                                                                                    type="button"
                                                                                    className="testcase-order-button"
                                                                                    onClick={() => moveTestcase(tc.id, -1)}
                                                                                    disabled={index === 0 || reorderingTestcases}
                                                                                    aria-label={`Move ${tc.name} up`}
                                                                                    title="Move testcase up"
                                                                                >
                                                                                    <FaArrowUp aria-hidden="true" />
                                                                                </button>
                                                                                <button
                                                                                    type="button"
                                                                                    className="testcase-order-button"
                                                                                    onClick={() => moveTestcase(tc.id, 1)}
                                                                                    disabled={index === orderedTestcases.length - 1 || reorderingTestcases}
                                                                                    aria-label={`Move ${tc.name} down`}
                                                                                    title="Move testcase down"
                                                                                >
                                                                                    <FaArrowDown aria-hidden="true" />
                                                                                </button>
                                                                            </div>
                                                                        </td>
                                                                        <td>{tc.name}</td>
                                                                        <td>
                                                                            <pre className="testcase-input">{tc.input}</pre>
                                                                        </td>
                                                                        <td>
                                                                            <pre className="testcase-output"><InputTranscript text={tc.output} /></pre>
                                                                        </td>
                                                                        <td>
                                                                            <button
                                                                                type="button"
                                                                                className="testcase-edit-button"
                                                                                onClick={() => handleOpenModal(tc.id)}
                                                                            >
                                                                                <FaEdit aria-hidden="true" /> Edit
                                                                            </button>

                                                                            <div className="testcase-hidden-toggle">
                                                                                <span className="toggle-label">Hidden</span>
                                                                                <label className="switch">
                                                                                    <input
                                                                                        type="checkbox"
                                                                                        checked={!!tc.hidden}
                                                                                        onChange={(e) => setHiddenFromRow(tc, e.currentTarget.checked)}
                                                                                        aria-label="Toggle hidden test case"
                                                                                    />
                                                                                    <span className="slider" aria-hidden="true" />
                                                                                </label>
                                                                            </div>

                                                                        </td>
                                                                    </tr>
                                                                ))}
                                                            <tr>
                                                                <td colSpan={5} className="add-row-cell">
                                                                    <button
                                                                        type="button"
                                                                        className="add-testcase-button"
                                                                        onClick={() => handleOpenModal(-1)}
                                                                    >
                                                                        <FaPlusCircle aria-hidden="true" /> Add Test Case
                                                                    </button>
                                                                </td>
                                                            </tr>
                                                        </tbody>
                                                    </table>
                                                </div>

                                                <div className="export-json-segment">
                                                    <button
                                                        type="button"
                                                        className="export-json-button"
                                                        onClick={get_testcase_json}
                                                    >
                                                        <FaDownload aria-hidden="true" /> {getJSON}
                                                    </button>
                                                </div>

                                                <div className="or-separator">
                                                    <span>or</span>
                                                </div>

                                                <div className="upload-testcases-segment">
                                                    <h1 className="upload-title">Upload Test Cases</h1>
                                                    <div
                                                        className="file-drop-area"
                                                        onDragOver={e => e.preventDefault()}
                                                        onDrop={e => {
                                                            e.preventDefault()
                                                            const files = e.dataTransfer.files
                                                            if (files && files.length > 0) {
                                                                handleJsonFileChange({ target: { files } } as any)
                                                            }
                                                        }}
                                                    >
                                                        {!jsonfilename ? (
                                                            <>
                                                                <input
                                                                    id="jsonFile"
                                                                    type="file"
                                                                    className="file-input"
                                                                    accept=".json,application/json"
                                                                    onChange={handleJsonFileChange}
                                                                />
                                                                <div className="file-drop-content">
                                                                    <FaCloudUploadAlt className="file-drop-cloud-icon" aria-hidden="true" />
                                                                    <div className="file-drop-message">
                                                                        Drag &amp; drop your JSON file here or{' '}
                                                                        <span className="browse-text">browse</span>
                                                                    </div>
                                                                </div>
                                                            </>
                                                        ) : (
                                                            <div className="file-preview json-file-preview">
                                                                <span className="file-icon-wrapper" aria-hidden="true">
                                                                    <FaRegFile className="file-outline-icon" aria-hidden="true" />
                                                                    {getFileIcon(jsonfilename)}
                                                                </span>
                                                                <span className="file-name">{jsonfilename}</span>
                                                                <button
                                                                    type="button"
                                                                    className="exchange-icon"
                                                                    onClick={() => {
                                                                        setjsonfilename('')
                                                                        const jsonInput = document.getElementById('jsonFile') as HTMLInputElement | null
                                                                        if (jsonInput) jsonInput.value = ''
                                                                    }}
                                                                >
                                                                    <FaExchangeAlt aria-hidden="true" />
                                                                </button>
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>

                                                <div className="json-button-group">
                                                    <button
                                                        type="button"
                                                        className="json-submit-button"
                                                        onClick={handleJsonSubmit}
                                                        disabled={submittingJson}
                                                    >
                                                        {submittingJson ? (
                                                            <>
                                                                <FaCircleNotch className="spin" aria-hidden="true" />
                                                                &nbsp;Submitting...
                                                            </>
                                                        ) : (
                                                            <>
                                                                <FaUpload aria-hidden="true" /> {SubmitJSON}
                                                            </>
                                                        )}
                                                    </button>
                                                </div>
                                            </>
                                        )}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>

                    {previewOpen && (
                        <div className="preview-overlay">
                            <div className="preview-modal">
                                <div className="preview-header">
                                    <strong>{previewTitle}</strong>
                                    <button
                                        type="button"
                                        className="preview-close-button"
                                        onClick={() => {
                                            setPreviewOpen(false)
                                        }}
                                    >
                                        &times;
                                    </button>
                                </div>

                                {serverFiles && (
                                    <div className="preview-file-list">
                                        {serverFiles.length === 0 ? (
                                            <div>No previewable files.</div>
                                        ) : (
                                            <ul>
                                                {serverFiles.map(fp => (
                                                    <li key={fp}>
                                                        <button
                                                            type="button"
                                                            className="preview-file-link"
                                                            onClick={(e) => {
                                                                e.preventDefault()
                                                                e.stopPropagation()
                                                                openServerPreview(fp)
                                                            }}
                                                        >
                                                            {fp}
                                                        </button>
                                                    </li>
                                                ))}
                                            </ul>
                                        )}
                                    </div>
                                )}

                                <div className="preview-body">
                                    {previewText || (!serverFiles ? 'No content loaded.' : 'Pick a file above.')}
                                </div>
                            </div>
                        </div>
                    )}

                    {modalOpen && (
                        <>
                            <div
                                className="modal-overlay"
                                onClick={() => {
                                    setModalOpen(false)
                                    setModalDraft(null)
                                }}
                            />
                            <div className="testcase-modal">
                                <div className="modal-header">
                                    <div className="modal-title">Test Case Information</div>
                                    <button
                                        type="button"
                                        className="modal-close-button"
                                        aria-label="Close test case modal"
                                        onClick={() => {
                                            setModalOpen(false)
                                            setModalDraft(null)
                                        }}
                                    >
                                        ✕
                                    </button>
                                </div>
                                <div className="modal-content">
                                    <form>
                                        <div className="form-field modal-input">
                                            <label>Test Case Name</label>
                                            <textarea
                                                className="modal-textarea"
                                                rows={1}
                                                value={selectedTestCase?.name || ''}
                                                onChange={e => handleNameChange(selectedTestCaseId!, e.currentTarget.value)}
                                            />
                                        </div>

                                        <div className="form-field modal-textarea">
                                            <label>Input</label>
                                            <textarea
                                                className="modal-textarea"
                                                rows={1}
                                                value={selectedTestCase?.input || ''}
                                                onChange={e => handleInputChange(selectedTestCaseId!, e.currentTarget.value)}
                                            />
                                        </div>

                                        <div className="form-field modal-textarea">
                                            <label>Output</label>
                                            <div
                                                className="testcase-output-preview"
                                                role="textbox"
                                                aria-readonly="true"
                                                tabIndex={0}
                                            >
                                                <InputTranscript text={selectedTestCase?.output || ''} />
                                            </div>
                                        </div>

                                        <div className="form-field modal-checkbox">
                                            <label>
                                                <input
                                                    type="checkbox"
                                                    checked={!!selectedTestCase?.hidden}
                                                    onChange={(e) => handleHiddenChange(selectedTestCaseId!, e.currentTarget.checked)}
                                                />
                                                Hidden test case
                                            </label>
                                        </div>

                                        <div className="modal-action-buttons">
                                            <button
                                                type="button"
                                                className="modal-submit-button"
                                                onClick={() => buttonhandleClick(selectedTestCaseId!)}
                                                disabled={submittingTestcase}
                                            >
                                                {submittingTestcase ? (
                                                    <>
                                                        <FaCircleNotch className="spin" aria-hidden="true" />
                                                        &nbsp;Submitting...
                                                    </>
                                                ) : selectedTestCaseId === -1 ? (
                                                    'Submit new testcase'
                                                ) : (
                                                    'Submit changes'
                                                )}
                                            </button>

                                            {selectedTestCaseId !== -1 && (
                                                <button
                                                    type="button"
                                                    className="modal-trash-button"
                                                    onClick={() => buttonhandleTrashClick(selectedTestCaseId!)}
                                                >
                                                    Remove testcase
                                                </button>
                                            )}
                                        </div>
                                    </form>
                                </div>
                            </div>
                        </>
                    )}
                </>
            </div>
            <LoadingAnimation show={showFullScreenLoader} message={loaderMessage} />
        </div>
    )
}

export default AdminProjectManage