import { useRef, useState } from "react";
import { scanWorkflow } from "./api/client.js";

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
  const maxFileSize = 10 * 1024 * 1024;
  const [selectedFile, setSelectedFile] = useState(null);
  const [workflow, setWorkflow] = useState(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [isScanning, setIsScanning] = useState(false);

  function loadFile(file) {
    setError("");
    setResult(null);

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
    loadFile(event.dataTransfer.files[0]);
  }

  async function handleScan() {
    setError("");
    setResult(null);
    setIsScanning(true);

    try {
      setResult(await scanWorkflow(workflow));
    } catch (scanError) {
      setError(scanError.message);
    } finally {
      setIsScanning(false);
    }
  }

  function clearFile() {
    setSelectedFile(null);
    setWorkflow(null);
    setResult(null);
    setError("");
    if (inputRef.current) inputRef.current.value = "";
  }

  const nodeCount = Array.isArray(workflow?.nodes) ? workflow.nodes.length : null;
  const findings = result?.findings ?? [];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#" aria-label="Tervic Check home">
          <span className="brand-mark"><Icon name="bolt" size={19} /></span>
          <span className="brand-name">tervic<span>check</span></span>
        </a>

        <div className="workspace-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="Main navigation">
          <a className="nav-item active" href="#dashboard" aria-current="page">
            <Icon name="grid" /><span>Overview</span>
          </a>
          <a className="nav-item" href="#scan">
            <Icon name="shield" /><span>Workflow scans</span>
            <span className="nav-count">01</span>
          </a>
          <a className="nav-item" href="#recent">
            <Icon name="clock" /><span>Scan history</span>
          </a>
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="note-icon"><Icon name="shield" size={17} /></span>
            <p>Your workflow files stay in your browser until you start a scan.</p>
          </div>
          <a className="nav-item settings-link" href="#settings">
            <Icon name="settings" /><span>Settings</span>
          </a>
          <div className="user-card">
            <div className="avatar">TC</div>
            <div><strong>Local workspace</strong><span>Personal plan</span></div>
            <span className="user-menu" aria-hidden="true">···</span>
          </div>
        </div>
      </aside>

      <main className="main-content" id="dashboard">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span className="crumb-slash">/</span><strong>Overview</strong></div>
          <div className="topbar-right">
            <span className="version-pill"><span className="status-dot" /> V0.2 preview</span>
            <button className="help-button" type="button" aria-label="About Tervic Check">?</button>
          </div>
        </header>

        <div className="content-wrap">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> WORKFLOW RELIABILITY</div>
              <h1>Build with confidence<span>.</span></h1>
              <p className="welcome-copy">Find workflow issues before they find you.</p>
            </div>
            <div className="platform-chip"><span className="n8n-glyph">n</span> n8n workflows <span className="chip-chevron">⌄</span></div>
          </section>

          <section className="hero-card" aria-label="Getting started">
            <div className="hero-copy">
              <div className="hero-kicker"><span className="hero-kicker-dot" /> YOUR WORKFLOW, UNDER THE MICROSCOPE</div>
              <h2>Make every workflow<br />ready for what’s next.</h2>
              <p>Start with a quick structural and security check of your n8n workflow. Your workflow is never executed.</p>
              <a className="text-link" href="#scan">Start your first scan <Icon name="arrow" size={15} /></a>
            </div>
            <div className="hero-art" aria-hidden="true">
              <div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" />
              <div className="art-node node-a"><span /><i /></div>
              <div className="art-node node-b"><span /><i /></div>
              <div className="art-node node-c"><span /><i /></div>
              <div className="art-node node-d"><span /><i /></div>
              <div className="art-line line-a" /><div className="art-line line-b" /><div className="art-line line-c" />
              <div className="art-center"><Icon name="shield" size={29} /><span className="center-spark">✦</span></div>
              <span className="art-tag tag-top">STRUCTURE <b>CHECK</b></span>
              <span className="art-tag tag-bottom">SECURITY <b>READY</b></span>
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
            className={`upload-card${selectedFile ? " has-file" : ""}`}
            onDragOver={(event) => event.preventDefault()}
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

          {error && <div className="feedback error-feedback" role="alert">{error}</div>}

          <div className="scan-actions">
            <p><Icon name="shield" size={16} /> Files are only sent to your configured backend when you scan.</p>
            <button className="scan-button" type="button" disabled={!selectedFile || isScanning} onClick={handleScan}>
              {isScanning ? <><span className="button-spinner" /> Scanning…</> : <>Scan workflow <span>→</span></>}
            </button>
          </div>

          {result && (
            <section className="results-card" aria-live="polite">
              <div className="results-heading">
                <div><div className="eyebrow section-eyebrow">SCAN RESULTS</div><h2>Review your findings</h2></div>
                <span className="result-count">{findings.length} {findings.length === 1 ? "finding" : "findings"}</span>
              </div>
              {findings.length === 0 ? (
                <p className="empty-findings"><Icon name="check" size={17} /> No issues were reported by the scanner.</p>
              ) : (
                <ul className="findings-list">
                  {findings.map((finding, index) => (
                    <li className="finding-row" key={`${finding.ruleId ?? "finding"}-${index}`}>
                      <span className={`severity severity-${String(finding.severity ?? "info").toLowerCase()}`}>{finding.severity ?? "Info"}</span>
                      <div><strong>{finding.title ?? finding.message ?? "Workflow finding"}</strong><p>{finding.location ?? "Workflow"}{finding.remediation ? ` · ${finding.remediation}` : ""}</p></div>
                      <code>{finding.ruleId ?? "RULE"}</code>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <section className="bottom-grid" id="recent">
            <article className="info-card">
              <div className="info-icon green"><Icon name="shield" size={19} /></div>
              <div><h3>Made for safer workflows</h3><p>Catch structural errors and potential hardcoded secrets before deployment.</p></div>
              <span className="info-arrow">↗</span>
            </article>
            <article className="info-card">
              <div className="info-icon lavender"><Icon name="bolt" size={19} /></div>
              <div><h3>Checks, not execution</h3><p>Tervic Check analyzes your workflow file. It never runs your automation.</p></div>
              <span className="info-arrow">↗</span>
            </article>
          </section>

          <footer className="page-footer"><span>TERVIC CHECK <i>·</i> WORKFLOW RELIABILITY</span><span>Build <b>·</b> Check <b>·</b> Fix <b>·</b> Test <b>·</b> Deploy</span></footer>
        </div>
      </main>
    </div>
  );
}

export default App;
