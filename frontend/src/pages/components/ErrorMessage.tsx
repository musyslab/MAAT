// ErrorMessage.tsx: Renders the error message interface and coordinates its local data and interactions.
import { Component } from "react";

// Describes the error message props data expected by this file.
interface ErrorMessageProps {
  message: string;
  isHidden: boolean;
}

class ErrorMessage extends Component<ErrorMessageProps> {
  render() {
    const { message, isHidden } = this.props;

    if (isHidden || !message) return null;

    // Renders the interface using the current data and interaction state.
    return (
      <div role="alert" aria-live="assertive" className="error-message">
        {message}
      </div>
    );
  }
}

export default ErrorMessage;
