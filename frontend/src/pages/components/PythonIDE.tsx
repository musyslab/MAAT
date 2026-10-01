// PythonIDE.tsx: Provides the Python editor, diagnostics, execution results, and interactive console input.
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import CodeMirror from "@uiw/react-codemirror";
import { indentWithTab } from "@codemirror/commands";
import { python } from "@codemirror/lang-python";
import { indentUnit, syntaxTree } from "@codemirror/language";
import {
  Diagnostic,
  forEachDiagnostic,
  lintGutter,
  linter,
} from "@codemirror/lint";
import { oneDark } from "@codemirror/theme-one-dark";
import { EditorView, keymap, ViewUpdate } from "@codemirror/view";
import "../../styling/PythonIDE.scss";

// Describes the python ide run request data expected by this file.
export type PythonIdeRunRequest = {
  filename: string;
  source: string;
  stdin: string;
};

// Describes the python ide run result data expected by this file.
export type PythonIdeRunResult = {
  stdout?: string;
  stdout_transcript?: string;
  stderr?: string;
  compile_output?: string;
  message?: string;
  truncated?: boolean;
  waiting_for_input?: boolean;
};

// Describes the python ideprops data expected by this file.
type PythonIDEProps = {
  filename: string;
  source: string;
  disabled?: boolean;
  onFilenameChange: (filename: string) => void;
  onSourceChange: (source: string) => void;
  onRun: (request: PythonIdeRunRequest) => Promise<PythonIdeRunResult>;
};

// Describes the lint summary data expected by this file.
type LintSummary = {
  errors: number;
  warnings: number;
};

const MAX_PYTHON_LINE_LENGTH = 88;
const INPUT_EVENT_PATTERN =
  /\[\[\[MAAT_INPUT_B64:([A-Za-z0-9_-]*)\]\]\](?:\r?\n)?/g;

// Converts a recorded input event into the values used by the input transcript.
const decodeInputEvent = (encodedValue: string): string => {
  try {
    const base64Value = encodedValue.replace(/-/g, "+").replace(/_/g, "/");
    const paddedValue =
      base64Value + "=".repeat((4 - (base64Value.length % 4)) % 4);
    const bytes = Uint8Array.from(atob(paddedValue), (character) =>
      character.charCodeAt(0),
    );

    return new TextDecoder().decode(bytes);
  } catch {
    return "";
  }
};

// Formats console stdout for this view.
const formatConsoleStdout = (result: PythonIdeRunResult): string => {
  if (!result.stdout_transcript) return result.stdout ?? "";

  return result.stdout_transcript.replace(
    INPUT_EVENT_PATTERN,
    (_event, encodedValue: string) => `${decodeInputEvent(encodedValue)}\n`,
  );
};

// Checks whether execution paused to request another console input value.
const resultNeedsInput = (result: PythonIdeRunResult): boolean =>
  result.waiting_for_input ??
  /EOFError:\s*EOF when reading a line/i.test(result.stderr ?? "");

// Checks editor content and builds the Python diagnostics shown to the user.
const getPythonDiagnostics = (view: EditorView): Diagnostic[] => {
  const diagnostics: Diagnostic[] = [];
  const diagnosticKeys = new Set<string>();
  const { doc } = view.state;

  // Adds diagnostic for this view.
  const addDiagnostic = (diagnostic: Diagnostic) => {
    const key = `${diagnostic.from}:${diagnostic.to}:${diagnostic.severity}:${diagnostic.message}`;

    if (!diagnosticKeys.has(key)) {
      diagnosticKeys.add(key);
      diagnostics.push(diagnostic);
    }
  };

  const tree = syntaxTree(view.state);
  const isCodePosition = (position: number) => {
    for (let node: ReturnType<typeof tree.resolveInner> | null = tree.resolveInner(position, 1); node; node = node.parent) {
      if (node.name === "String" || node.name === "Comment") return false;
    }
    return true;
  };

  tree.iterate({
    enter: (node) => {
      if (!node.type.isError) return;

      addDiagnostic({
        from: node.from,
        to: node.to,
        severity: "error",
        message: "Python syntax error or incomplete statement.",
      });
    },
  });

  for (let lineNumber = 1; lineNumber <= doc.lines; lineNumber += 1) {
    const line = doc.line(lineNumber);
    const lineText = line.text;
    const trailingWhitespace = /[ \t]+$/.exec(lineText);
    const leadingWhitespace = /^[ \t]+/.exec(lineText)?.[0] ?? "";

    if (trailingWhitespace) {
      const from = line.from + trailingWhitespace.index;

      addDiagnostic({
        from,
        to: line.to,
        severity: "warning",
        message: "Remove trailing whitespace.",
      });
    }

    if (leadingWhitespace.includes("\t")) {
      addDiagnostic({
        from: line.from,
        to: line.from + leadingWhitespace.length,
        severity: "warning",
        message: "Use four spaces instead of tabs for Python indentation.",
      });
    }

    if (lineText.length > MAX_PYTHON_LINE_LENGTH) {
      addDiagnostic({
        from: line.from + MAX_PYTHON_LINE_LENGTH,
        to: line.to,
        severity: "warning",
        message: `Keep lines at or below ${MAX_PYTHON_LINE_LENGTH} characters.`,
      });
    }

    const bareExcept = /^\s*except\s*:/.exec(lineText);

    if (bareExcept && isCodePosition(line.from + lineText.indexOf("except"))) {
      const exceptOffset = lineText.indexOf("except");

      addDiagnostic({
        from: line.from + exceptOffset,
        to: line.from + exceptOffset + "except".length,
        severity: "warning",
        message: "Catch a specific exception instead of using a bare except.",
      });
    }

    const identityComparison = /(?:==|!=)\s*(None|True|False)\b/g;
    let comparisonMatch = identityComparison.exec(lineText);

    while (comparisonMatch) {
      if (!isCodePosition(line.from + comparisonMatch.index)) {
        comparisonMatch = identityComparison.exec(lineText);
        continue;
      }
      const comparedValue = comparisonMatch[1];

      addDiagnostic({
        from: line.from + comparisonMatch.index,
        to: line.from + comparisonMatch.index + comparisonMatch[0].length,
        severity: "warning",
        message:
          comparedValue === "None"
            ? 'Use "is None" or "is not None" for this comparison.'
            : `Use the ${comparedValue.toLowerCase()} value directly instead of comparing to it.`,
      });

      comparisonMatch = identityComparison.exec(lineText);
    }
  }

  return diagnostics;
};

// Formats issue count for this view.
const formatIssueCount = (count: number, singular: string) =>
  `${count} ${singular}${count === 1 ? "" : "s"}`;

// Provides the Python editor, diagnostics, execution results, and interactive console input.
const PythonIDE = ({
  filename,
  source,
  disabled = false,
  onFilenameChange,
  onSourceChange,
  onRun,
}: PythonIDEProps) => {
  // Keeps the values that drive this component’s display and user interactions in React state.
  const [sessionInput, setSessionInput] = useState<string>("");
  const [consoleInput, setConsoleInput] = useState<string>("");
  const [output, setOutput] = useState<string>("");
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [isWaitingForInput, setIsWaitingForInput] =
    useState<boolean>(false);
  const [runError, setRunError] = useState<string>("");
  const [lintSummary, setLintSummary] = useState<LintSummary>({
    errors: 0,
    warnings: 0,
  });
  // Keeps run program ref available across renders without triggering a state update.
  const runProgramRef = useRef<() => Promise<void>>(async () => undefined);
  // Keeps console input ref available across renders without triggering a state update.
  const consoleInputRef = useRef<HTMLInputElement>(null);
  const executionRef = useRef({ id: 0, running: false });

  useEffect(() => {
    executionRef.current.id += 1;
    executionRef.current.running = false;
    setIsRunning(false);
    setIsWaitingForInput(false);
    setSessionInput("");
    setConsoleInput("");
    setOutput("");
    setRunError("");
    return () => {
      executionRef.current.id += 1;
      executionRef.current.running = false;
    };
  }, [source, filename, disabled]);

  // Formats result for this view.
  const formatResult = (
    result: PythonIdeRunResult,
    waitingForInput: boolean,
  ): string => {
    const sections: string[] = [];
    const consoleStdout = formatConsoleStdout(result);

    if (consoleStdout) sections.push(consoleStdout);
    if (result.stderr && !waitingForInput) {
      sections.push(`Errors:\n${result.stderr}`);
    }
    if (result.compile_output) {
      sections.push(`Compiler output:\n${result.compile_output}`);
    }
    if (result.truncated) sections.push("[Output truncated]");
    if (result.message && sections.length === 0) sections.push(result.message);

    if (sections.length > 0) return sections.join("\n").trimEnd();
    if (waitingForInput) return "Program is waiting for input.";

    return "Program finished with no output.";
  };

  // Sends the editor program for execution and handles its returned result.
  const executeProgram = async (stdin: string, startsNewSession: boolean) => {
    if (disabled || executionRef.current.running) return;

    if (!source.trim()) {
      setRunError("Enter Python code before running the program.");
      setOutput("");
      return;
    }

    if (!/^[^\\/:*?"<>|]+\.py$/i.test(filename.trim())) {
      setRunError("Enter a valid file name ending in .py.");
      return;
    }

    const runId = ++executionRef.current.id;
    executionRef.current.running = true;
    if (startsNewSession) {
      setSessionInput("");
      setConsoleInput("");
      setOutput("");
    }

    setIsRunning(true);
    setIsWaitingForInput(false);
    setRunError("");

    try {
      const result = await onRun({ filename: filename.trim(), source, stdin });
      if (runId !== executionRef.current.id) return;
      const waitingForInput = resultNeedsInput(result);

      setOutput(formatResult(result, waitingForInput));
      setIsWaitingForInput(waitingForInput);

      if (!waitingForInput) {
        setSessionInput("");
        setConsoleInput("");
      }
    } catch (error: any) {
      if (runId !== executionRef.current.id) return;
      setIsWaitingForInput(false);
      setRunError(
        error?.response?.data?.message ||
          error?.message ||
          "The program could not be run.",
      );
    } finally {
      if (runId === executionRef.current.id) {
        executionRef.current.running = false;
        setIsRunning(false);
      }
    }
  };

  // Runs program for this view.
  const runProgram = async () => {
    await executeProgram("", true);
  };

  // Supplies the entered console input so the program can continue running.
  const sendConsoleInput = async () => {
    if (disabled || executionRef.current.running || !isWaitingForInput) return;

    const submittedValue = consoleInput;
    const nextSessionInput = `${sessionInput}${submittedValue}\n`;

    setSessionInput(nextSessionInput);
    setConsoleInput("");
    setOutput((currentOutput) => {
      const separator = currentOutput.endsWith("\n") ? "" : " ";
      return `${currentOutput}${separator}${submittedValue}\n`;
    });

    await executeProgram(nextSessionInput, false);
  };

  runProgramRef.current = runProgram;

  // Synchronizes this component with the values listed in the dependency array.
  useEffect(() => {
    if (isWaitingForInput) consoleInputRef.current?.focus();
  }, [isWaitingForInput]);

  // Recomputes editor extensions only when its dependencies change.
  const editorExtensions = useMemo(
    () => [
      python(),
      oneDark,
      lintGutter(),
      linter(getPythonDiagnostics, { delay: 250 }),
      indentUnit.of("    "),
      EditorView.lineWrapping,
      keymap.of([
        {
          key: "Mod-Enter",
          run: () => {
            void runProgramRef.current();
            return true;
          },
        },
        indentWithTab,
      ]),
    ],
    [],
  );

  // Keeps the handle editor update callback stable until its dependencies change.
  const handleEditorUpdate = useCallback((update: ViewUpdate) => {
    let errors = 0;
    let warnings = 0;

    forEachDiagnostic(update.state, (diagnostic) => {
      if (diagnostic.severity === "error") errors += 1;
      if (diagnostic.severity === "warning") warnings += 1;
    });

    setLintSummary((current) => {
      if (current.errors === errors && current.warnings === warnings) {
        return current;
      }

      return { errors, warnings };
    });
  }, []);

  const filenameIsValid = /^[^\\/:*?"<>|]+\.py$/i.test(filename.trim());
  const lintStatus =
    lintSummary.errors > 0
      ? "error"
      : lintSummary.warnings > 0
        ? "warning"
        : "clean";
  const lintMessage =
    lintSummary.errors > 0 || lintSummary.warnings > 0
      ? [
          lintSummary.errors > 0
            ? formatIssueCount(lintSummary.errors, "error")
            : "",
          lintSummary.warnings > 0
            ? formatIssueCount(lintSummary.warnings, "warning")
            : "",
        ]
          .filter(Boolean)
          .join(", ")
      : "No issues found";

  // Renders the interface using the current data and interaction state.
  return (
    <section
      className="maat-python-ide"
      aria-labelledby="maat-python-ide-title"
    >
      <header className="maat-python-ide__header">
        <div className="maat-python-ide__heading-group">
          <span className="maat-python-ide__eyebrow">Built-in workspace</span>
          <h2 id="maat-python-ide-title" className="maat-python-ide__title">
            Python IDE
          </h2>
          <p className="maat-python-ide__description">
            Write, check, and run Python here, then submit the same program for
            grading.
          </p>
        </div>

        <label className="maat-python-ide__filename-field">
          <span className="maat-python-ide__filename-label">File name</span>
          <input
            type="text"
            className={`maat-python-ide__filename-input ${
              filenameIsValid ? "" : "is-invalid"
            }`}
            value={filename}
            maxLength={128}
            disabled={disabled || isRunning}
            onChange={(event) => onFilenameChange(event.target.value)}
            aria-invalid={!filenameIsValid}
            aria-describedby="maat-python-ide-filename-help"
          />
          <span
            id="maat-python-ide-filename-help"
            className={`maat-python-ide__filename-help ${
              filenameIsValid ? "" : "is-invalid"
            }`}
          >
            {filenameIsValid
              ? "Ready to submit as a Python file."
              : "Enter a valid file name ending in .py."}
          </span>
        </label>
      </header>

      <div className="maat-python-ide__workspace">
        <div className="maat-python-ide__editor-panel">
          <div className="maat-python-ide__panel-heading">
            <div>
              <span
                id="maat-python-ide-editor-label"
                className="maat-python-ide__panel-label"
              >
                Program
              </span>
              <span className="maat-python-ide__panel-caption">
                Python 3 source code
              </span>
            </div>

            <div
              className={`maat-python-ide__lint-status is-${lintStatus}`}
              role="status"
              aria-live="polite"
              aria-atomic="true"
              title="Real-time Python syntax and style checks"
            >
              <span
                className="maat-python-ide__lint-indicator"
                aria-hidden="true"
              />
              <span>{lintMessage}</span>
            </div>
          </div>

          <div
            id="maat-python-ide-editor"
            className="maat-python-ide__editor-shell"
            role="group"
            aria-labelledby="maat-python-ide-editor-label"
          >
            <CodeMirror
              value={source}
              className="maat-python-ide__editor"
              extensions={editorExtensions}
              basicSetup={{
                bracketMatching: true,
                closeBrackets: true,
                foldGutter: true,
                highlightActiveLine: true,
                highlightActiveLineGutter: true,
                highlightSelectionMatches: true,
                lineNumbers: true,
              }}
              editable={!disabled && !isRunning}
              readOnly={disabled || isRunning}
              onChange={(value) => onSourceChange(value)}
              onUpdate={handleEditorUpdate}
              placeholder="# Start writing Python..."
              aria-label="Python program editor"
            />
          </div>

          <p className="maat-python-ide__editor-help">
            <span>Ctrl+Enter or Command+Enter runs the program.</span>
            <span>Tab indents with four spaces.</span>
            <span>Hover over a marker for lint details.</span>
          </p>
        </div>

        <aside className="maat-python-ide__io-panel">
          <div className="maat-python-ide__run-actions">
            <button
              type="button"
              className="maat-python-ide__run-button"
              disabled={disabled || isRunning || !source.trim()}
              onClick={() => void runProgram()}
              aria-label="Run program without submitting it for grading"
            >
              <span className="maat-python-ide__run-icon" aria-hidden="true">
                {isRunning ? "•••" : "▶"}
              </span>
              <span className="maat-python-ide__run-copy">
                <strong>
                  {isRunning ? "Running with Judge0..." : "Run program"}
                </strong>
                <small>Test only — does not submit</small>
              </span>
            </button>
          </div>

          <div
            className={`maat-python-ide__console ${
              runError ? "has-error" : ""
            }`}
            role="region"
            aria-labelledby="maat-python-ide-console-title"
            aria-live="polite"
          >
            <div className="maat-python-ide__console-heading">
              <h3
                id="maat-python-ide-console-title"
                className="maat-python-ide__console-title"
              >
                Output
              </h3>
              <span className="maat-python-ide__console-language">
                {runError
                  ? "Error"
                  : isWaitingForInput
                    ? "Input needed"
                    : isRunning
                      ? "Running"
                      : "Console"}
              </span>
            </div>
            {runError ? (
              <pre className="maat-python-ide__console-error">{runError}</pre>
            ) : (
              <div className="maat-python-ide__console-body">
                <pre className="maat-python-ide__console-output">
                  {output ||
                    (isRunning
                      ? "Running..."
                      : "Run the program to see output.")}
                </pre>

                {isWaitingForInput ? (
                  <div
                    className="maat-python-ide__console-input-form"
                    role="group"
                    aria-labelledby="maat-python-ide-console-input-label"
                  >
                    <label
                      id="maat-python-ide-console-input-label"
                      htmlFor="maat-python-ide-console-input"
                      className="maat-python-ide__console-input-label"
                    >
                      Program input
                    </label>
                    <div className="maat-python-ide__console-input-row">
                      <span
                        className="maat-python-ide__console-prompt"
                        aria-hidden="true"
                      >
                        &gt;
                      </span>
                      <input
                        ref={consoleInputRef}
                        id="maat-python-ide-console-input"
                        type="text"
                        className="maat-python-ide__console-input"
                        value={consoleInput}
                        disabled={disabled || isRunning}
                        onChange={(event) =>
                          setConsoleInput(event.target.value)
                        }
                        onKeyDown={(event) => {
                          if (
                            event.key !== "Enter" ||
                            event.nativeEvent.isComposing
                          ) {
                            return;
                          }

                          event.preventDefault();
                          event.stopPropagation();
                          void sendConsoleInput();
                        }}
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <button
                        type="button"
                        className="maat-python-ide__console-send-button"
                        disabled={disabled || isRunning}
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          void sendConsoleInput();
                        }}
                      >
                        Send
                      </button>
                    </div>
                  </div>
                ) : null}

                {isRunning && output ? (
                  <div
                    className="maat-python-ide__console-running"
                    role="status"
                  >
                    Continuing program...
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </aside>
      </div>
    </section>
  );
};

export default PythonIDE;
