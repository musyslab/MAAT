// LoadingAnimation.tsx: Renders the loading animation interface and coordinates its local data and interactions.
import { useEffect } from 'react'
import '../../styling/LoadingAnimation.scss'

// Describes the loading animation props data expected by this file.
type LoadingAnimationProps = {
    show: boolean
    message?: string
}

// Renders the loading animation interface and coordinates its local data and interactions.
export default function LoadingAnimation({ show, message }: LoadingAnimationProps) {
    // Synchronizes this component with the values listed in the dependency array.
    useEffect(() => {
        if (!show) return

        const body = document.body
        const html = document.documentElement

        const prevBodyOverflow = body.style.overflow
        const prevHtmlOverflow = html.style.overflow
        const prevBodyPaddingRight = body.style.paddingRight

        const scrollbarWidth = window.innerWidth - html.clientWidth

        body.style.overflow = 'hidden'
        html.style.overflow = 'hidden'
        if (scrollbarWidth > 0) body.style.paddingRight = `${scrollbarWidth}px`

        return () => {
            body.style.overflow = prevBodyOverflow
            html.style.overflow = prevHtmlOverflow
            body.style.paddingRight = prevBodyPaddingRight
        }
    }, [show])

    if (!show) return null

    // Renders the interface using the current data and interaction state.
    return (
        <div className="loading-animation-overlay" role="status" aria-live="polite" aria-busy="true">
            <div className="loading-animation-panel">
                <div className="loading-animation-spinner" aria-hidden="true" />
                <div className="loading-animation-text">{message || 'Loading...'}</div>
            </div>
        </div>
    )
}
