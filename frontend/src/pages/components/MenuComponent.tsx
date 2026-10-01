// MenuComponent.tsx: Renders the menu component interface and coordinates its local data and interactions.
import { Component } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import {
    FaHome,
    FaSignInAlt,
    FaSignOutAlt,
} from "react-icons/fa";
import maatLogo from "../../images/MAAT.png";
import "../../styling/MenuComponent.scss";

// Reads the stored login token and rejects placeholder or missing values.
const getValidStoredToken = (): string | null => {
    const token = localStorage.getItem("AUTOTA_AUTH_TOKEN");

    if (!token) {
        return null;
    }

    const cleanedToken = token.trim();

    if (
        !cleanedToken ||
        cleanedToken.toLowerCase() === "null" ||
        cleanedToken.toLowerCase() === "undefined"
    ) {
        localStorage.removeItem("AUTOTA_AUTH_TOKEN");
        return null;
    }

    return cleanedToken;
};

class MenuComponent extends Component {
    handleLogout = () => {
        localStorage.removeItem("AUTOTA_AUTH_TOKEN");
        window.location.replace("/login");
    };

    handleLogin = () => {
        window.location.replace("/login");
    };

    handleDashboard = () => {
        const token = getValidStoredToken();

        if (!token) {
            window.location.replace("/login");
            return;
        }

        axios
            .get(`${import.meta.env.VITE_API_URL}/auth/access_summary`, {
                headers: {
                    Authorization: `Bearer ${token}`,
                },
            })
            .then(() => {
                window.location.replace("/schools");
            })
            .catch(() => {
                localStorage.removeItem("AUTOTA_AUTH_TOKEN");
                window.location.replace("/login");
            });
    };

    render() {
        const isLoggedIn = Boolean(getValidStoredToken());

        // Renders the interface using the current data and interaction state.
        return (
            <nav className="menu menu--top menu--inverted menu--borderless menu--huge">
                <div className="menu__container">
                    <Link className="menu__item menu__item--header" to="/">
                        <img src={maatLogo} alt="MAAT" className="menu__logo" />
                    </Link>
                    <div className="menu__right">
                        {isLoggedIn ? (
                            <>
                                <button
                                    type="button"
                                    className="menu__item menu__item--link"
                                    onClick={this.handleDashboard}
                                    title="Dashboard"
                                >
                                    <FaHome className="menu__icon" aria-hidden="true" />
                                    <span className="menu__text">Home</span>
                                </button>

                                <button
                                    type="button"
                                    className="menu__item menu__item--link menu__logout"
                                    onClick={this.handleLogout}
                                    title="Log Out"
                                >
                                    <FaSignOutAlt className="menu__icon" aria-hidden="true" />
                                    <span className="menu__text">Log Out</span>
                                </button>
                            </>
                        ) : (
                            <button
                                type="button"
                                className="menu__item menu__item--link"
                                onClick={this.handleLogin}
                                title="Log In"
                            >
                                <FaSignInAlt className="menu__icon" aria-hidden="true" />
                                <span className="menu__text">Log In</span>
                            </button>
                        )}
                    </div>
                </div>
            </nav>
        );
    }
}

export default MenuComponent;
