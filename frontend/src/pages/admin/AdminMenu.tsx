// AdminMenu.tsx: Renders the admin menu interface and coordinates its local data and interactions.
import { ReactElement, KeyboardEvent, useEffect, useState } from "react";
import axios from "axios";
import { Link, useParams } from "react-router-dom";
import { Helmet } from "react-helmet";
import {
    FaChartLine,
    FaChevronRight,
    FaListUl,
    FaEye,
    FaUpload,
    FaUsers,
} from "react-icons/fa";

import MenuComponent from "../components/MenuComponent";
import DirectoryBreadcrumbs from "../components/DirectoryBreadcrumbs";
import "../../styling/Selection.scss";
import "../../styling/AdminMenu.scss";

// Describes the admin menu option data expected by this file.
type AdminMenuOption = {
    title: string;
    description: string;
    to?: string;
    icon: ReactElement;
    actionLabel: string;
    disabled?: boolean;
};

// Describes the class access response data expected by this file.
type ClassAccessResponse = {
    id?: number;
    name?: string;
    school_id?: number;
    school_name?: string;
};

// Renders the admin menu interface and coordinates its local data and interactions.
export default function AdminMenu() {
    // Reads the school, class, or assignment identifiers from the current route.
    const { school_id, class_id } = useParams<{
        school_id: string;
        class_id: string;
    }>();

    const schoolId = school_id || "";
    const classId = class_id || "";
    // Keeps the values that drive this component’s display and user interactions in React state.
    const [className, setClassName] = useState("");

    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (!schoolId || !classId) {
            setClassName("");
            return;
        }

        axios
            .get<ClassAccessResponse>(
                import.meta.env.VITE_API_URL +
                `/classes/validate_class_access/${classId}?school_id=${schoolId}&role_context=admin`,
                {
                    headers: {
                        Authorization: `Bearer ${localStorage.getItem("AUTOTA_AUTH_TOKEN")}`,
                    },
                },
            )
            .then((res) => {
                setClassName(res.data?.name || "");
            })
            .catch((err) => {
                console.error(err);
                setClassName("");
            });
    }, [schoolId, classId]);

    const moduleListPath = `/admin/school/${schoolId}/class/${classId}/modules`;
    const analyticsPath = `/admin/school/${schoolId}/class/${classId}/analytics`;
    const adminUploadPath = `/admin/school/${schoolId}/class/${classId}/upload`;
    const officeHoursPath = `/admin/school/${schoolId}/class/${classId}/office-hours`;
    const studentPreviewPath = `/admin/school/${schoolId}/class/${classId}/student-preview`;

    const menuOptions: AdminMenuOption[] = [
        {
            title: "Module List",
            description: "Create, edit, and open class modules",
            to: moduleListPath,
            icon: <FaListUl aria-hidden="true" />,
            actionLabel: "Open Module List",
        },
        {
            title: "Analytics Dashboard",
            description: "See every student's progress across every assignment and checkpoint",
            to: analyticsPath,
            icon: <FaChartLine aria-hidden="true" />,
            actionLabel: "Open Analytics",
        },
        {
            title: "Student View Preview",
            description: "Demonstrate student submissions, cooldowns, and feedback as the Test User",
            to: studentPreviewPath,
            icon: <FaEye aria-hidden="true" />,
            actionLabel: "Preview Student View",
        },
        {
            title: "Office Hours",
            description: "View the in-person queue and start 30-minute help sessions",
            to: officeHoursPath,
            icon: <FaUsers aria-hidden="true" />,
            actionLabel: "Open Office Hours",
        },
        {
            title: "Admin Upload",
            description: "Upload and manage admin files for this class",
            to: adminUploadPath,
            icon: <FaUpload aria-hidden="true" />,
            actionLabel: "Open Admin Upload",
        },
    ];

    // Handles card key down for this view.
    const handleCardKeyDown = (
        event: KeyboardEvent<HTMLElement>,
        option: AdminMenuOption,
    ) => {
        if (option.disabled || !option.to) return;
        if (event.key !== "Enter" && event.key !== " ") return;

        event.preventDefault();
        window.location.href = option.to;
    };

    // Renders the interface using the current data and interaction state.
    return (
        <div className="projects-page admin-landing-root admin-menu-page">
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
                    { label: "School Selection", to: "/schools" },
                    {
                        label: "Class Selection",
                        to: schoolId
                            ? `/admin/school/${schoolId}/classes`
                            : "/schools",
                    },
                    { label: "Admin Menu" },
                ]}
                trailingSeparator={true}
            />

            <div className="pageTitle">
                {className ? `${className} Admin Menu` : "Admin Menu"}
            </div>

            <section className="admin-menu-shell" aria-label="Admin menu">
                <div className="admin-menu-header-row">
                    <div>
                        <h2>Class Admin Options</h2>
                    </div>
                </div>

                <div className="admin-menu-grid">
                    {menuOptions.map((option) => {
                        const cardClasses = [
                            "admin-menu-card",
                            option.disabled ? "is-disabled" : "",
                        ]
                            .join(" ")
                            .trim();

                        const cardContent = (
                            <>
                                <div className="admin-menu-card-main">
                                    <div className="admin-menu-icon">
                                        {option.icon}
                                    </div>

                                    <div className="admin-menu-card-title-row">
                                        <h3>{option.title}</h3>
                                    </div>

                                    <p className="admin-menu-card-description">
                                        {option.description}
                                    </p>
                                </div>

                                <div className="admin-menu-card-actions">
                                    <span
                                        className={`admin-menu-action ${option.disabled
                                            ? "admin-menu-action-secondary admin-menu-disabled-action"
                                            : "admin-menu-action-primary"
                                            }`}
                                    >
                                        {option.actionLabel}
                                        {!option.disabled ? <FaChevronRight aria-hidden="true" /> : null}
                                    </span>
                                </div>
                            </>
                        );

                        if (option.disabled || !option.to) {
                            // Renders the interface using the current data and interaction state.
                            return (
                                <article
                                    className={cardClasses}
                                    key={option.title}
                                    aria-label={`${option.title} is coming soon`}
                                    aria-disabled="true"
                                >
                                    {cardContent}
                                </article>
                            );
                        }

                        // Renders the interface using the current data and interaction state.
                        return (
                            <Link
                                className={cardClasses}
                                key={option.title}
                                to={option.to}
                                role="button"
                                onKeyDown={(event) => handleCardKeyDown(event, option)}
                                aria-label={`Open ${option.title}`}
                            >
                                {cardContent}
                            </Link>
                        );
                    })}
                </div>
            </section>
        </div>
    );
}
