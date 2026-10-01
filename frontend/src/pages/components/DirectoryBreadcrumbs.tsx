// DirectoryBreadcrumbs.tsx: Renders the directory breadcrumbs interface and coordinates its local data and interactions.
import React from "react"
import { Link } from "react-router-dom"
import "../../styling/Directory.scss"

// Describes the directory crumb data expected by this file.
export type DirectoryCrumb = {
    label: string
    to?: string
    onClick?: () => void
}

// Describes the directory breadcrumbs props data expected by this file.
interface DirectoryBreadcrumbsProps {
    items: DirectoryCrumb[]
    trailingSeparator?: boolean // true => "Class Selection/"
    className?: string
    confirmOnNavigate?: boolean
    confirmMessage?: string
}

// Renders the directory breadcrumbs interface and coordinates its local data and interactions.
const DirectoryBreadcrumbs: React.FC<DirectoryBreadcrumbsProps> = ({
    items,
    trailingSeparator = false,
    className = "",
    confirmOnNavigate = false,
    confirmMessage = "You have unsaved changes. Leave this page?",
}) => {
    // Helper for should navigate used by this component.
    const shouldNavigate = () => {
        if (!confirmOnNavigate) return true
        return window.confirm(confirmMessage)
    }

    // Handles crumb click for this view.
    const handleCrumbClick = (e: React.MouseEvent, onClick?: () => void) => {
        if (!shouldNavigate()) {
            e.preventDefault()
            return
        }

        onClick?.()
    }

    // Renders the interface using the current data and interaction state.
    return (
        <nav className={`directory ${className}`.trim()} aria-label="Directory">
            <ol className="directory__list">
                {items.map((item, idx) => {
                    const isLast = idx === items.length - 1
                    const showSeparator = !isLast || trailingSeparator

                    const content =
                        item.to ? (
                            <Link
                                className="directory__link"
                                to={item.to}
                                onClick={(e) => handleCrumbClick(e, item.onClick)}
                            >
                                {item.label}
                            </Link>
                        ) : item.onClick ? (
                            <button
                                type="button"
                                className="directory__link"
                                onClick={(e) => handleCrumbClick(e, item.onClick)}
                            >
                                {item.label}
                            </button>
                        ) : (
                            <span className="directory__current">{item.label}</span>
                        )

                    // Renders the interface using the current data and interaction state.
                    return (
                        <li key={`${item.label}-${idx}`} className="directory__item">
                            {content}
                            {showSeparator && <span className="directory__sep">/</span>}
                        </li>
                    )
                })}
            </ol>
        </nav>
    )
}

export default DirectoryBreadcrumbs
