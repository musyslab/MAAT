// NotFound.tsx: Renders the not found interface and coordinates its local data and interactions.
import { Component } from "react";
import { Helmet } from "react-helmet";
import MenuComponent from "../components/MenuComponent";

class NotFound extends Component {
    render() {
        // Renders the interface using the current data and interaction state.
        return (
            <div className="notfound">
                {/* Sets the page title and document metadata. */}
                <Helmet>
                    <title>404 Error | MAAT</title>
                </Helmet>

                {/* Displays the navigation and actions available on this page. */}
                <MenuComponent
                />

                <div className="notfound__grid">
                    <div className="notfound__column">
                        <img
                            className="notfound__image"
                            src="https://i.ytimg.com/vi/AY-rnBoaiY8/maxresdefault.jpg"
                            alt="Sad robot"
                            height="200"
                            width="300"
                        />
                        <h1 className="notfound__title">We&apos;re sorry. AutoTA failed this test.</h1>
                        <p className="notfound__message">
                            Sorry we couldn&apos;t find the page you were looking for.
                        </p>
                        <p className="notfound__message">
                            Perhaps you can return back to the homepage and see if you can find what you&apos;re
                            looking for. If you believe this is a mistake, please contact us.
                        </p>
                    </div>
                </div>
            </div>
        );
    }
}

export default NotFound;
