// SchoolLoginSelect.tsx: Renders the school login select interface and coordinates its local data and interactions.
import React, { useEffect, useMemo, useState } from "react";

import axios from "axios";
import { Helmet } from "react-helmet";
import { Navigate, useNavigate } from "react-router-dom";

import MenuComponent from "../components/MenuComponent";
import "../../styling/Login.scss";

// Describes the oauth provider data expected by this file.
type OAuthProvider = "google" | "microsoft";

// Describes the school login option data expected by this file.
interface SchoolLoginOption {
  id: number;
  name: string;
  auth_provider: OAuthProvider;
}

// Helper for provider label used by this component.
function providerLabel(provider: OAuthProvider): string {
  return provider === "google" ? "Google" : "Microsoft";
}

// Renders the school login select interface and coordinates its local data and interactions.
function SchoolLoginSelect() {
  const apiBase = (import.meta.env.VITE_API_URL as string) || "";
  const navigate = useNavigate();

  // Keeps the values that drive this component’s display and user interactions in React state.
  const [schools, setSchools] = useState<Array<SchoolLoginOption>>([]);
  const [schoolId, setSchoolId] = useState<number>(-1);
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const storedToken = localStorage.getItem("AUTOTA_AUTH_TOKEN");
  const isLoggedIn = Boolean(
    storedToken &&
    storedToken.trim() &&
    storedToken.trim().toLowerCase() !== "null" &&
    storedToken.trim().toLowerCase() !== "undefined"
  );

  // Synchronizes this component with the values listed in the dependency array.
  useEffect(() => {
    let isMounted = true;

    axios
      .get(`${apiBase}/schools/get_school_login_options`)
      .then((res) => {
        if (!isMounted) {
          return;
        }

        const rows = Array.isArray(res.data) ? (res.data as Array<SchoolLoginOption>) : [];
        setSchools(rows);

        if (rows.length === 0) {
          setErrorMessage("No schools are currently configured for login.");
        }
      })
      .catch(() => {
        if (isMounted) {
          setErrorMessage("Could not load school login options.");
        }
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [apiBase]);

  // Recomputes selected school only when its dependencies change.
  const selectedSchool = useMemo(
    () => schools.find((school) => school.id === schoolId) || null,
    [schoolId, schools]
  );

  // Handles submit for this view.
  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!selectedSchool) {
      setErrorMessage("Please choose your school.");
      return;
    }

    navigate(`/login?school_id=${selectedSchool.id}`);
  };

  if (isLoggedIn) {
    // Renders the interface using the current data and interaction state.
    return <Navigate to="/schools" replace />;
  }

  // Renders the interface using the current data and interaction state.
  return (
    <div className="login-page">
      {/* Sets the page title and document metadata. */}
      <Helmet>
        <title>Select School | MAAT</title>
      </Helmet>

      {/* Displays the navigation and actions available on this page. */}
      <MenuComponent
      />

      <div className="login-shell">
        <div className="login-card">
          <h1 className="login-title">Choose your school</h1>

          <form className="login-form" onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label" htmlFor="loginSchool">
                School
              </label>
              <select
                id="loginSchool"
                className="form-select"
                value={schoolId}
                disabled={isLoading}
                onChange={(event) => {
                  setSchoolId(Number(event.target.value));
                  setErrorMessage("");
                }}
              >
                <option value={-1}>{isLoading ? "Loading schools..." : "Select a school"}</option>
                {schools.map((school) => (
                  <option key={school.id} value={school.id}>
                    {school.name}
                  </option>
                ))}
              </select>
            </div>

            <button className="btn btn--primary login-form__submit" type="submit" disabled={!selectedSchool}>
              {selectedSchool
                ? `Continue to ${providerLabel(selectedSchool.auth_provider)}`
                : "Continue"}
            </button>
          </form>

          {errorMessage ? (
            <div className="alert alert--error" role="alert" aria-live="assertive">
              {errorMessage}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default SchoolLoginSelect;
