// AdminClassSelect.tsx: Renders the admin class select interface and coordinates its local data and interactions.
import { Component, KeyboardEvent } from 'react'
import axios from 'axios'
import { Link, NavigateFunction, useNavigate, useParams } from 'react-router-dom'
import { Helmet } from 'react-helmet'
import MenuComponent from '../components/MenuComponent'
import '../../styling/Selection.scss'
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs"

// Describes the class object data expected by this file.
interface ClassObject {
    Id: number
    Name: string
}

// Describes the school object data expected by this file.
interface SchoolObject {
    Id: number
    Name: string
}

// Describes the class list response data expected by this file.
interface ClassListResponse {
    school?: {
        id: number
        name: string
    }
    classes?: Array<{
        id: number
        name: string
    }>
}

// Describes the class state data expected by this file.
interface ClassState {
    classes: Array<ClassObject>
    selectedSchoolId: number
    selectedSchoolName: string
    errorMessage: string
    isLoading: boolean
}

// Describes the admin class select props data expected by this file.
interface AdminClassSelectProps {
    schoolIdFromUrl: string
    navigate: NavigateFunction
}

class AdminClassSelectInner extends Component<AdminClassSelectProps, ClassState> {
    constructor(props: AdminClassSelectProps) {
        super(props)
        this.state = {
            classes: [],
            selectedSchoolId: -1,
            selectedSchoolName: "",
            errorMessage: "",
            isLoading: true
        }
    }

    componentDidMount() {
        this.loadSchoolAndClassesFromUrl()
    }

    componentDidUpdate(prevProps: AdminClassSelectProps) {
        if (prevProps.schoolIdFromUrl === this.props.schoolIdFromUrl) return
        this.loadSchoolAndClassesFromUrl()
    }

    loadSchoolAndClassesFromUrl = () => {
        const schoolId = Number(this.props.schoolIdFromUrl)

        if (!schoolId || Number.isNaN(schoolId)) {
            this.props.navigate("/schools", { replace: true })
            return
        }

        this.loadSchoolAndClasses(schoolId)
    }

    loadSchoolAndClasses = (schoolId: number) => {
        this.setState({
            classes: [],
            selectedSchoolId: schoolId,
            selectedSchoolName: "",
            errorMessage: "",
            isLoading: true
        })

        axios
            .get(import.meta.env.VITE_API_URL + `/classes/get_classes_and_ids?school_id=${schoolId}&include_school=true`, {
                headers: {
                    Authorization: `Bearer ${localStorage.getItem('AUTOTA_AUTH_TOKEN')}`
                }
            })
            .then(res => {
                const payload: ClassListResponse = res.data || {}
                const schoolObj: SchoolObject = {
                    Id: payload.school?.id || schoolId,
                    Name: payload.school?.name || ""
                }
                const classRows = Array.isArray(payload.classes) ? payload.classes : []
                const classes: ClassObject[] = classRows.map(
                    (obj: { id: number; name: string }) => ({
                        Id: obj.id,
                        Name: obj.name
                    })
                )

                classes.sort((a: ClassObject, b: ClassObject) =>
                    a.Name.localeCompare(b.Name)
                )

                this.setState({
                    classes,
                    selectedSchoolId: schoolObj.Id,
                    selectedSchoolName: schoolObj.Name,
                    errorMessage: "",
                    isLoading: false
                })
            })
            .catch(err => {
                console.error(err)

                if (err.response && (err.response.status === 403 || err.response.status === 404)) {
                    this.props.navigate("/schools", { replace: true })
                    return
                }

                this.setState({
                    classes: [],
                    selectedSchoolId: schoolId,
                    selectedSchoolName: "",
                    errorMessage: "Could not load classes for the selected school.",
                    isLoading: false
                })
            })
    }

    handleClassCardKeyDown = (event: KeyboardEvent<HTMLElement>, classObj: ClassObject) => {
        if (event.key !== "Enter" && event.key !== " ") return

        event.preventDefault()
        this.props.navigate(this.getClassMenuUrl(classObj.Id))
    }

    getClassMenuUrl = (classId: number): string => {
        return `/admin/school/${this.state.selectedSchoolId}/class/${classId}/menu`
    }

    render() {
        const { classes, selectedSchoolId, selectedSchoolName, errorMessage, isLoading } = this.state
        const hasSelectedSchool = selectedSchoolId !== -1

        // Renders the interface using the current data and interaction state.
        return (
            <div className="projects-page admin-landing-root">
                {/* Sets the page title and document metadata. */}
                <Helmet>
                    <title>[Admin] MAAT</title>
                </Helmet>

                {/* Displays the navigation and actions available on this page. */}
                <MenuComponent
                ></MenuComponent>

                {/* Shows the current location and links back to parent pages. */}
                <DirectoryBreadcrumbs
                    items={[
                        { label: "School Selection", to: "/schools" },
                        { label: "Class Selection" }
                    ]}
                    trailingSeparator={true}
                />

                <div className="pageTitle">
                    {hasSelectedSchool && selectedSchoolName ? `Admin · ${selectedSchoolName}` : "Admin Class Selection"}
                </div>

                <section className="module-list-shell" aria-label="Class list">
                    <div className="module-list-header-row">
                        <div>
                            <h2>{selectedSchoolName} Classes</h2>
                        </div>
                    </div>

                    {errorMessage ? <div className="pageMessage">{errorMessage}</div> : null}

                    {!hasSelectedSchool ? (
                        <Link
                            to="/schools"
                            className="project-action project-action-secondary"
                        >
                            Go to School Selection
                        </Link>
                    ) : null}

                    {isLoading && hasSelectedSchool ? (
                        <div className="empty-projects">Loading classes...</div>
                    ) : null}

                    {!isLoading && hasSelectedSchool && classes.length > 0 ? (
                        <div className="module-list-grid">
                            {classes.map((classObj: ClassObject) => (
                                <article
                                    className="module-list-card"
                                    key={classObj.Id}
                                    role="button"
                                    tabIndex={0}
                                    onClick={() => this.props.navigate(this.getClassMenuUrl(classObj.Id))}
                                    onKeyDown={(event) => this.handleClassCardKeyDown(event, classObj)}
                                    aria-label={`Open ${classObj.Name}`}
                                >
                                    <div className="module-list-card-main">
                                        <div className="module-list-card-title-row">
                                            <h3>{classObj.Name}</h3>
                                        </div>
                                    </div>

                                    <div className="module-list-card-actions">
                                        <Link
                                            to={this.getClassMenuUrl(classObj.Id)}
                                            className="project-action project-action-primary"
                                            onClick={(event) => event.stopPropagation()}
                                        >
                                            Open Admin Menu
                                        </Link>
                                    </div>
                                </article>
                            ))}
                        </div>
                    ) : null}

                    {!isLoading && hasSelectedSchool && classes.length === 0 && !errorMessage ? (
                        <div className="empty-projects">
                            No classes are currently available for this school.
                        </div>
                    ) : null}
                </section>
            </div>
        )
    }
}

// Renders the admin class select interface and coordinates its local data and interactions.
export default function AdminClassSelect() {
    const navigate = useNavigate()
    // Reads the school, class, or assignment identifiers from the current route.
    const { school_id } = useParams<{ school_id: string }>()

    // Renders the interface using the current data and interaction state.
    return <AdminClassSelectInner navigate={navigate} schoolIdFromUrl={school_id || ""} />
}
