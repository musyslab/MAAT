import { KeyboardEvent, useEffect, useState } from "react";
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

type AdminMenuOption = {
    title: string;
    description: string;
    to?: string;
    icon: JSX.Element;
    actionLabel: string;
    disabled?: boolean;
};

type ClassAccessResponse = {
    id?: number;
    name?: string;
    school_id?: number;
    school_name?: string;
};

export default function AdminMenu() {
    const { school_id, class_id } = useParams<{
        school_id: string;
        class_id: string;
    }>();

    const schoolId = school_id || "";
    const classId = class_id || "";
    const [className, setClassName] = useState("");

    useEffect(() => {
        if (!schoolId || !classId) {
            setClassName("");
            return;
        }

        axios
            .get<ClassAccessResponse>(
                import.meta.env.VITE_API_URL +
                `/class/id/${classId}/access?school_id=${schoolId}&role_context=admin`,
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
            description: "Preview the student-facing class experience without submitting work",
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

    const handleCardKeyDown = (
        event: KeyboardEvent<HTMLElement>,
        option: AdminMenuOption,
    ) => {
        if (option.disabled || !option.to) return;
        if (event.key !== "Enter" && event.key !== " ") return;

        event.preventDefault();
        window.location.href = option.to;
    };

    return (
        <div className="projects-page admin-landing-root admin-menu-page">
            <Helmet>
                <title>[Admin] MAAT</title>
            </Helmet>

            <MenuComponent
                showUpload={false}
                showAdminUpload={false}
                showHelp={false}
                showCreate={false}
                showLast={false}
                showReviewButton={false}
            />

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
