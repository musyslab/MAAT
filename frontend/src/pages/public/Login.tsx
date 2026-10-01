// Login.tsx: Renders the login interface and coordinates its local data and interactions.
import React, { useCallback, useEffect, useRef, useState } from "react";

import { FaMicrosoft } from "react-icons/fa";
import axios from "axios";
import { Helmet } from "react-helmet";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { PublicClientApplication } from "@azure/msal-browser";

import MenuComponent from "../components/MenuComponent";
import maatLogo from "../../images/MAAT.png";
import "../../styling/Login.scss";

declare global {
  // Describes the window data expected by this file.
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: { credential?: string }) => void;
          }) => void;
          renderButton: (
            parent: HTMLElement,
            options: {
              theme?: "outline" | "filled_blue" | "filled_black";
              size?: "large" | "medium" | "small";
              shape?: "rectangular" | "pill" | "circle" | "square";
              text?: "signin_with" | "signup_with" | "continue_with" | "signin";
              width?: number | string;
              logo_alignment?: "left" | "center";
            }
          ) => void;
        };
      };
    };
  }
}

// Describes the id name pair data expected by this file.
interface IdNamePair {
  name: string;
  id: number;
}

// Describes the class json data expected by this file.
interface ClassJson {
  name: string;
  id: number;
  labs: Array<IdNamePair>;
  lectures: Array<IdNamePair>;
}

// Describes the drop down option data expected by this file.
interface DropDownOption {
  key: number;
  value: number;
  text: string;
}

// Describes the oauth provider data expected by this file.
type OAuthProvider = "google" | "microsoft";

// Describes the oauth config data expected by this file.
interface OAuthConfig {
  enabled: boolean;
  provider: OAuthProvider;
  school: {
    id: number;
    name: string;
    requires_lab_and_lecture: boolean;
  };
  google_client_id: string;
  microsoft_client_id: string;
  microsoft_authority: string;
}

// Describes the oauth profile data expected by this file.
interface OAuthProfile {
  provider: OAuthProvider;
  email: string;
  first_name: string;
  last_name: string;
  display_name: string;
}

// Describes the session access summary data expected by this file.
interface SessionAccessSummary {
  role?: number;
  can_teach?: boolean;
  can_study?: boolean;
  default_dashboard?: "admin" | "student";
}

const GOOGLE_SCRIPT_SRC = "https://accounts.google.com/gsi/client";

// Loads the Google sign-in script needed by the login interface.
function loadGoogleScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) {
      resolve();
      return;
    }

    const existing = document.querySelector(`script[src="${GOOGLE_SCRIPT_SRC}"]`) as HTMLScriptElement | null;
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error("Failed to load Google Sign-In script.")), {
        once: true,
      });
      return;
    }

    const script = document.createElement("script");
    script.src = GOOGLE_SCRIPT_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Failed to load Google Sign-In script."));
    document.body.appendChild(script);
  });
}

// Renders the login interface and coordinates its local data and interactions.
function Login() {
  const apiBase = (import.meta.env.VITE_API_URL as string) || "";
  const [searchParams] = useSearchParams();
  const requestedSchoolId = Number(searchParams.get("school_id") || 0);
  const selectedSchoolId =
    Number.isInteger(requestedSchoolId) && requestedSchoolId > 0 ? requestedSchoolId : 0;

  const storedToken = localStorage.getItem("AUTOTA_AUTH_TOKEN");
  const initialLoggedIn = Boolean(
    storedToken &&
      storedToken.trim() &&
      storedToken.trim().toLowerCase() !== "null" &&
      storedToken.trim().toLowerCase() !== "undefined"
  );

  // Keeps the values that drive this component’s display and user interactions in React state.
  const [isLoggedIn, setIsLoggedIn] = useState<boolean>(initialLoggedIn);
  const [errorMessage, setErrorMessage] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(false);

  const [isNewUser, setIsNewUser] = useState<boolean>(false);
  const [newUserError, setNewUserError] = useState<string>("");
  const [studentNumber, setStudentNumber] = useState<string>("");

  const [classId, setClassId] = useState<number>(-1);
  const [labId, setLabId] = useState<number>(-1);
  const [lectureId, setLectureId] = useState<number>(-1);

  const [classes, setClasses] = useState<Array<ClassJson>>([]);
  const [classOptions, setClassOptions] = useState<Array<DropDownOption>>([]);
  const [labOptions, setLabOptions] = useState<Array<DropDownOption>>([]);
  const [lectureOptions, setLectureOptions] = useState<Array<DropDownOption>>([]);
  const [hasClassSelected, setHasClassSelected] = useState<boolean>(false);

  const [oauthConfig, setOAuthConfig] = useState<OAuthConfig | null>(null);
  const [oauthProfile, setOAuthProfile] = useState<OAuthProfile | null>(null);
  const [oauthSignupToken, setOAuthSignupToken] = useState<string>("");

  // Keeps google button ref available across renders without triggering a state update.
  const googleButtonRef = useRef<HTMLDivElement | null>(null);
  const requiresLabAndLecture = oauthConfig?.school.requires_lab_and_lecture ?? true;

  // Keeps the persist session callback stable until its dependencies change.
  const persistSession = useCallback(
    (accessToken: string, _userRole: number, _accessSummary?: SessionAccessSummary) => {
      localStorage.setItem("AUTOTA_AUTH_TOKEN", accessToken);
      setIsLoggedIn(true);
    },
    []
  );

  // Keeps the reset new user selections callback stable until its dependencies change.
  const resetNewUserSelections = useCallback(() => {
    setClassId(-1);
    setLabId(-1);
    setLectureId(-1);
    setClasses([]);
    setClassOptions([]);
    setLabOptions([]);
    setLectureOptions([]);
    setHasClassSelected(false);
  }, []);

  const currentSchoolRef = useRef(selectedSchoolId);
  currentSchoolRef.current = selectedSchoolId;

  // Keeps the fetch sections callback stable until its dependencies change.
  const fetchSections = useCallback(async () => {
    if (selectedSchoolId <= 0) {
      return;
    }

    try {
      // Fetches the server data needed for this operation.
      const res = await axios.get(`${apiBase}/classes/get_class_labs?school_id=${selectedSchoolId}`);
      if (currentSchoolRef.current !== selectedSchoolId) return;
      const sectionClasses = Array.isArray(res.data) ? (res.data as Array<ClassJson>) : [];

      setClasses(sectionClasses);
      setClassOptions(
        sectionClasses.map((cls) => ({
          key: cls.id,
          value: cls.id,
          text: cls.name,
        }))
      );

      setLabOptions([]);
      setLectureOptions([]);
      setHasClassSelected(false);

      if (sectionClasses.length === 0) {
        setNewUserError("No classes were returned for the selected school.");
      } else {
        setNewUserError("");
      }
    } catch (err) {
      if (currentSchoolRef.current !== selectedSchoolId) return;
      console.error(err);
      setNewUserError("Could not load class options.");
    }
  }, [apiBase, selectedSchoolId]);

  // Synchronizes this component with the values listed in the dependency array.
  useEffect(() => {
    if (selectedSchoolId <= 0) {
      return;
    }

    const controller = new AbortController();
    setOAuthConfig(null);
    setErrorMessage("");

    axios
      .get(`${apiBase}/auth/oauth_config?school_id=${selectedSchoolId}`, { signal: controller.signal })
      .then((res) => {
        if (controller.signal.aborted) return;
        setOAuthConfig(res.data as OAuthConfig);
      })
      .catch((err: any) => {
        if (controller.signal.aborted) return;
        setErrorMessage(err.response?.data?.message || "Login is not configured for this school.");
      });
    return () => controller.abort();
  }, [apiBase, selectedSchoolId]);

  // Loads or refreshes view data when the dependencies below change.
  useEffect(() => {
    if (isNewUser) {
      void fetchSections();
    }
  }, [fetchSections, isNewUser]);

  // Keeps the handle oauth backend login callback stable until its dependencies change.
  const handleOAuthBackendLogin = useCallback(
    async (provider: OAuthProvider, idToken: string) => {
      setErrorMessage("");
      setIsLoading(true);

      try {
        // Sends this operation and its payload to the server.
        const res = await axios.post(`${apiBase}/auth/oauth_login`, {
          provider,
          id_token: idToken,
          school_id: selectedSchoolId,
        });

        if (res.data.message === "New OAuth User") {
          resetNewUserSelections();
          setIsNewUser(true);
          setOAuthSignupToken(res.data.signup_token || "");
          setOAuthProfile(res.data.oauth_profile || null);
          setNewUserError("");
        } else {
          persistSession(res.data.access_token, Number(res.data.role || 0), res.data as SessionAccessSummary);
        }
      } catch (err: any) {
        setErrorMessage(err.response?.data?.message || "OAuth login failed.");
      } finally {
        setIsLoading(false);
      }
    },
    [apiBase, persistSession, resetNewUserSelections, selectedSchoolId]
  );

  // Loads or refreshes view data when the dependencies below change.
  useEffect(() => {
    if (
      oauthConfig?.provider !== "google" ||
      !oauthConfig.enabled ||
      !oauthConfig.google_client_id ||
      !googleButtonRef.current
    ) {
      return;
    }

    let cancelled = false;
    loadGoogleScript()
      .then(() => {
        if (cancelled || !window.google?.accounts?.id || !googleButtonRef.current) {
          return;
        }

        googleButtonRef.current.innerHTML = "";
        window.google.accounts.id.initialize({
          client_id: oauthConfig.google_client_id,
          callback: (response) => {
            if (cancelled) return;
            if (!response.credential) {
              setErrorMessage("Google login did not return an ID token.");
              return;
            }

            void handleOAuthBackendLogin("google", response.credential);
          },
        });

        window.google.accounts.id.renderButton(googleButtonRef.current, {
          theme: "outline",
          size: "large",
          shape: "rectangular",
          text: "continue_with",
          width: 360,
          logo_alignment: "left",
        });
      })
      .catch(() => {
        if (!cancelled) setErrorMessage("Failed to initialize Google Sign-In.");
      });
    return () => { cancelled = true; };
  }, [handleOAuthBackendLogin, oauthConfig]);

  // Handles microsoft login for this view.
  const handleMicrosoftLogin = async () => {
    if (
      oauthConfig?.provider !== "microsoft" ||
      !oauthConfig.enabled ||
      !oauthConfig.microsoft_client_id ||
      !oauthConfig.microsoft_authority
    ) {
      setErrorMessage("Microsoft login is not configured for this school.");
      return;
    }

    setErrorMessage("");
    setIsLoading(true);

    try {
      const msal = new PublicClientApplication({
        auth: {
          clientId: oauthConfig.microsoft_client_id,
          authority: oauthConfig.microsoft_authority,
          redirectUri: window.location.origin,
        },
        cache: {
          cacheLocation: "memoryStorage",
        },
      });

      await msal.initialize();

      const response = await msal.loginPopup({
        scopes: ["openid", "profile", "email", "User.Read"],
        prompt: "select_account",
      });

      if (!response.idToken) {
        throw new Error("Microsoft login did not return an ID token.");
      }

      await handleOAuthBackendLogin("microsoft", response.idToken);
    } catch (err: any) {
      setErrorMessage(err?.message || err?.response?.data?.message || "Microsoft login failed.");
      setIsLoading(false);
    }
  };

  // Handles class id change for this view.
  const handleClassIdChange = (value: number) => {
    const selectedClass = classes.find((cls) => cls.id === value);

    const newLabOptions = selectedClass
      ? selectedClass.labs.map((lab) => ({ key: lab.id, text: lab.name, value: lab.id }))
      : [];

    const newLectureOptions = selectedClass
      ? selectedClass.lectures.map((lecture) => ({
          key: lecture.id,
          text: lecture.name,
          value: lecture.id,
        }))
      : [];

    setClassId(value);
    setLabId(-1);
    setLectureId(-1);
    setHasClassSelected(value !== -1);
    setLabOptions(newLabOptions);
    setLectureOptions(newLectureOptions);
  };

  // Handles new user submit for this view.
  const handleNewUserSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setNewUserError("");

    if (!oauthSignupToken.trim()) {
      setNewUserError("OAuth sign-up token is missing. Please sign in again.");
      return;
    }

    if (
      classId <= 0 ||
      !studentNumber.trim() ||
      (requiresLabAndLecture && (labId <= 0 || lectureId <= 0))
    ) {
      setNewUserError(
        requiresLabAndLecture
          ? "Please enter your school ID and choose a class, lecture, and lab."
          : "Please enter your school ID and choose a class."
      );
      return;
    }

    setIsLoading(true);

    try {
      // Sends this operation and its payload to the server.
      const res = await axios.post(`${apiBase}/auth/create_oauth_user`, {
        signup_token: oauthSignupToken,
        id: studentNumber,
        school_id: selectedSchoolId,
        class_id: classId,
        lab_id: requiresLabAndLecture ? labId : null,
        lecture_id: requiresLabAndLecture ? lectureId : null,
      });

      persistSession(res.data.access_token, Number(res.data.role || 0), res.data as SessionAccessSummary);
    } catch (err: any) {
      setNewUserError(err.response?.data?.message || "Account creation failed.");
    } finally {
      setIsLoading(false);
    }
  };

  if (isLoggedIn) {
    // Renders the interface using the current data and interaction state.
    return <Navigate to="/schools" replace />;
  }

  if (selectedSchoolId <= 0) {
    // Renders the interface using the current data and interaction state.
    return <Navigate to="/school-login" replace />;
  }

  // Renders the interface using the current data and interaction state.
  return (
    <div className="login-page">
      {/* Sets the page title and document metadata. */}
      <Helmet>
        <title>Login | MAAT</title>
      </Helmet>

      {/* Displays the navigation and actions available on this page. */}
      <MenuComponent
      />

      {isNewUser ? (
        <div className="login-modal">
          <div className="login-modal__content" role="dialog" aria-modal="true">
            <h2 className="login-modal__title">Finish creating your MAAT account</h2>

            {oauthProfile ? (
              <div className="oauth-profile-card">
                <div className="oauth-profile-card__label">
                  Signed in with {oauthProfile.provider} for {oauthConfig?.school.name}
                </div>
                <div className="oauth-profile-card__name">{oauthProfile.display_name}</div>
                <div className="oauth-profile-card__email">{oauthProfile.email}</div>
                <div className="oauth-profile-card__hint">
                  Your name and email came from your identity provider. Finish your student and class details below.
                </div>
              </div>
            ) : null}

            <form className="login-modal__form" onSubmit={handleNewUserSubmit}>
              <div className="form-group">
                <label className="form-label" htmlFor="schoolName">
                  School
                </label>
                <input
                  id="schoolName"
                  type="text"
                  className="form-input"
                  value={oauthConfig?.school.name || ""}
                  readOnly
                />
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="sid">
                  School ID
                </label>
                <input
                  id="sid"
                  type="text"
                  placeholder="001234567"
                  value={studentNumber}
                  onChange={(event) => setStudentNumber(event.target.value)}
                  className="form-input"
                />
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="classSelect">
                  Class Name
                </label>
                <select
                  id="classSelect"
                  value={classId}
                  onChange={(event) => handleClassIdChange(Number(event.target.value))}
                  className="form-select"
                >
                  <option value={-1}>Class</option>
                  {classOptions.map((option) => (
                    <option key={option.key} value={option.value}>
                      {option.text}
                    </option>
                  ))}
                </select>
              </div>

              {requiresLabAndLecture ? (
                <>
                  <div className="form-group">
                    <label className="form-label" htmlFor="lectureSelect">
                      Lecture Number
                    </label>
                    <select
                      id="lectureSelect"
                      value={lectureId}
                      onChange={(event) => setLectureId(Number(event.target.value))}
                      disabled={!hasClassSelected}
                      className="form-select"
                    >
                      <option value={-1}>Lecture</option>
                      {lectureOptions.map((option) => (
                        <option key={option.key} value={option.value}>
                          {option.text}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="form-group">
                    <label className="form-label" htmlFor="labSelect">
                      Lab Number
                    </label>
                    <select
                      id="labSelect"
                      value={labId}
                      onChange={(event) => setLabId(Number(event.target.value))}
                      disabled={!hasClassSelected}
                      className="form-select"
                    >
                      <option value={-1}>Lab</option>
                      {labOptions.map((option) => (
                        <option key={option.key} value={option.value}>
                          {option.text}
                        </option>
                      ))}
                    </select>
                  </div>
                </>
              ) : null}

              {newUserError ? (
                <div className="alert alert--error" role="alert" aria-live="assertive">
                  {newUserError}
                </div>
              ) : null}

              <button className="btn btn--primary" type="submit" disabled={isLoading}>
                {isLoading ? "Submitting..." : "Submit"}
              </button>
            </form>
          </div>
        </div>
      ) : null}

      <div className="login-shell">
        <div className="login-card">
          <h1 className="login-title">Login to MAAT</h1>
          <p className="login-subtitle">
            {oauthConfig ? `Use your ${oauthConfig.school.name} account.` : "Loading school login..."}
          </p>

          <div className="oauth-section">
            {oauthConfig?.provider === "google" && oauthConfig.enabled ? (
              <div className="google-button-shell">
                <div ref={googleButtonRef} />
              </div>
            ) : null}

            {oauthConfig?.provider === "microsoft" && oauthConfig.enabled ? (
              <button
                className="btn btn--microsoft"
                type="button"
                onClick={handleMicrosoftLogin}
                disabled={isLoading}
              >
                <FaMicrosoft aria-hidden="true" />
                <span>{isLoading ? "Working..." : "Continue with Microsoft"}</span>
              </button>
            ) : null}
          </div>

          {oauthConfig && !oauthConfig.enabled ? (
            <div className="alert alert--error" role="alert">
              Login has not been configured for this school.
            </div>
          ) : null}

          {errorMessage ? (
            <div className="alert alert--error" role="alert" aria-live="assertive">
              {errorMessage}
            </div>
          ) : null}

          <div className="login-links">
            <Link className="login-links__link" to="/school-login">
              Choose a different school
            </Link>
            <div className="login-links__logo-wrap">
              <img src={maatLogo} alt="MAAT" className="login-links__logo" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Login;
