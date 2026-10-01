// AdminViewStudentCode.tsx: Loads the selected submission for the code and test-case viewer.
// frontend/src/pages/admin/AdminViewStudentCode.tsx
import { useEffect, useState } from 'react'
import axios from 'axios'
import { useLocation, useParams } from 'react-router-dom'
import { Helmet } from 'react-helmet'
import MenuComponent from '../components/MenuComponent'
import DirectoryBreadcrumbs from '../components/DirectoryBreadcrumbs'
import DiffView from '../components/CodeDiffView'

const defaultpagenumber = -1

// Loads the selected submission for the code and test-case viewer.
export function AdminViewStudentCode() {

    const { search } = useLocation()
    // Reads the school, class, or assignment identifiers from the current route.
    const { id, school_id, class_id, module_id, project_id, checkpoint_id: route_checkpoint_id } = useParams<{
        id?: string
        school_id?: string
        class_id?: string
        module_id?: string
        project_id?: string
        checkpoint_id?: string
    }>()

    const submissionId = id !== undefined ? parseInt(id, 10) : defaultpagenumber
    const cid = class_id !== undefined ? parseInt(class_id, 10) : -1
    const pid = project_id !== undefined ? parseInt(project_id, 10) : -1

    // Keeps the values that drive this component’s display and user interactions in React state.
    const [studentName, setStudentName] = useState<string>('')
    const [projectDisplayName, setProjectDisplayName] = useState<string>('')

    const params = new URLSearchParams(search)
    const fromParam = (params.get('from') || '').toLowerCase()
    const fromOfficeHours = fromParam === 'office-hours'
    const fromAdminUpload = fromParam === 'admin-upload'
    const fromAnalytics = fromParam === 'analytics'
    const parsedCheckpointId = Number(route_checkpoint_id)
    const checkpointId = Number.isInteger(parsedCheckpointId) && parsedCheckpointId > 0
        ? parsedCheckpointId
        : undefined
    const isCheckpoint = checkpointId !== undefined

    const schoolIdStr = school_id ?? ''
    const classIdStr = class_id ?? ''
    const moduleIdStr = module_id ?? ''
    const projectIdStr = project_id ?? ''

    const hasClassDirectoryPath = !!schoolIdStr && !!classIdStr
    const hasFullDirectoryPath = !!schoolIdStr && !!classIdStr && !!moduleIdStr && !!projectIdStr

    const classSelectionUrl = hasClassDirectoryPath ? `/admin/school/${schoolIdStr}/classes` : '/schools'
    const adminMenuUrl = `/admin/school/${schoolIdStr}/class/${classIdStr}/menu`
    const adminUploadUrl = `/admin/school/${schoolIdStr}/class/${classIdStr}/upload`
    const analyticsDashboardUrl = `/admin/school/${schoolIdStr}/class/${classIdStr}/analytics`
    const moduleListUrl = `/admin/school/${schoolIdStr}/class/${classIdStr}/modules`
    const moduleDetailsUrl = `/admin/school/${schoolIdStr}/class/${classIdStr}/module/${moduleIdStr}/overview`
    const studentListUrl = isCheckpoint && checkpointId
        ? `/admin/school/${schoolIdStr}/class/${classIdStr}/module/${moduleIdStr}/project/${projectIdStr}/checkpoint/${checkpointId}/submissions`
        : `/admin/school/${schoolIdStr}/class/${classIdStr}/module/${moduleIdStr}/project/${projectIdStr}/submissions`

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (submissionId < 0 || pid < 0) return
        axios
            .post(
                `${import.meta.env.VITE_API_URL}/submissions/recentsubproject`,
                {
                    project_id: pid,
                    checkpoint: isCheckpoint,
                    checkpoint_id: checkpointId ?? null,
                },
                {
                    headers: {
                        Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                    },
                }
            )
            .then((res) => {
                const data = res.data
                const entry = Object.entries(data).find(
                    ([_, value]) => parseInt((value as Array<string>)[7], 10) === submissionId
                )
                if (entry) {
                    const studentData = entry[1] as Array<string>
                    setStudentName(`${studentData[1]} ${studentData[0]}`)
                }
            })
            .catch((err) => console.log(err))
    }, [submissionId, pid, isCheckpoint, checkpointId])

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (pid < 0) return
        axios
            .get(
                `${import.meta.env.VITE_API_URL}/assignment_tracking/get_project?id=${pid}${isCheckpoint && checkpointId ? `&checkpoint_id=${checkpointId}` : ''
                }`,
                {
                    headers: {
                        Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`,
                    },
                }
            )
            .then((res) => {
                const projectInfo = res.data as Record<string, unknown[]>;
                setProjectDisplayName(String(projectInfo[pid]?.[0] ?? '').trim());
            })
            .catch((err) => console.log(err))
    }, [pid, isCheckpoint, checkpointId])

    // Renders the interface using the current data and interaction state.
    return (
        <div className="page-container" id="admin-view-student-code">
            {/* Sets the page title and document metadata. */}
            <Helmet>
                <title>MAAT</title>
            </Helmet>

            {/* Displays the navigation and actions available on this page. */}
            <MenuComponent
            />

            {/* Shows the current location and links back to parent pages. */}
            <DirectoryBreadcrumbs
                items={[
                    { label: 'School Selection', to: '/schools' },
                    ...(fromOfficeHours
                        ? [{ label: 'Office Hours', to: `/admin/school/${school_id}/class/${class_id}/office-hours` }]
                        : fromAnalytics && hasClassDirectoryPath
                            ? [
                                { label: 'Class Selection', to: classSelectionUrl },
                                { label: 'Admin Menu', to: adminMenuUrl },
                                { label: 'Analytics Dashboard', to: analyticsDashboardUrl },
                            ]
                            : fromAdminUpload && hasClassDirectoryPath
                                ? [
                                    { label: 'Class Selection', to: classSelectionUrl },
                                    { label: 'Admin Menu', to: adminMenuUrl },
                                    { label: 'Admin Upload', to: adminUploadUrl },
                                ]
                                : fromAdminUpload || !hasFullDirectoryPath
                                    ? [{ label: 'Admin Upload', to: '/schools' }]
                                    : [
                                        { label: 'Class Selection', to: classSelectionUrl },
                                        { label: 'Admin Menu', to: adminMenuUrl },
                                        { label: 'Module List', to: moduleListUrl },
                                        { label: 'Module Details', to: moduleDetailsUrl },
                                        {
                                            label: 'Student List',
                                            to: studentListUrl,
                                        },
                                    ]),
                    { label: 'Code View' },
                ]}
            />

            <div className="pageTitle">
                {(projectDisplayName || (isCheckpoint ? 'Checkpoint Submission' : 'Main Submission'))}: {studentName || 'Unknown Student'}
            </div>

            <DiffView submissionId={submissionId} classId={cid} isPractice={isCheckpoint} practiceProblemId={checkpointId} revealHiddenOutput />
        </div>
    )
}

export default AdminViewStudentCode
