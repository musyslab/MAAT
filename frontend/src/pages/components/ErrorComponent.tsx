// ErrorComponent.tsx: Renders the error component interface and coordinates its local data and interactions.
import axios from 'axios'
import React, { Component, PropsWithChildren } from 'react'
import CriticalErrorPage from './CriticalErrorPage'

// Describes the error message state data expected by this file.
interface ErrorMessageState {
    hasError: boolean
}

class ErrorBoundary extends Component<PropsWithChildren, ErrorMessageState> {
    constructor(props: {}) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError() {
        // Update state so the next render will show the fallback UI.
        return { hasError: true };
    }

    componentDidCatch(_error: Error, errorInfo: React.ErrorInfo) {
        axios.post(import.meta.env.VITE_API_URL + `/error/log_error`, { location: window.location.href, error_stack: JSON.stringify(errorInfo) }, {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem("AUTOTA_AUTH_TOKEN")}`
            }
        })
            .catch(err => {
                console.log(err)
            })
    }

    render() {
        if (this.state.hasError) {
            // You can render any custom fallback UI
            return (<CriticalErrorPage></CriticalErrorPage>);
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
