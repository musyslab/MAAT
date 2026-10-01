// HomePage.tsx: Renders the home page interface and coordinates its local data and interactions.
import { Helmet } from "react-helmet";
import { Link } from "react-router-dom";
import {
  FaArrowRight,
  FaChalkboardTeacher,
  FaCheckCircle,
  FaClipboardCheck,
  FaCode,
  FaTasks,
  FaUpload,
} from "react-icons/fa";

import MenuComponent from "../components/MenuComponent";
import "../../styling/HomePage.scss";

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

// Renders the home page interface and coordinates its local data and interactions.
function HomePage() {
  const isLoggedIn = Boolean(getValidStoredToken());
  const dashboardPath = "/schools";

  // Renders the interface using the current data and interaction state.
  return (
    <div className="home-page">
      {/* Sets the page title and document metadata. */}
      <Helmet>
        <title>MAAT</title>
      </Helmet>

      {/* Displays the navigation and actions available on this page. */}
      <MenuComponent
      />

      <main className="home-shell">
        <section className="home-hero">
          <div className="home-hero__grid">
            <div className="home-hero__content">
              <h1 className="home-hero__title">
                Faster assignment workflows for CS students and teachers
              </h1>

              <p className="home-hero__text home-hero__text--lead">
                The Marquette Automated Assessment Tool or MAAT (formerly known as TA-Bot) helps programming courses collect submissions,
                run automated testcase checks, and organize grading in one place.
              </p>

              <div className="home-hero__actions">
                {isLoggedIn ? (
                  <Link className="home-button home-button--primary" to={dashboardPath}>
                    Go to dashboard
                    <FaArrowRight />
                  </Link>
                ) : (
                  <Link className="home-button home-button--primary" to="/login">
                    Login
                    <FaArrowRight />
                  </Link>
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="home-section" id="features">
          <div className="home-section__header">
            <div className="home-section__eyebrow">Features</div>
            <h2 className="home-section__title">What MAAT helps you do</h2>
          </div>

          <div className="home-feature-grid">
            <article className="home-feature-card">
              <div className="home-feature-card__icon">
                <FaUpload />
              </div>
              <h3 className="home-feature-card__title">Collect submissions</h3>
              <p className="home-feature-card__text">
                Give students a direct place to upload programming assignments and keep class activity organized.
              </p>
            </article>

            <article className="home-feature-card">
              <div className="home-feature-card__icon">
                <FaClipboardCheck />
              </div>
              <h3 className="home-feature-card__title">Run testcase checks</h3>
              <p className="home-feature-card__text">
                Evaluate submissions against defined testcases to support consistent correctness checks.
              </p>
            </article>

            <article className="home-feature-card">
              <div className="home-feature-card__icon">
                <FaCode />
              </div>
              <h3 className="home-feature-card__title">Review programming work</h3>
              <p className="home-feature-card__text">
                Inspect student submissions and testcase outcomes in a workflow tailored to code-based assignments.
              </p>
            </article>

            <article className="home-feature-card">
              <div className="home-feature-card__icon">
                <FaTasks />
              </div>
              <h3 className="home-feature-card__title">Manage assignments</h3>
              <p className="home-feature-card__text">
                Create projects, organize coursework, and keep course expectations structured from start to finish.
              </p>
            </article>

            <article className="home-feature-card">
              <div className="home-feature-card__icon">
                <FaChalkboardTeacher />
              </div>
              <h3 className="home-feature-card__title">Support teaching workflows</h3>
              <p className="home-feature-card__text">
                Reduce manual handling so teachers can focus more on student progress and course delivery.
              </p>
            </article>

            <article className="home-feature-card">
              <div className="home-feature-card__icon">
                <FaCheckCircle />
              </div>
              <h3 className="home-feature-card__title">Keep grading aligned</h3>
              <p className="home-feature-card__text">
                Bring submissions, testcase results, and grading decisions together in one system.
              </p>
            </article>
          </div>
        </section>

        <section className="home-section">
          <div className="home-section__header">
            <div className="home-section__eyebrow">How it works</div>
            <h2 className="home-section__title">A simple course workflow</h2>
          </div>

          <div className="home-steps">
            <article className="home-step-card">
              <div className="home-step-card__number">01</div>
              <h3 className="home-step-card__title">Teachers create coursework</h3>
              <p className="home-step-card__text">
                Set up programming projects, deadlines, and testcase expectations for the class.
              </p>
            </article>

            <article className="home-step-card">
              <div className="home-step-card__number">02</div>
              <h3 className="home-step-card__title">Students submit assignments</h3>
              <p className="home-step-card__text">
                Upload work through a focused interface built around programming assignment submissions.
              </p>
            </article>

            <article className="home-step-card">
              <div className="home-step-card__number">03</div>
              <h3 className="home-step-card__title">Results support grading</h3>
              <p className="home-step-card__text">
                Testcase outcomes and submission review tools help teachers evaluate student work more efficiently.
              </p>
            </article>
          </div>
        </section>

        <section className="home-cta">
          <div className="home-cta__content">
            <div>
              <div className="home-section__eyebrow">Get started</div>
              <h2 className="home-cta__title">Use MAAT to streamline programming course assessment</h2>
              <p className="home-cta__text">
                {isLoggedIn
                  ? "Open your dashboard to access submissions, projects, and grading tools."
                  : "Log in to access submissions, projects, and grading tools."}
              </p>
            </div>

            <div className="home-cta__actions">
              {isLoggedIn ? (
                <Link className="home-button home-button--primary" to={dashboardPath}>
                  Go to dashboard
                  <FaArrowRight />
                </Link>
              ) : (
                <Link className="home-button home-button--primary" to="/login">
                  Go to login
                  <FaArrowRight />
                </Link>
              )}
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

export default HomePage;
