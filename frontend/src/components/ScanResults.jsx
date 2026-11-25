import { useEffect, useMemo, useRef, useState } from "react";

const MAX_GRAPH_CONNECTIONS = 500;

const RESULT_TABS = [
  ["overview", "Overview"],
  ["findings", "Findings"],
  ["workflow", "Workflow analysis"],
  ["recommendations", "Recommendations"],
  ["technical", "Technical details"],
];

const SEVERITY_CARDS = [
  ["critical", "Critical"],
  ["high", "High"],
  ["medium", "Medium"],
  ["low", "Low"],
  ["informational", "Informational"],
];

const RECOMMENDATION_GROUPS = [
  ["security", "Security improvements"],
  ["reliability", "Reliability improvements"],
  ["structure", "Structure corrections"],
  ["dataflow", "Data-flow improvements"],
  ["performance", "Performance improvements"],
  ["maintainability", "Maintainability improvements"],
];

function categoryFor(ruleId = "") {
  if (ruleId.startsWith("SECURITY") || ruleId.startsWith("SECRET")) return "security";
  if (ruleId.startsWith("RELIABILITY")) return "reliability";
  if (ruleId.startsWith("DATAFLOW")) return "dataflow";
  if (ruleId === "GRAPH004") return "performance";
  if (ruleId.startsWith("GRAPH")) return "structure";
  if (ruleId.startsWith("N8N") || ruleId.startsWith("TERVIC")) return "structure";
  return "maintainability";
}

function categoryLabel(category) {
  return {
    security: "Security",
    reliability: "Reliability",
    structure: "Structure",
    dataflow: "Data flow",
    performance: "Performance",
    maintainability: "Maintainability",
  }[category] || "Other";
}

function priorityFor(finding) {
  if (finding.severity === "error") return "high";
  if (finding.severity === "warning") return "medium";
  return finding.severity === "critical" ? "critical" :
    finding.severity === "low" ? "low" : "informational";
}

function findingNode(finding) {
  return finding.nodeName || finding.nodeId || "Workflow-wide";
}

function findingKey(finding, index) {
  return `${finding.ruleId || "finding"}:${finding.location || index}:${index}`;
}

function formatDuration(durationMs) {
  if (!Number.isFinite(durationMs)) return "Not recorded";
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(2)} s`;
}

function formatCount(value) {
  return Number.isInteger(value) ? value.toLocaleString() : "Unavailable";
}

function severityCounts(findings) {
  const counts = Object.fromEntries(SEVERITY_CARDS.map(([key]) => [key, 0]));
  for (const finding of findings) counts[priorityFor(finding)] += 1;
  return counts;
}

function impactFor(finding) {
  switch (categoryFor(finding.ruleId)) {
    case "security":
      return "If this configuration is reachable in practice, it could expose credentials or data, or grant an unintended execution path. The scanner did not execute the workflow or verify exploitability.";
    case "reliability":
      return "This configuration may affect failure handling or repeat an operation. The actual impact depends on the workflow's runtime inputs and service behavior.";
    case "dataflow":
      return "A downstream node may receive an undefined or unexpected value on the analyzed path. Only the documented node subset is modeled.";
    case "performance":
      return "A repeated or cyclic path may perform extra work. No runtime cost or performance impact was measured.";
    case "structure":
      return "The reported structure may prevent the workflow from loading or route data as intended. Runtime behavior was not tested.";
    default:
      return "The scanner cannot determine runtime impact from this configuration alone. Review the evidence and surrounding workflow context.";
  }
}

function findingNodeIndexes(finding, nodes) {
  const indexes = new Set();
  const locationMatch = finding.location?.match(/^nodes\[(\d+)\]/);
  if (locationMatch && Number(locationMatch[1]) < nodes.length) {
    indexes.add(Number(locationMatch[1]));
  }
  nodes.forEach((node, index) => {
    if (finding.nodeId && finding.nodeId !== "[REDACTED]" && node?.id === finding.nodeId) {
      indexes.add(index);
    }
    if (finding.nodeName && finding.nodeName !== "[REDACTED]" && node?.name === finding.nodeName) {
      indexes.add(index);
    }
  });
  return indexes;
}

function buildGraph(workflow, findings) {
  if (!workflow || !Array.isArray(workflow.nodes) || !workflow.connections ||
      typeof workflow.connections !== "object" || Array.isArray(workflow.connections)) {
    return { error: "The workflow JSON is not available or does not contain renderable nodes and connections." };
  }
  if (workflow.nodes.length > 80) {
    return { error: "This workflow has more than 80 nodes. The graph is omitted to keep the view responsive." };
  }

  const nodes = [];
  for (const [index, node] of workflow.nodes.entries()) {
    if (!node || !Array.isArray(node.position) || node.position.length < 2 ||
        !Number.isFinite(node.position[0]) || !Number.isFinite(node.position[1])) {
      return { error: "One or more nodes have no valid position. The graph requires the original n8n layout coordinates." };
    }
    nodes.push({
      index,
      x: node.position[0],
      y: node.position[1],
      type: typeof node.type === "string" && /^n8n-nodes-base\.[A-Za-z0-9._-]{1,100}$/.test(node.type)
        ? node.type
        : "Other node type",
      isEntry: typeof node.type === "string" && /(trigger|webhook|schedule|cron)/i.test(node.type),
      source: node,
    });
  }

  const namedNodes = new Map();
  workflow.nodes.forEach((node, index) => {
    if (typeof node?.name !== "string") return;
    const matches = namedNodes.get(node.name) || [];
    matches.push(index);
    namedNodes.set(node.name, matches);
  });

  const edges = [];
  let omittedConnections = 0;
  let connectionLimitReached = false;
  sourceLoop: for (const sourceName in workflow.connections) {
    if (!Object.prototype.hasOwnProperty.call(workflow.connections, sourceName)) continue;
    const outputs = workflow.connections[sourceName];
    const sourceMatches = namedNodes.get(sourceName) || [];
    if (sourceMatches.length !== 1 || !outputs || typeof outputs !== "object" || Array.isArray(outputs)) {
      omittedConnections += 1;
      continue;
    }
    let outputIndex = 0;
    for (const outputName in outputs) {
      if (!Object.prototype.hasOwnProperty.call(outputs, outputName)) continue;
      const currentOutputIndex = outputIndex;
      outputIndex += 1;
      const branches = outputs[outputName];
      if (!Array.isArray(branches)) {
        omittedConnections += 1;
        continue;
      }
      for (const [branchIndex, branch] of branches.entries()) {
        if (!Array.isArray(branch)) {
          omittedConnections += 1;
          continue;
        }
        for (const connection of branch) {
          const validConnection = connection && typeof connection === "object" &&
            typeof connection.type === "string" && Number.isInteger(connection.index) &&
            connection.index >= 0;
          const targetMatches = validConnection && typeof connection.node === "string"
            ? namedNodes.get(connection.node) || []
            : [];
          if (targetMatches.length !== 1) {
            omittedConnections += 1;
            continue;
          }
          edges.push({
            source: sourceMatches[0],
            target: targetMatches[0],
            connectionType: connection.type === "main" || connection.type === "error" ||
              /^ai_[a-z0-9_]{1,30}$/.test(connection.type) ? connection.type : "other",
            outputIndex: currentOutputIndex,
            branchIndex,
            targetIndex: connection.index,
          });
          if (edges.length >= MAX_GRAPH_CONNECTIONS) {
            connectionLimitReached = true;
            break sourceLoop;
          }
        }
      }
    }
  }

  const adjacency = nodes.map(() => new Set());
  const degree = new Array(nodes.length).fill(0);
  for (const edge of edges) {
    degree[edge.source] += 1;
    degree[edge.target] += 1;
    adjacency[edge.source].add(edge.target);
    adjacency[edge.target].add(edge.source);
  }
  const component = new Array(nodes.length).fill(-1);
  let componentCount = 0;
  for (let index = 0; index < nodes.length; index += 1) {
    if (component[index] !== -1) continue;
    const queue = [index];
    component[index] = componentCount;
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      for (const neighbor of adjacency[queue[cursor]]) {
        if (component[neighbor] !== -1) continue;
        component[neighbor] = componentCount;
        queue.push(neighbor);
      }
    }
    componentCount += 1;
  }
  const entryComponents = new Set(nodes.filter((node) => node.isEntry).map((node) => component[node.index]));
  const affected = new Set();
  for (const finding of findings) {
    for (const index of findingNodeIndexes(finding, workflow.nodes)) affected.add(index);
  }

  return {
    nodes: nodes.map((node) => ({
      ...node,
      isIsolated: degree[node.index] === 0,
      isDisconnected: componentCount > 1 && !entryComponents.has(component[node.index]),
      isAffected: affected.has(node.index),
    })),
    edges,
    omittedConnections,
    connectionLimitReached,
  };
}

function getTechnicalConnections(workflow) {
  const graph = buildGraph(workflow, []);
  if (graph.error) return graph;
  const output = [];
  for (const edge of graph.edges) {
    output.push({
      source: edge.source + 1,
      target: edge.target + 1,
      connectionType: edge.connectionType,
      outputIndex: edge.outputIndex + 1,
      branchIndex: edge.branchIndex + 1,
      targetIndex: edge.targetIndex,
    });
  }
  return {
    connections: output,
    omittedConnections: graph.omittedConnections,
    connectionLimitReached: graph.connectionLimitReached,
  };
}

function WorkflowGraph({ workflow, findings, selectedFinding }) {
  const graph = useMemo(() => buildGraph(workflow, findings), [workflow, findings]);
  if (graph.error) {
    return (
      <div className="graph-fallback">
        <span className="fallback-mark">i</span>
        <div>
          <strong>Workflow graph unavailable</strong>
          <p>{graph.error} {workflow ? "No graph is generated from incomplete workflow data." : "Saved scans do not retain workflow JSON, so their graphs cannot be reconstructed."}</p>
        </div>
      </div>
    );
  }
  if (graph.nodes.length === 0) {
    return <p className="empty-state">This scan contains no nodes to visualize.</p>;
  }

  const minX = Math.min(...graph.nodes.map((node) => node.x));
  const minY = Math.min(...graph.nodes.map((node) => node.y));
  const maxX = Math.max(...graph.nodes.map((node) => node.x));
  const maxY = Math.max(...graph.nodes.map((node) => node.y));
  if (maxX - minX > 4000 || maxY - minY > 1600) {
    return (
      <div className="graph-fallback">
        <span className="fallback-mark">i</span>
        <div>
          <strong>Workflow layout exceeds the visualization bounds</strong>
          <p>Node positions are preserved in the export, but this view does not scale extreme coordinates.</p>
        </div>
      </div>
    );
  }

  const width = Math.max(760, maxX - minX + 300);
  const height = Math.max(360, maxY - minY + 190);
  const positions = graph.nodes.map((node) => ({
    x: node.x - minX + 80,
    y: node.y - minY + 75,
  }));
  const selectedIndexes = selectedFinding
    ? findingNodeIndexes(selectedFinding, graph.nodes.map((node) => node.source))
    : new Set();

  return (
    <>
      <div className="graph-legend">
        <span><i className="legend-entry" /> Entry-point candidate</span>
        <span><i className="legend-affected" /> Affected node</span>
        <span><i className="legend-isolated" /> Disconnected / isolated</span>
      </div>
      <div className="workflow-graph-scroll" role="region" aria-label="Workflow graph" tabIndex="0">
        <svg
          className="workflow-graph"
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${graph.nodes.length} workflow nodes and ${graph.edges.length} connections`}
        >
          <defs>
            <marker id="graph-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" className="graph-arrowhead" />
            </marker>
          </defs>
          {graph.edges.map((edge, index) => {
            const start = positions[edge.source];
            const end = positions[edge.target];
            const x1 = start.x + 174;
            const y1 = start.y + 32;
            const x2 = end.x;
            const y2 = end.y + 32;
            const curve = Math.max(42, Math.abs(x2 - x1) / 2);
            return (
              <path
                key={`${edge.source}-${edge.target}-${index}`}
                className="graph-edge"
                d={`M ${x1} ${y1} C ${x1 + curve} ${y1}, ${x2 - curve} ${y2}, ${x2} ${y2}`}
                markerEnd="url(#graph-arrow)"
              />
            );
          })}
          {graph.nodes.map((node) => {
            const point = positions[node.index];
            const selected = selectedIndexes.has(node.index);
            const className = [
              "graph-node",
              node.isEntry ? "is-entry" : "",
              node.isIsolated || node.isDisconnected ? "is-isolated" : "",
              node.isAffected ? "is-affected" : "",
              selected ? "is-selected" : "",
            ].filter(Boolean).join(" ");
            return (
              <g className={className} key={node.index} transform={`translate(${point.x}, ${point.y})`}>
                <rect width="174" height="64" rx="9" />
                <circle cx="17" cy="19" r="4" />
                <text x="29" y="23">Node {String(node.index + 1).padStart(2, "0")}</text>
                <text className="graph-node-type" x="14" y="46">{node.type}</text>
              </g>
            );
          })}
        </svg>
      </div>
      <p className="graph-caption">
        Positions and connections come from the uploaded workflow. Node names and parameter values are intentionally omitted.
        {graph.omittedConnections > 0 && ` Some malformed or ambiguous connection data is not drawn (${graph.omittedConnections} skipped item(s)).`}
        {graph.connectionLimitReached && ` Rendering stops at ${MAX_GRAPH_CONNECTIONS} visible connections.`}
      </p>
    </>
  );
}

function FindingDetail({ finding, onClose, onViewWorkflow }) {
  const [copyStatus, setCopyStatus] = useState("");
  const closeButtonRef = useRef(null);
  const location = finding.location || "Workflow";
  const confidence = finding.certainty === "confirmed" ? "Confirmed pattern" : "Heuristic / potential";
  const category = categoryFor(finding.ruleId);

  useEffect(() => {
    closeButtonRef.current?.focus();
    function handleKeyDown(event) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  async function copyLocation() {
    try {
      await navigator.clipboard.writeText(location);
      setCopyStatus("Location copied");
    } catch {
      setCopyStatus("Clipboard access is unavailable in this browser");
    }
  }

  return (
    <div className="detail-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="finding-detail" role="dialog" aria-modal="true" aria-labelledby="finding-detail-title">
        <header className="detail-header">
          <div>
            <span className={`severity-chip ${priorityFor(finding)}`}>{finding.severity || "info"}</span>
            <span className="category-chip">{categoryLabel(category)}</span>
            <h2 id="finding-detail-title">{finding.title || "Workflow finding"}</h2>
            <code>{finding.ruleId || "RULE"}</code>
          </div>
          <button ref={closeButtonRef} className="icon-button" type="button" onClick={onClose} aria-label="Close finding details">×</button>
        </header>
        <div className="detail-body">
          <section>
            <h3>What was detected</h3>
            <p>{finding.message || "No explanation was stored with this finding."}</p>
          </section>
          <section>
            <h3>Why it matters</h3>
            <p>{finding.message || "The scanner did not store an explanation for this rule."}</p>
          </section>
          <section>
            <h3>Potential impact</h3>
            <p>{impactFor(finding)}</p>
          </section>
          <section>
            <h3>Evidence</h3>
            <p>{finding.message || "No evidence text was stored with this finding."}</p>
            <div className="location-value">
              <code>{location}</code>
              <button className="secondary-button compact" type="button" onClick={copyLocation}>Copy location</button>
            </div>
            {copyStatus && <p className="copy-status" role="status">{copyStatus}</p>}
          </section>
          <div className="detail-two-column">
            <section>
              <h3>Affected node</h3>
              <p>{findingNode(finding)}</p>
            </section>
            <section>
              <h3>Confidence</h3>
              <p>{confidence}</p>
              <p className="muted-copy">Confidence describes the pattern match, not runtime exploitability.</p>
            </section>
          </div>
          <section>
            <h3>Recommended solution</h3>
            <p>{finding.recommendation || "No remediation was stored with this finding."}</p>
          </section>
          <section>
            <h3>Documentation</h3>
            <p>No rule-specific documentation link was attached to this result.</p>
          </section>
        </div>
        <footer className="detail-footer">
          <button className="secondary-button" type="button" onClick={onViewWorkflow}>View in workflow</button>
          <button className="primary-button" type="button" onClick={onClose}>Close</button>
        </footer>
      </section>
    </div>
  );
}

export default function ScanResults({ result, workflow, fallbackName }) {
  const findings = Array.isArray(result.findings) ? result.findings : [];
  const [activeTab, setActiveTab] = useState("overview");
  const [selectedFinding, setSelectedFinding] = useState(null);
  const [detailFinding, setDetailFinding] = useState(null);
  const [search, setSearch] = useState("");
  const [severityFilter, setSeverityFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [confidenceFilter, setConfidenceFilter] = useState("all");
  const [nodeFilter, setNodeFilter] = useState("all");
  const [sortBy, setSortBy] = useState("severity");
  const counts = useMemo(() => severityCounts(findings), [findings]);
  const checkSummary = result.checkSummary;
  const workflowName = result.workflow?.name || fallbackName || "Untitled workflow";
  const metrics = result.workflowMetrics || {};
  const nodeCount = result.workflow?.nodeCount ?? metrics.nodeCount;
  const connectionCount = metrics.connectionCount;
  const categoryOptions = [...new Set(findings.map((finding) => categoryFor(finding.ruleId)))].sort();
  const nodeOptions = [...new Set(findings.map(findingNode))].sort();

  useEffect(() => {
    setActiveTab("overview");
    setSelectedFinding(null);
    setDetailFinding(null);
  }, [result.id, result.startedAt]);

  const filteredFindings = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const filtered = findings.filter((finding) => {
      const category = categoryFor(finding.ruleId);
      const confidence = finding.certainty === "confirmed" ? "confirmed" : "heuristic";
      const searchable = [
        finding.title,
        finding.ruleId,
        finding.severity,
        finding.message,
        finding.location,
        finding.nodeName,
        finding.nodeId,
      ].filter(Boolean).join(" ").toLowerCase();
      return (!needle || searchable.includes(needle)) &&
        (severityFilter === "all" || finding.severity === severityFilter) &&
        (categoryFilter === "all" || category === categoryFilter) &&
        (confidenceFilter === "all" || confidence === confidenceFilter) &&
        (nodeFilter === "all" || findingNode(finding) === nodeFilter);
    });
    const severityOrder = { error: 0, critical: 0, warning: 1, medium: 1, low: 2, info: 3 };
    const confidenceOrder = { confirmed: 0, heuristic: 1 };
    return filtered.sort((left, right) => {
      if (sortBy === "confidence") {
        return (confidenceOrder[left.certainty] ?? 2) - (confidenceOrder[right.certainty] ?? 2);
      }
      if (sortBy === "location") {
        return (left.location || "").localeCompare(right.location || "");
      }
      return (severityOrder[left.severity] ?? 4) - (severityOrder[right.severity] ?? 4);
    });
  }, [findings, search, severityFilter, categoryFilter, confidenceFilter, nodeFilter, sortBy]);

  const recommendations = useMemo(() => {
    const groups = new Map(RECOMMENDATION_GROUPS.map(([key]) => [key, new Map()]));
    findings.forEach((finding) => {
      const category = categoryFor(finding.ruleId);
      const group = groups.get(category);
      if (!group || !finding.recommendation) return;
      const key = `${finding.ruleId}:${finding.recommendation}`;
      const existing = group.get(key) || { ruleId: finding.ruleId, recommendation: finding.recommendation, findings: [] };
      existing.findings.push(finding);
      group.set(key, existing);
    });
    return groups;
  }, [findings]);

  function showFinding(finding) {
    setSelectedFinding(finding);
    setDetailFinding(finding);
  }

  function viewInWorkflow() {
    setActiveTab("workflow");
    setDetailFinding(null);
  }

  return (
    <section className="results-card" id="results" aria-live="polite">
      <header className="results-header">
        <div>
          <div className="eyebrow section-eyebrow">SCAN RESULTS</div>
          <h2>{workflowName}</h2>
          <p>{result.platform || "n8n"} <span>·</span> <span className={`status-pill status-${result.status || "completed"}`}>{result.status || "completed"}</span> <span>·</span> {result.startedAt ? new Date(result.startedAt).toLocaleString() : "Current scan"}</p>
        </div>
        <div className="scan-duration">
          <span>SCAN DURATION</span>
          <strong>{formatDuration(result.durationMs)}</strong>
        </div>
      </header>
      <nav className="result-tabs" aria-label="Scan result views">
        {RESULT_TABS.map(([key, label]) => (
          <button
            className={activeTab === key ? "result-tab active" : "result-tab"}
            type="button"
            key={key}
            aria-current={activeTab === key ? "page" : undefined}
            onClick={() => setActiveTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>

      {activeTab === "overview" && (
        <div className="result-panel">
          <div className="metric-grid">
            {[
              ["Nodes", formatCount(nodeCount)],
              ["Connections", formatCount(connectionCount)],
              ["Total checks", checkSummary ? checkSummary.totalChecks.toLocaleString() : "Not recorded"],
              ["Checks passed", checkSummary ? checkSummary.checksPassed.toLocaleString() : "Not recorded"],
              ["Findings", findings.length.toLocaleString()],
              ["Analysis coverage", result.analysisCoverage?.dataFlow?.status === "partial" ? "Partial" : "Not recorded"],
            ].map(([label, value]) => (
              <article className="metric-card" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </article>
            ))}
          </div>
          <div className="summary-section-heading">
            <div><h3>Finding severity</h3><p>Scanner errors and warnings are mapped to presentation buckets.</p></div>
            <span className="summary-hint">Critical, Low, and Informational are not emitted by this scanner version.</span>
          </div>
          <div className="severity-grid">
            {SEVERITY_CARDS.map(([key, label]) => (
              <article className={`severity-card ${key}`} key={key}>
                <span>{label}</span><strong>{counts[key]}</strong>
              </article>
            ))}
          </div>
          <p className="mapping-note">
            Error findings are shown as High and warning findings as Medium; this does not alter the scanner's stored severity.
          </p>
          <div className="coverage-panel">
            <div className="coverage-heading">
              <div><span className="eyebrow section-eyebrow">ANALYSIS COVERAGE</span><h3>Scope and limitations</h3></div>
              <span className="coverage-badge">{result.analysisCoverage?.dataFlow?.status || "Not recorded"}</span>
            </div>
            <p>{result.analysisCoverage?.dataFlow?.message || "Coverage details were not stored for this scan."}</p>
            {result.analysisCoverage?.dataFlow?.supportedNodeTypes?.length > 0 && (
              <p className="coverage-supported">Modeled node types: {result.analysisCoverage.dataFlow.supportedNodeTypes.join(", ")}</p>
            )}
          </div>
          {findings.length === 0 ? (
            <div className="empty-state success-state">
              <strong>No findings were reported.</strong>
              <p>This is a bounded static analysis, not a guarantee that a workflow is secure or error-free.</p>
            </div>
          ) : (
            <div className="overview-findings">
              <div className="summary-section-heading">
                <div><h3>Priority findings</h3><p>Highest-priority items from this scan.</p></div>
                <button className="text-button" type="button" onClick={() => setActiveTab("findings")}>View all findings →</button>
              </div>
              {findings.slice().sort((a, b) => {
                const rank = { critical: 0, high: 1, medium: 2, low: 3, informational: 4 };
                return rank[priorityFor(a)] - rank[priorityFor(b)];
              }).slice(0, 3).map((finding, index) => (
                <button className="overview-finding" key={findingKey(finding, index)} type="button" onClick={() => showFinding(finding)}>
                  <span className={`severity-chip ${priorityFor(finding)}`}>{finding.severity || "info"}</span>
                  <span><strong>{finding.title || "Workflow finding"}</strong><small>{finding.ruleId} · {finding.location || "Workflow"}</small></span>
                  <span aria-hidden="true">→</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === "findings" && (
        <div className="result-panel">
          <div className="panel-title-row">
            <div><h3>Findings</h3><p>{filteredFindings.length} of {findings.length} findings</p></div>
          </div>
          <div className="finding-filters">
            <label className="search-field">
              <span>Search findings</span>
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Title, rule ID, location…" />
            </label>
            <label><span>Severity</span><select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}>
              <option value="all">All severities</option><option value="error">Error</option><option value="warning">Warning</option>
            </select></label>
            <label><span>Category</span><select value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
              <option value="all">All categories</option>{categoryOptions.map((category) => <option value={category} key={category}>{categoryLabel(category)}</option>)}
            </select></label>
            <label><span>Confidence</span><select value={confidenceFilter} onChange={(event) => setConfidenceFilter(event.target.value)}>
              <option value="all">All confidence levels</option><option value="confirmed">Confirmed pattern</option><option value="heuristic">Heuristic / potential</option>
            </select></label>
            <label><span>Affected node</span><select value={nodeFilter} onChange={(event) => setNodeFilter(event.target.value)}>
              <option value="all">All nodes</option>{nodeOptions.map((node) => <option key={node} value={node}>{node}</option>)}
            </select></label>
            <label><span>Sort by</span><select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
              <option value="severity">Severity</option><option value="confidence">Confidence</option><option value="location">Location</option>
            </select></label>
          </div>
          {filteredFindings.length === 0 ? (
            <p className="empty-state">{findings.length ? "No findings match these filters." : "No findings were reported for this scan."}</p>
          ) : (
            <ul className="findings-list">
              {filteredFindings.map((finding, index) => (
                <li key={findingKey(finding, index)}>
                  <button className="finding-card" type="button" onClick={() => showFinding(finding)}>
                    <span className={`severity-chip ${priorityFor(finding)}`}>{finding.severity || "info"}</span>
                    <span className="finding-card-main">
                      <strong>{finding.title || "Workflow finding"}</strong>
                      <span className="finding-card-description">{finding.message || "No explanation was stored."}</span>
                      <span className="finding-card-meta">
                        <code>{finding.ruleId || "RULE"}</code>
                        <span>{categoryLabel(categoryFor(finding.ruleId))}</span>
                        <span>{finding.certainty === "confirmed" ? "Confirmed pattern" : "Heuristic / potential"}</span>
                        <span>{findingNode(finding)}</span>
                        <code>{finding.location || "Workflow"}</code>
                      </span>
                    </span>
                    <span className="finding-open" aria-hidden="true">↗</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {activeTab === "workflow" && (
        <div className="result-panel">
          <div className="panel-title-row">
            <div><h3>Visual workflow analysis</h3><p>Graph uses only the nodes, positions, and connections in the uploaded JSON.</p></div>
            {selectedFinding && <span className="graph-selected-label">Highlighting {selectedFinding.ruleId}</span>}
          </div>
          <WorkflowGraph workflow={workflow} findings={findings} selectedFinding={selectedFinding} />
          {findings.length > 0 && (
            <div className="graph-finding-list">
              <h4>Findings by node</h4>
              {findings.map((finding, index) => (
                <button
                  type="button"
                  className={selectedFinding === finding ? "graph-finding active" : "graph-finding"}
                  key={findingKey(finding, index)}
                  onClick={() => setSelectedFinding(finding)}
                >
                  <span className={`severity-chip ${priorityFor(finding)}`}>{finding.severity || "info"}</span>
                  <span>{finding.title || finding.ruleId} <small>{findingNode(finding)}</small></span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === "recommendations" && (
        <div className="result-panel">
          <div className="panel-title-row">
            <div><h3>Prioritized recommendations</h3><p>Grouped by rule and recommendation to reduce duplicate advice.</p></div>
          </div>
          <div className="recommendation-groups">
            {RECOMMENDATION_GROUPS.map(([key, label]) => {
              const items = [...(recommendations.get(key)?.values() || [])];
              items.sort((left, right) => {
                const rank = (item) => Math.min(...item.findings.map((finding) =>
                  finding.severity === "error" ? 0 : finding.severity === "warning" ? 1 : 2
                ));
                return rank(left) - rank(right);
              });
              return (
                <section className="recommendation-group" key={key}>
                  <div className="recommendation-group-heading"><h4>{label}</h4><span>{items.length}</span></div>
                  {items.length === 0 ? <p className="empty-group">No recommendations in this category were generated by this scan.</p> : items.map((item) => (
                    <article className="recommendation-card" key={`${key}-${item.ruleId}`}>
                      <div className="recommendation-rule">
                        <code>{item.ruleId}</code>
                        <span className={`severity-chip ${priorityFor(item.findings[0])}`}>{item.findings[0].severity || "info"}</span>
                        <span>{item.findings.length} related {item.findings.length === 1 ? "finding" : "findings"}</span>
                      </div>
                      <p className="recommendation-reason">{item.findings[0].message}</p>
                      <p>{item.recommendation}</p>
                      <div className="recommendation-nodes">
                        <span>Affected nodes</span>
                        {[...new Set(item.findings.map(findingNode))].map((node) => <span className="node-chip" key={node}>{node}</span>)}
                      </div>
                      {item.findings.map((finding, index) => (
                        <button className="text-button recommendation-link" key={findingKey(finding, index)} type="button" onClick={() => showFinding(finding)}>
                          View finding at {finding.location || "Workflow"} →
                        </button>
                      ))}
                    </article>
                  ))}
                </section>
              );
            })}
          </div>
        </div>
      )}

      {activeTab === "technical" && (
        <div className="result-panel">
          <div className="panel-title-row">
            <div><h3>Technical details</h3><p>Advanced scan metadata. Values are omitted where they could expose workflow configuration.</p></div>
          </div>
          <div className="technical-metadata">
            <div><span>Scanner version</span><strong>{result.scannerVersion || "Not recorded"}</strong></div>
            <div><span>Platform</span><strong>{result.platform || "Not recorded"}</strong></div>
            <div><span>Analysis coverage</span><strong>{result.analysisCoverage?.dataFlow?.status || "Not recorded"}</strong></div>
            <div><span>Scan status</span><strong>{result.status || "completed"}</strong></div>
          </div>
          <details className="advanced-details">
            <summary>Checks executed and skipped</summary>
            {!checkSummary ? <p className="empty-state">Per-rule check status was not recorded for this saved scan.</p> : (
              <>
                <p className="technical-counts">{checkSummary.checksExecuted} executed · {checkSummary.checksPassed} passed · {checkSummary.checksSkipped} skipped</p>
                <div className="check-list">
                  {checkSummary.checks.map((check) => (
                    <div className="check-row" key={check.ruleId}><code>{check.ruleId}</code><span className={`check-status check-${check.status}`}>{check.status}</span></div>
                  ))}
                </div>
              </>
            )}
          </details>
          <details className="advanced-details">
            <summary>Node metadata</summary>
            {!workflow ? <p className="empty-state">The original workflow JSON was not retained for this scan.</p> : !Array.isArray(workflow.nodes) ? (
              <p className="empty-state">No node array was present in the scanned workflow.</p>
            ) : (
              <>
                <p className="technical-notice">Only node type, version, and position are shown. Node identifiers, names, and parameter values are omitted.</p>
                <div className="node-metadata-list">
                  {workflow.nodes.map((node, index) => (
                    <div className="node-metadata-row" key={index}>
                      <strong>Node {index + 1}</strong>
                      <code>{typeof node?.type === "string" && /^n8n-nodes-base\.[A-Za-z0-9._-]{1,100}$/.test(node.type) ? node.type : "Other node type"}</code>
                      <span>v{Number.isFinite(node?.typeVersion) ? node.typeVersion : "—"}</span>
                      <span>{Array.isArray(node?.position) && node.position.every(Number.isFinite) ? `(${node.position[0]}, ${node.position[1]})` : "Position unavailable"}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </details>
          <details className="advanced-details">
            <summary>Connection metadata</summary>
            {!workflow ? <p className="empty-state">The original workflow JSON was not retained for this scan.</p> : (() => {
              const connections = getTechnicalConnections(workflow);
              if (connections.error) return <p className="empty-state">{connections.error}</p>;
              if (connections.connections.length === 0) return <p className="empty-state">No unambiguous valid connections were available to display.</p>;
              return (
                <>
                  <p className="technical-notice">Node labels are numeric indices; connection names and configuration values are omitted.</p>
                  <div className="connection-metadata-list">
                    {connections.connections.map((connection, index) => (
                      <div className="connection-metadata-row" key={`${connection.source}-${connection.target}-${index}`}>
                    <span>Node {connection.source}</span>
                    <span className="connection-arrow">→</span>
                    <span>Node {connection.target}</span>
                    <small>output {connection.outputIndex} · {connection.connectionType} · branch {connection.branchIndex} · target slot {connection.targetIndex}</small>
                      </div>
                    ))}
                  </div>
                  {connections.omittedConnections > 0 && <p className="technical-notice">{connections.omittedConnections} malformed or ambiguous connection reference(s) were omitted.</p>}
                    {connections.connectionLimitReached && <p className="technical-notice">Connection metadata display is capped at {MAX_GRAPH_CONNECTIONS} entries.</p>}
                </>
              );
            })()}
          </details>
          <details className="advanced-details">
            <summary>Finding evidence</summary>
            {findings.length === 0 ? <p className="empty-state">No findings were stored.</p> : findings.map((finding, index) => (
              <button className="evidence-row" type="button" key={findingKey(finding, index)} onClick={() => showFinding(finding)}>
                <span><code>{finding.ruleId}</code><strong>{finding.title}</strong></span>
                <span>{finding.location || "Workflow"}</span>
                <span className={`confidence-tag ${finding.certainty}`}>{finding.certainty || "unknown"}</span>
              </button>
            ))}
          </details>
        </div>
      )}

      {detailFinding && (
        <FindingDetail
          finding={detailFinding}
          onClose={() => setDetailFinding(null)}
          onViewWorkflow={viewInWorkflow}
        />
      )}
    </section>
  );
}
