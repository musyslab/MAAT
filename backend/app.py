"""Start the backend's development HTTP server.

Build the Flask application through src.core.application and listen on port
5000. FLASK_DEBUG controls debug mode; configuration, dependency injection,
and endpoint registration are handled by the application factory."""

from src.core.application import create_app
from src.core.config import env_bool

if __name__ == "__main__":
    app = create_app()
    app.run(debug=env_bool("FLASK_DEBUG", False), host="0.0.0.0", port=5000)
