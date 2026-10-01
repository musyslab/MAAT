// SchoolSelect.tsx: Renders the school select interface and coordinates its local data and interactions.
import { Component, KeyboardEvent } from 'react'
import axios from 'axios'
import { NavigateFunction, useNavigate } from 'react-router-dom'
import { Helmet } from 'react-helmet'
import MenuComponent from '../components/MenuComponent'
import '../../styling/Selection.scss'
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs"

// Describes the school object data expected by this file.
interface SchoolObject {
    Id: number
    Name: string
}

// Describes the class object data expected by this file.
interface ClassObject {
    id: number
    name: string
    school_id?: number | null
    school_name?: string
}

// Describes the school state data expected by this file.
interface SchoolState {
    teachingSchools: Array<SchoolObject>
    studentSchools: Array<SchoolObject>
    errorMessage: string
}

// Describes the school select props data expected by this file.
interface SchoolSelectProps {
    navigate: NavigateFunction
}

// Reads the stored login token and rejects placeholder or missing values.
const getValidStoredToken = (): string | null => {
    const token = localStorage.getItem("AUTOTA_AUTH_TOKEN")

    if (!token) {
        return null
    }

    const cleanedToken = token.trim()

    if (
        !cleanedToken ||
        cleanedToken.toLowerCase() === "null" ||
        cleanedToken.toLowerCase() === "undefined"
    ) {
        localStorage.removeItem("AUTOTA_AUTH_TOKEN")
        return null
    }

    return cleanedToken
}

// Clears stored auth and redirect to login for this view.
const clearStoredAuthAndRedirectToLogin = () => {
    localStorage.removeItem("AUTOTA_AUTH_TOKEN")
    window.location.replace("/login")
}

// Groups the available class records into school choices.
const buildSchoolsFromClasses = (classes: Array<ClassObject>): Array<SchoolObject> => {
    const schoolsById = new Map<number, SchoolObject>()

    classes.forEach((classObj) => {
        const schoolId = Number(classObj.school_id || 0)

        if (schoolId <= 0 || schoolsById.has(schoolId)) {
            return
        }

        schoolsById.set(schoolId, {
            Id: schoolId,
            Name: classObj.school_name || `School ${schoolId}`
        })
    })

    return Array.from(schoolsById.values()).sort((a: SchoolObject, b: SchoolObject) =>
        a.Name.localeCompare(b.Name)
    )
}

// Extracts class rows from the returned response format.
const readClassRows = (data: any): Array<ClassObject> => {
    if (Array.isArray(data)) {
        return data
    }

    if (Array.isArray(data?.classes)) {
        return data.classes
    }

    return []
}

class SchoolSelectInner extends Component<SchoolSelectProps, SchoolState> {
    constructor(props: SchoolSelectProps) {
        super(props)
        this.state = {
            teachingSchools: [],
            studentSchools: [],
            errorMessage: ""
        }
    }

    componentDidMount() {
        const token = getValidStoredToken()

        if (!token) {
            clearStoredAuthAndRedirectToLogin()
            return
        }

        const headers = {
            Authorization: `Bearer ${token}`
        }

        // Fetches the server data needed for this operation.
        const teachingRequest = axios.get(
            import.meta.env.VITE_API_URL + `/classes/get_classes_and_ids?include_school=true&role_context=admin`,
            { headers }
        )

        // Fetches the server data needed for this operation.
        const studentRequest = axios.get(
            import.meta.env.VITE_API_URL + `/classes/get_classes_and_ids?include_school=true&role_context=student`,
            { headers }
        )

        Promise.allSettled([teachingRequest, studentRequest])
            .then(([teachingResult, studentResult]) => {
                const failures = [teachingResult, studentResult].filter(
                    (result) => result.status === "rejected"
                ) as Array<PromiseRejectedResult>

                const authFailure = failures.some((result) => {
                    const status = result.reason?.response?.status
                    return status === 401 || status === 422
                })

                if (authFailure) {
                    clearStoredAuthAndRedirectToLogin()
                    return
                }

                const teachingClasses = teachingResult.status === "fulfilled"
                    ? readClassRows(teachingResult.value.data)
                    : []

                const studentClasses = studentResult.status === "fulfilled"
                    ? readClassRows(studentResult.value.data)
                    : []

                this.setState({
                    teachingSchools: buildSchoolsFromClasses(teachingClasses),
                    studentSchools: buildSchoolsFromClasses(studentClasses),
                    errorMessage: failures.length === 2 ? "Could not load your schools." : ""
                })
            })
            .catch(err => {
                console.error(err)
                this.setState({ errorMessage: "Could not load your schools." })
            })
    }

    handleSchoolSelect = (schoolObj: SchoolObject, roleContext: "admin" | "student") => {
        const basePath = roleContext === "admin" ? "/admin" : "/student"
        this.props.navigate(`${basePath}/school/${schoolObj.Id}/classes`)
    }

    handleSchoolCardKeyDown = (
        event: KeyboardEvent<HTMLElement>,
        schoolObj: SchoolObject,
        roleContext: "admin" | "student"
    ) => {
        if (event.key !== "Enter" && event.key !== " ") return

        event.preventDefault()
        this.handleSchoolSelect(schoolObj, roleContext)
    }

    renderSchoolSection(
        title: string,
        schools: Array<SchoolObject>,
        roleContext: "admin" | "student",
        buttonLabel: string
    ) {
        if (schools.length <= 0) {
            return null
        }

        // Renders the interface using the current data and interaction state.
        return (
            <section className="module-list-shell" aria-label={title}>
                <div className="module-list-header-row">
                    <div>
                        <h2>{title}</h2>
                    </div>
                </div>

                <div className="module-list-grid">
                    {schools.map((schoolObj: SchoolObject) => (
                        <article
                            className="module-list-card"
                            key={`${roleContext}-${schoolObj.Id}`}
                            role="button"
                            tabIndex={0}
                            onClick={() => this.handleSchoolSelect(schoolObj, roleContext)}
                            onKeyDown={(event) => this.handleSchoolCardKeyDown(event, schoolObj, roleContext)}
                            aria-label={`Open ${schoolObj.Name}`}
                        >
                            <div className="module-list-card-main">
                                <div className="module-list-card-title-row">
                                    <h3>{schoolObj.Name}</h3>
                                </div>
                            </div>

                            <div className="module-list-card-actions">
                                <button
                                    type="button"
                                    className="project-action project-action-primary"
                                    onClick={(event) => {
                                        event.stopPropagation()
                                        this.handleSchoolSelect(schoolObj, roleContext)
                                    }}
                                >
                                    {buttonLabel}
                                </button>
                            </div>
                        </article>
                    ))}
                </div>
            </section>
        )
    }

    render() {
        const { teachingSchools, studentSchools, errorMessage } = this.state
        const hasTeachingSchools = teachingSchools.length > 0
        const hasStudentSchools = studentSchools.length > 0
        const hasAnySchools = hasTeachingSchools || hasStudentSchools

        // Renders the interface using the current data and interaction state.
        return (
            <div className="projects-page admin-landing-root">
                {/* Sets the page title and document metadata. */}
                <Helmet>
                    <title>MAAT</title>
                </Helmet>

                {/* Displays the navigation and actions available on this page. */}
                <MenuComponent
                ></MenuComponent>

                {/* Shows the current location and links back to parent pages. */}
                <DirectoryBreadcrumbs
                    items={[
                        { label: "School Selection" }
                    ]}
                    trailingSeparator={true}
                />

                <div className="pageTitle">School Selection</div>


                {errorMessage ? <div className="pageMessage">{errorMessage}</div> : null}

                {hasTeachingSchools
                    ? this.renderSchoolSection(
                        "Admin Schools",
                        teachingSchools,
                        "admin",
                        "Open as Teacher"
                    )
                    : null}

                {hasStudentSchools
                    ? this.renderSchoolSection(
                        "Student Schools",
                        studentSchools,
                        "student",
                        "Open as Student"
                    )
                    : null}

                {!hasAnySchools && !errorMessage ? (
                    <div className="empty-projects school-selection-empty-state">
                        No schools are available for your account yet.
                    </div>
                ) : null}
            </div>
        )
    }
}

// Renders the school select interface and coordinates its local data and interactions.
export default function SchoolSelect() {
    const navigate = useNavigate()
    // Renders the interface using the current data and interaction state.
    return <SchoolSelectInner navigate={navigate} />
}
