"use strict";

const TITLES = {
    N8N001: "Invalid workflow document",
    N8N002: "Invalid nodes collection",
    N8N003: "Invalid connections collection",
    N8N004: "Invalid node",
    N8N005: "Missing node ID",
    N8N006: "Duplicate node ID",
    N8N007: "Duplicate node name",
    N8N008: "Unknown connection source",
    N8N009: "Invalid node connections",
    N8N010: "Unknown connection target",
    N8N011: "Invalid connection branches",
    N8N012: "Invalid connection branch",
    N8N013: "Invalid connection target",
    N8N014: "Missing node name",
    N8N015: "Missing node type",
    N8N016: "Invalid required node properties",
    N8N017: "Invalid workflow metadata",
    GRAPH001: "No identifiable trigger",
    GRAPH002: "Potentially unreachable nodes",
    GRAPH003: "Disconnected workflow components",
    GRAPH004: "Possible workflow cycle",
    SECURITY001: "Insecure HTTP endpoint",
    SECURITY002: "Credentials embedded in URL",
    SECURITY003: "Shell command execution",
    SECURITY004: "External input reaches shell command",
    SECURITY005: "Webhook without node authentication",
    SECURITY006: "Dynamic code execution pattern",
    SECURITY007: "Sensitive field sent to external endpoint",
    SECURITY008: "Webhook input used as authentication header",
    RELIABILITY001: "Missing error output path",
    RELIABILITY002: "Errors continue on regular output",
    RELIABILITY003: "Retries may repeat request side effects",
    DATAFLOW001: "Potentially undefined upstream field",
    SECRET001: "Possible hardcoded credential",
    "TERVIC.ENGINE.001": "Rule could not complete",
    "TERVIC.LIMIT.001": "Scan resource limit reached",
    "TERVIC.LIMIT.002": "Finding limit reached"
};

function createFinding({
    ruleId,
    severity,
    message,
    location = "workflow",
    nodeId = null,
    nodeName = null,
    certainty = severity === "warning" ? "heuristic" : "confirmed",
    recommendation = "Review the workflow and correct the reported issue."
}) {
    return {
        ruleId,
        severity,
        title: TITLES[ruleId] || "Workflow validation finding",
        message,
        nodeId,
        nodeName,
        recommendation,
        location,
        certainty
    };
}

module.exports = { createFinding };
