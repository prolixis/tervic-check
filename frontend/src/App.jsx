import { useEffect, useRef, useState } from "react";
import {
  getCurrentUser,
  getScan,
  listScans,
  login,
  logout,
  register,
  scanWorkflow,
} from "./api/client.js";
import ScanResults from "./components/ScanResults.jsx";

function Icon({ name, size = 20 }) {
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
    shield: <><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z" /><path d="m9 12 2 2 4-4" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.7 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.7-1l-1.7.7-1.4-2.4 1.4-1.1a8 8 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.7-1l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.7 1l1.7-.7 1.4 2.4-1.4 1.1a8 8 0 0 1 0 2Z" /></>,
    upload: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="m17 8-5-5-5 5M12 3v12" /></>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13h8M8 17h8" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    arrow: <><path d="M7 17 17 7M7 7h10v10" /></>,
    close: <><path d="m18 6-12 12M6 6l12 12" /></>,
    bolt: <path d="m13 2-3 8h7l-6 12 2-9H6l7-11Z" />,
  };

  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

function App() {
  const inputRef = useRef(null);
  const helpButtonRef = useRef(null);
  const helpDialogRef = useRef(null);
  const maxFileSize = 10 * 1024 * 1024;
  const [selectedFile, setSelectedFile] = useState(null);
  const [workflow, setWorkflow] = useState(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [isScanning, setIsScanning] = useState(false);
  const [authMode, setAuthMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState(() => sessionStorage.getItem("tervic_session"));
  const [user, setUser] = useState(null);
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [history, setHistory] = useState([]);
  const [historyError, setHistoryError] = useState("");
  const [isHelpOpen, setIsHelpOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [activeSection, setActiveSection] = useState("dashboard");
  const [analyzedWorkflow, setAnalyzedWorkflow] = useState(null);

  useEffect(() => {
    if (!token) {
      setUser(null);
      setHistory([]);
      return;
    }

    let active = true;
    getCurrentUser(token)
      .then((currentUser) => {
        if (!active) return;
        setUser(currentUser);
        setAuthError("");
        return listScans(token).then((scans) => {
          if (active) setHistory(scans);
        });
      })
      .catch((sessionError) => {
        if (!active) return;
        if (sessionError.status === 401) {
          sessionStorage.removeItem("tervic_session");
          setToken(null);
          setUser(null);
          setAuthError("Your session expired. Sign in again to continue.");
          return;
        }
        setHistoryError(sessionError.message);
      });

    return () => { active = false; };
  }, [token]);

  useEffect(() => {
    if (!isHelpOpen) return undefined;

    helpDialogRef.current?.focus();
    function handleHelpKeyDown(event) {
      if (event.key === "Escape") {
        setIsHelpOpen(false);
        helpButtonRef.current?.focus();
        return;
      }

      if (event.key === "Tab") {
        const focusable = helpDialogRef.current?.querySelectorAll(
          'button:not(:disabled), [href], input:not(:disabled), [tabindex]:not([tabindex="-1"])',
        );
        if (!focusable?.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }

    window.addEventListener("keydown", handleHelpKeyDown);
    return () => window.removeEventListener("keydown", handleHelpKeyDown);
  }, [isHelpOpen]);

  useEffect(() => {
    function syncActiveSection() {
      const section = window.location.hash.slice(1);
      if (["scan", "account", "results"].includes(section)) {
        setActiveSection("scan");
      } else {
        setActiveSection(["recent", "about"].includes(section) ? section : "dashboard");
      }
    }

    syncActiveSection();
    window.addEventListener("hashchange", syncActiveSection);
    return () => window.removeEventListener("hashchange", syncActiveSection);
  }, []);

  async function handleAuth(event) {
    event.preventDefault();
    setAuthError("");
    setAuthBusy(true);
    try {
      const response = authMode === "register"
        ? await register(email, password)
        : await login(email, password);
      sessionStorage.setItem("tervic_session", response.token);
      setPassword("");
      setToken(response.token);
      setUser(response.user);
    } catch (authRequestError) {
      setAuthError(authRequestError.message);
    } finally {
      setAuthBusy(false);
    }
  }

  async function handleLogout() {
    try {
      if (token) await logout(token);
    } catch (logoutError) {
      setAuthError(logoutError.message);
    } finally {
      sessionStorage.removeItem("tervic_session");
      setToken(null);
      setUser(null);
      setHistory([]);
      setResult(null);
      setAnalyzedWorkflow(null);
    }
  }

  async function handleOpenScan(scanId) {
    try {
      const scan = await getScan(token, scanId);
      setResult({
        ...(scan.result || {}),
        findings: scan.result?.findings || scan.findings || [],
        totalFindings: scan.result?.totalFindings ?? scan.totalFindings,
        platform: scan.platform,
        scannerVersion: scan.result?.scannerVersion || null,
        workflow: scan.workflow,
        workflowMetrics: scan.workflowMetrics || scan.result?.workflowMetrics || null,
        checkSummary: scan.result?.checkSummary || null,
        analysisCoverage: scan.result?.analysisCoverage || null,
        status: scan.status,
        durationMs: scan.durationMs,
        startedAt: scan.startedAt,
        completedAt: scan.completedAt,
      });
      setAnalyzedWorkflow(null);
      setError("");
      window.location.hash = "results";
      window.setTimeout(() => {
        document.getElementById("results")?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 0);
    } catch (historyRequestError) {
      if (historyRequestError.status === 401) {
        sessionStorage.removeItem("tervic_session");
        setToken(null);
        setUser(null);
      }
      setHistoryError(historyRequestError.message);
    }
  }

  function loadFile(file) {
    setError("");
    setResult(null);
    setAnalyzedWorkflow(null);

    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".json")) {
      setSelectedFile(null);
      setWorkflow(null);
      setError("Choose a workflow exported as a .json file.");
      return;
    }
    if (file.size > maxFileSize) {
      setSelectedFile(null);
      setWorkflow(null);
      setError("This file is larger than 10 MB. Choose a smaller workflow export.");
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(reader.result);
        setSelectedFile(file);
        setWorkflow(parsed);
      } catch {
        setSelectedFile(null);
        setWorkflow(null);
        setError("This file is not valid JSON. Check it and try again.");
      }
    };
    reader.onerror = () => {
      setSelectedFile(null);
      setWorkflow(null);
      setError("The file could not be read. Try selecting it again.");
    };
    reader.readAsText(file);
  }

  function handleDrop(event) {
    event.preventDefault();
    setIsDragging(false);
    loadFile(event.dataTransfer.files[0]);
  }

  async function handleScan() {
    setError("");
    setResult(null);
    setAnalyzedWorkflow(null);
    setIsScanning(true);

    try {
      const scan = await scanWorkflow(workflow, token);
      setResult(scan);
      setAnalyzedWorkflow(workflow);
      setHistory(await listScans(token));
      setHistoryError("");
    } catch (scanError) {
      if (scanError.status === 401) {
        sessionStorage.removeItem("tervic_session");
        setToken(null);
        setUser(null);
      }
      setError(scanError.message);
    } finally {
      setIsScanning(false);
    }
  }

  function clearFile() {
    setSelectedFile(null);
    setWorkflow(null);
    setResult(null);
    setAnalyzedWorkflow(null);
    setError("");
    if (inputRef.current) inputRef.current.value = "";
  }

  const nodeCount = Array.isArray(workflow?.nodes) ? workflow.nodes.length : null;
  const activeStep = result ? 4 : !selectedFile ? 1 : !user ? 2 : 3;
  const steps = [
    { number: 1, label: "Choose a workflow", anchor: "#scan", complete: Boolean(selectedFile) },
    { number: 2, label: user ? "Account ready" : "Sign in", anchor: "#account", complete: Boolean(user) },
    { number: 3, label: "Run and review", anchor: result ? "#results" : "#scan", complete: Boolean(result) },
  ];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#" aria-label="Tervic Flowcheck home">
          <span className="brand-mark"><Icon name="bolt" size={19} /></span>
          <span className="brand-name">Tervic <span>Flowcheck</span></span>
        </a>

        <div className="workspace-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="Main navigation">
          <a className={`nav-item${activeSection === "dashboard" ? " active" : ""}`} href="#dashboard" aria-label="Overview" aria-current={activeSection === "dashboard" ? "page" : undefined}>
            <Icon name="grid" /><span>Overview</span>
          </a>
          <a className={`nav-item${activeSection === "scan" ? " active" : ""}`} href="#scan" aria-label="Workflow scans" aria-current={activeSection === "scan" ? "page" : undefined}>
            <Icon name="shield" /><span>Workflow scans</span>
            <span className="nav-count">01</span>
          </a>
          <a className={`nav-item${activeSection === "recent" ? " active" : ""}`} href="#recent" aria-label="Scan history" aria-current={activeSection === "recent" ? "page" : undefined}>
            <Icon name="clock" /><span>Scan history</span>
          </a>
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="note-icon"><Icon name="shield" size={17} /></span>
            <p>Workflow contents are analyzed but never saved; scan results are linked to your account.</p>
          </div>
          <a className={`nav-item settings-link${activeSection === "about" ? " active" : ""}`} href="#about" aria-label="About and privacy" aria-current={activeSection === "about" ? "page" : undefined}>
            <Icon name="settings" /><span>About &amp; privacy</span>
          </a>
          <div className="user-card">
            <div className="avatar">{user ? user.email.slice(0, 2).toUpperCase() : "TC"}</div>
            <div><strong>{user ? user.email : "Account required"}</strong><span>{user ? "Private workspace" : "Sign in to scan"}</span></div>
            <span className="user-menu" aria-hidden="true">{user ? "●" : "···"}</span>
          </div>
        </div>
      </aside>

      <main className="main-content" id="dashboard">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span className="crumb-slash">/</span><strong>Overview</strong></div>
          <div className="topbar-right">
            <span className="version-pill"><span className="status-dot" /> V0.2 preview</span>
            <button
              ref={helpButtonRef}
              className="help-button"
              type="button"
              aria-label="How Tervic Flowcheck works"
              aria-haspopup="dialog"
              aria-expanded={isHelpOpen}
              onClick={() => setIsHelpOpen(true)}
            >
              ?
            </button>
          </div>
        </header>

        {isHelpOpen && (
          <div
            className="help-overlay"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) {
                setIsHelpOpen(false);
                helpButtonRef.current?.focus();
              }
            }}
          >
            <section
              ref={helpDialogRef}
              className="help-dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="help-heading"
              tabIndex="-1"
            >
              <div className="help-dialog-heading">
                <div>
                  <div className="eyebrow section-eyebrow">QUICK GUIDE</div>
                  <h2 id="help-heading">How a workflow check works</h2>
                </div>
                <button
                  className="remove-file"
                  type="button"
                  aria-label="Close guide"
                  onClick={() => {
                    setIsHelpOpen(false);
                    helpButtonRef.current?.focus();
                  }}
                >
                  <Icon name="close" size={18} />
                </button>
              </div>
              <ol className="help-steps">
                <li><strong>Export your workflow from n8n.</strong> Choose the workflow’s JSON export file.</li>
                <li><strong>Sign in to your private account.</strong> This keeps your scan history available to you.</li>
                <li><strong>Review the checks.</strong> The scanner looks for common structure problems and possible hardcoded secrets.</li>
              </ol>
              <p className="help-privacy"><Icon name="shield" size={17} /> Your workflow is only sent when you start a scan. It is analyzed, not executed or saved.</p>
              <button
                className="scan-button help-done"
                type="button"
                onClick={() => {
                  setIsHelpOpen(false);
                  helpButtonRef.current?.focus();
                }}
              >
                Got it
              </button>
            </section>
          </div>
        )}

        <div className="content-wrap">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> WORKFLOW RELIABILITY</div>
              <h1>Build with confidence<span>.</span></h1>
              <p className="welcome-copy">Find workflow issues before they find you.</p>
            </div>
            <div className="platform-chip"><span className="n8n-glyph">n</span> n8n workflows <span className="chip-chevron">⌄</span></div>
          </section>

          <nav className="scan-stepper" aria-label="Scan steps">
            {steps.map((step) => (
              <a
                className={`scan-step${step.complete ? " complete" : ""}${activeStep === step.number ? " current" : ""}`}
                href={step.anchor}
                key={step.number}
                aria-current={activeStep === step.number ? "step" : undefined}
              >
                <span className="scan-step-number">{step.complete ? <Icon name="check" size={13} /> : step.number}</span>
                <span>{step.label}</span>
              </a>
            ))}
          </nav>

          <section className="hero-card" aria-label="Getting started">
            <div className="hero-copy">
              <div className="hero-kicker"><span className="hero-kicker-dot" /> STATIC ANALYSIS FOR AUTOMATION</div>
              <h2>Review workflow risks<br />before deployment.</h2>
              <p>Check structure, security patterns, reliability, and supported data flows. Your workflow is analyzed, never executed.</p>
              <a className="text-link" href="#scan">Start your first scan <Icon name="arrow" size={15} /></a>
            </div>
            <div className="hero-art" aria-hidden="true">
              <div className="hero-status-panel">
                <div className="hero-status-heading">ANALYSIS MODE <span>● ACTIVE</span></div>
                <strong>Configuration only</strong>
                <p>No workflow execution. No saved workflow JSON in scan history.</p>
              </div>
            </div>
            <div className="hero-index">01 <span>/ 03</span></div>
          </section>

          <section className="section-heading" id="scan">
            <div>
              <div className="eyebrow section-eyebrow">GET STARTED</div>
              <h2>Run a workflow check</h2>
              <p>Upload an n8n JSON export to inspect its structure and potential risks.</p>
            </div>
            <span className="step-label"><span>01</span> OF 03</span>
          </section>

          <section
            className={`upload-card${selectedFile ? " has-file" : ""}${isDragging ? " is-dragging" : ""}`}
            onDragOver={(event) => event.preventDefault()}
            onDragEnter={() => setIsDragging(true)}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) setIsDragging(false);
            }}
            onDrop={handleDrop}
          >
            {!selectedFile ? (
              <div className="drop-content">
                <div className="upload-icon"><Icon name="upload" size={23} /></div>
                <div className="drop-text">
                  <h3>Drop your workflow file here</h3>
                  <p>or <button className="browse-button" type="button" onClick={() => inputRef.current?.click()}>browse files</button> from your computer</p>
                </div>
                <span className="file-format">JSON <span>·</span> MAX 10 MB</span>
              </div>
            ) : (
              <div className="file-content">
                <div className="file-icon"><Icon name="file" size={22} /></div>
                <div className="file-details">
                  <h3>{selectedFile.name}</h3>
                  <p>{nodeCount === null ? "JSON parsed · node count unavailable" : `${nodeCount} ${nodeCount === 1 ? "node" : "nodes"} detected`} <span>·</span> {(selectedFile.size / 1024).toFixed(1)} KB</p>
                </div>
                <span className="file-ready"><Icon name="check" size={15} /> READY</span>
                <button className="remove-file" type="button" aria-label="Remove selected file" onClick={clearFile}><Icon name="close" size={17} /></button>
              </div>
            )}
            <input
              ref={inputRef}
              className="visually-hidden"
              type="file"
              accept=".json,application/json"
              aria-label="Choose an n8n workflow JSON file"
              onChange={(event) => loadFile(event.target.files[0])}
            />
          </section>
          <details className="beginner-tip">
            <summary>New to n8n? Where do I find my workflow JSON?</summary>
            <p>Open your workflow in n8n and use its menu to download or export the workflow as JSON. Choose that file here. You can scan only workflows exported from n8n in this preview.</p>
          </details>

          {!user ? (
            <section className="auth-card" id="account" aria-labelledby="account-heading">
              <div>
                <div className="eyebrow section-eyebrow">PERSONAL SCAN HISTORY</div>
                <h2 id="account-heading">{authMode === "register" ? "Create your account" : "Sign in to Tervic Flowcheck"}</h2>
                <p>Sign in to run a scan and keep its findings in your private history.</p>
              </div>
              <form className="auth-form" onSubmit={handleAuth}>
                <label>Email
                  <input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required maxLength={254} />
                </label>
                <label>Password
                  <input type="password" autoComplete={authMode === "register" ? "new-password" : "current-password"} value={password} onChange={(event) => setPassword(event.target.value)} required minLength={authMode === "register" ? 12 : undefined} maxLength={128} />
                  {authMode === "register" && <span className="field-hint">Use at least 12 characters.</span>}
                </label>
                <button className="scan-button" type="submit" disabled={authBusy}>
                  {authBusy ? "Please wait…" : authMode === "register" ? "Create account" : "Sign in"}
                </button>
                <button className="auth-switch" type="button" onClick={() => { setAuthMode(authMode === "register" ? "login" : "register"); setAuthError(""); }}>
                  {authMode === "register" ? "Already have an account? Sign in" : "New here? Create an account"}
                </button>
              </form>
              {authError && <div className="feedback error-feedback" role="alert">{authError}</div>}
            </section>
          ) : (
            <div className="account-bar" id="account">
              <span>Signed in as <strong>{user.email}</strong></span>
              <button type="button" onClick={handleLogout}>Sign out</button>
            </div>
          )}

          {error && <div className="feedback error-feedback" role="alert">{error}</div>}

          <div className="scan-actions">
            <p><Icon name="shield" size={16} /> Files are only sent to your configured backend when you scan.</p>
            <button className="scan-button" type="button" disabled={!user || !selectedFile || isScanning} onClick={handleScan}>
              {isScanning ? <><span className="button-spinner" /> Scanning…</> : <>Scan workflow <span>→</span></>}
            </button>
          </div>
          {!user && <p className="sign-in-hint">Sign in or create an account before scanning.</p>}

          {result && (
            <ScanResults
              key={result.id || result.startedAt || "current-scan"}
              result={result}
              workflow={analyzedWorkflow}
              fallbackName={analyzedWorkflow ? selectedFile?.name : undefined}
            />
          )}

          <section className="history-card" id="recent">
            <div className="results-heading">
              <div><div className="eyebrow section-eyebrow">YOUR ACCOUNT</div><h2>Scan history</h2></div>
              <span className="result-count">{history.length} recent</span>
            </div>
            {historyError && <p className="history-error" role="alert">{historyError}</p>}
            {!user ? (
              <p className="empty-history">Sign in to view your saved scan history.</p>
            ) : history.length === 0 ? (
              <p className="empty-history">Your completed scans will appear here.</p>
            ) : (
              <ul className="history-list">
                {history.map((scan) => (
                  <li key={scan.id}>
                    <button className="history-item" type="button" onClick={() => handleOpenScan(scan.id)}>
                      <span className={`history-status history-status-${scan.status}`}>{scan.status}</span>
                      <span className="history-name">{scan.workflow.name || "Untitled workflow"}</span>
                      <span className="history-meta">{scan.platform || "n8n"} · {scan.totalFindings} findings · {scan.durationMs === null ? "—" : `${scan.durationMs} ms`}</span>
                      <span className="history-severity" aria-label="Severity summary">
                        <span className="severity-chip high">H {scan.severitySummary?.high ?? "—"}</span>
                        <span className="severity-chip medium">M {scan.severitySummary?.medium ?? "—"}</span>
                      </span>
                      <time dateTime={scan.startedAt}>{new Date(scan.startedAt).toLocaleString()}</time>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="bottom-grid" id="about" aria-label="About and privacy">
            <article className="info-card">
              <div className="info-icon green"><Icon name="shield" size={19} /></div>
              <div><h3>Made for safer workflows</h3><p>Catch structural errors and potential hardcoded secrets before deployment.</p></div>
              <span className="info-arrow">↗</span>
            </article>
            <article className="info-card">
              <div className="info-icon lavender"><Icon name="bolt" size={19} /></div>
              <div><h3>Checks, not execution</h3><p>Tervic Flowcheck analyzes your workflow file. It never runs your automation.</p></div>
              <span className="info-arrow">↗</span>
            </article>
          </section>

          <footer className="page-footer"><span>TERVIC FLOWCHECK <i>·</i> WORKFLOW RELIABILITY</span><span>Build <b>·</b> Check <b>·</b> Fix <b>·</b> Test <b>·</b> Deploy</span></footer>
        </div>
      </main>
    </div>
  );
}

export default App;
