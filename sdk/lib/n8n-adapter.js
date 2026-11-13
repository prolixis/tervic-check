"use strict";

const { createFinding } = require("./findings");

const DEFAULT_MAX_NODES = 10000;
const DEFAULT_MAX_CONNECTIONS = 100000;

function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function addFinding(findings, ruleId, message, location, node = null) {
    const recommendations = {
        N8N001: "Provide a workflow as a JSON object.",
        N8N002: "Provide nodes as an array.",
        N8N003: "Provide connections as an object.",
        N8N004: "Replace the invalid node entry with a node object.",
        N8N005: "Assign each node a non-empty string ID.",
        N8N006: "Give every node a unique ID.",
        N8N007: "Give every node a unique name so connection references are unambiguous.",
        N8N008: "Connect from a node name that exists in the workflow.",
        N8N009: "Represent each node's connections as an object.",
        N8N010: "Connect to a node name that exists in the workflow.",
        N8N011: "Represent each output's branches as an array.",
        N8N012: "Represent each connection branch as an array.",
        N8N013: "Provide a connection object with a target name, non-empty type, and non-negative integer output index.",
        N8N014: "Assign each node a non-empty string name.",
        N8N015: "Assign each node a non-empty n8n node type.",
        N8N016: "Provide a positive typeVersion, a two-number position, and an object-valued parameters field.",
        N8N017: "Use the expected JSON types for workflow metadata fields."
    };

    findings.push(createFinding({
        ruleId,
        severity: "error",
        message,
        location,
        nodeId: node && typeof node.id === "string" ? node.id : null,
        nodeName: node && typeof node.name === "string" ? node.name : null,
        recommendation: recommendations[ruleId]
    }));
}

function validateWorkflowShape(workflow) {
    const findings = [];
    if (!isRecord(workflow)) {
        addFinding(findings, "N8N001", "Workflow must be a JSON object.", "workflow");
        return findings;
    }

    if (!Array.isArray(workflow.nodes)) {
        addFinding(findings, "N8N002", "Workflow must contain a nodes array.", "nodes");
    }
    if (!isRecord(workflow.connections)) {
        addFinding(findings, "N8N003", "Workflow must contain a connections object.", "connections");
    }
    if (
        (Object.prototype.hasOwnProperty.call(workflow, "name") &&
            (typeof workflow.name !== "string" || workflow.name.trim() === "")) ||
        (Object.prototype.hasOwnProperty.call(workflow, "id") &&
            typeof workflow.id !== "string") ||
        (Object.prototype.hasOwnProperty.call(workflow, "versionId") &&
            typeof workflow.versionId !== "string") ||
        (Object.prototype.hasOwnProperty.call(workflow, "active") &&
            typeof workflow.active !== "boolean") ||
        (Object.prototype.hasOwnProperty.call(workflow, "settings") &&
            workflow.settings !== null && !isRecord(workflow.settings)) ||
        (Object.prototype.hasOwnProperty.call(workflow, "meta") &&
            workflow.meta !== null && !isRecord(workflow.meta)) ||
        (Object.prototype.hasOwnProperty.call(workflow, "staticData") &&
            workflow.staticData !== null && !isRecord(workflow.staticData)) ||
        (Object.prototype.hasOwnProperty.call(workflow, "pinData") &&
            workflow.pinData !== null && !isRecord(workflow.pinData)) ||
        (Object.prototype.hasOwnProperty.call(workflow, "tags") &&
            workflow.tags !== null && !Array.isArray(workflow.tags))
    ) {
        addFinding(findings, "N8N017", "Workflow metadata contains a field with an invalid JSON type or value.", "metadata");
    }
    return findings;
}

function validateNodes(workflow, options = {}) {
    const findings = [];
    if (!isRecord(workflow) || !Array.isArray(workflow.nodes)) {
        return findings;
    }

    const maxNodes = options.maxNodes === undefined ? DEFAULT_MAX_NODES : options.maxNodes;
    if (workflow.nodes.length > maxNodes) {
        findings.push(createFinding({
            ruleId: "TERVIC.LIMIT.001",
            severity: "error",
            message: `Workflow contains more than the configured maximum of ${maxNodes} nodes; node checks were not completed.`,
            location: "nodes",
            recommendation: "Reduce the workflow size or increase limits.maxNodes for a trusted input."
        }));
        return findings;
    }

    const seenIds = new Set();
    const seenNames = new Set();

    for (const [index, node] of workflow.nodes.entries()) {
        const location = `nodes[${index}]`;
        if (!isRecord(node)) {
            addFinding(findings, "N8N004", "A node entry must be an object.", location);
            continue;
        }

        if (typeof node.id !== "string" || node.id.trim() === "") {
            addFinding(findings, "N8N005", "A node must have a non-empty string ID.", `${location}.id`, node);
        } else if (seenIds.has(node.id)) {
            addFinding(findings, "N8N006", "Node IDs must be unique.", `${location}.id`, node);
        } else {
            seenIds.add(node.id);
        }

        if (typeof node.name !== "string" || node.name.trim() === "") {
            addFinding(findings, "N8N014", "A node must have a non-empty string name.", `${location}.name`, node);
        } else {
            if (seenNames.has(node.name)) {
                addFinding(findings, "N8N007", "Node names must be unique.", `${location}.name`, node);
            }
            seenNames.add(node.name);
        }

        if (typeof node.type !== "string" || node.type.trim() === "") {
            addFinding(findings, "N8N015", "A node must have a non-empty string type.", `${location}.type`, node);
        } else if (
            !Number.isFinite(node.typeVersion) ||
            node.typeVersion <= 0 ||
            !Array.isArray(node.position) ||
            node.position.length !== 2 ||
            !Number.isFinite(node.position[0]) ||
            !Number.isFinite(node.position[1]) ||
            !isRecord(node.parameters)
        ) {
            addFinding(
                findings,
                "N8N016",
                "A typed node must have a positive typeVersion, a two-number position, and an object-valued parameters field.",
                location,
                node
            );
        }
    }

    return findings;
}

function validateConnections(workflow, options = {}) {
    const findings = [];
    if (!isRecord(workflow) || !isRecord(workflow.connections)) {
        return findings;
    }

    const nodes = Array.isArray(workflow.nodes) ? workflow.nodes : [];
    const maxNodes = options.maxNodes === undefined ? DEFAULT_MAX_NODES : options.maxNodes;
    if (nodes.length > maxNodes) {
        return findings;
    }
    const names = new Map();
    for (const node of nodes) {
        if (isRecord(node) && typeof node.name === "string" && node.name.trim() !== "") {
            if (!names.has(node.name)) names.set(node.name, node);
        }
    }

    const maxConnections = options.maxConnections === undefined
        ? DEFAULT_MAX_CONNECTIONS
        : options.maxConnections;
    let inspectedConnections = 0;
    let limitReported = false;
    const reportLimit = (location) => {
        if (!limitReported) {
            findings.push(createFinding({
                ruleId: "TERVIC.LIMIT.001",
                severity: "error",
                message: `Connection traversal exceeded the configured maximum of ${maxConnections} entries; checks were not completed.`,
                location,
                recommendation: "Reduce the workflow size or increase limits.maxConnections for a trusted input."
            }));
            limitReported = true;
        }
    };

    const consumeEntry = (location) => {
        inspectedConnections += 1;
        if (inspectedConnections <= maxConnections) return true;
        reportLimit(location);
        return false;
    };

    let sourceIndex = 0;
    for (const sourceName in workflow.connections) {
        if (!Object.prototype.hasOwnProperty.call(workflow.connections, sourceName)) continue;
        const sourceLocation = `connections.${sourceIndex}`;
        if (!consumeEntry(sourceLocation)) return findings;
        const source = names.get(sourceName) || null;
        const outputs = workflow.connections[sourceName];
        if (!source) {
            addFinding(findings, "N8N008", "A connection source does not match a workflow node.", sourceLocation);
        }

        if (!isRecord(outputs)) {
            addFinding(findings, "N8N009", "A node's connections must be an object.", sourceLocation, source);
            sourceIndex += 1;
            continue;
        }

        let outputIndex = 0;
        for (const outputType in outputs) {
            if (!Object.prototype.hasOwnProperty.call(outputs, outputType)) continue;
            const outputLocation = `${sourceLocation}.outputs[${outputIndex}]`;
            if (!consumeEntry(outputLocation)) return findings;
            const branches = outputs[outputType];
            if (!Array.isArray(branches)) {
                addFinding(findings, "N8N011", "Connection outputs must contain an array of branches.", outputLocation, source);
                outputIndex += 1;
                continue;
            }

            for (let branchIndex = 0; branchIndex < branches.length; branchIndex += 1) {
                const branchLocation = `${outputLocation}.branches[${branchIndex}]`;
                if (!consumeEntry(branchLocation)) return findings;
                const branch = branches[branchIndex];
                if (!Array.isArray(branch)) {
                    addFinding(findings, "N8N012", "A connection branch must be an array.", branchLocation, source);
                    continue;
                }

                for (let connectionIndex = 0; connectionIndex < branch.length; connectionIndex += 1) {
                    const location = `${branchLocation}[${connectionIndex}]`;
                    if (!consumeEntry(location)) return findings;
                    const connection = branch[connectionIndex];
                    if (
                        !isRecord(connection) ||
                        typeof connection.node !== "string" ||
                        connection.node.trim() === "" ||
                        typeof connection.type !== "string" ||
                        connection.type.trim() === "" ||
                        !Number.isInteger(connection.index) ||
                        connection.index < 0
                    ) {
                        addFinding(
                            findings,
                            "N8N013",
                            "A connection must specify a target node name, a non-empty type, and a non-negative integer output index.",
                            location,
                            source
                        );
                    } else if (!names.has(connection.node)) {
                        addFinding(findings, "N8N010", "A connection target does not match a workflow node.", location, source);
                    }
                }
            }
            outputIndex += 1;
        }
        sourceIndex += 1;
    }

    return findings;
}

const n8nRules = [
    {
        id: "N8N_STRUCTURE",
        findingRuleIds: ["N8N001", "N8N002", "N8N003", "N8N017"],
        check: validateWorkflowShape
    },
    {
        id: "N8N_NODES",
        findingRuleIds: ["N8N004", "N8N005", "N8N006", "N8N007", "N8N014", "N8N015", "N8N016", "TERVIC.LIMIT.001"],
        check: validateNodes
    },
    {
        id: "N8N_CONNECTIONS",
        findingRuleIds: ["N8N008", "N8N009", "N8N010", "N8N011", "N8N012", "N8N013", "TERVIC.LIMIT.001"],
        check: validateConnections
    }
];

function validateN8n(workflow, options = {}) {
    return n8nRules.flatMap((rule) => rule.check(workflow, options));
}

module.exports = {
    n8nRules,
    validateConnections,
    validateN8n,
    validateNodes,
    validateWorkflowShape
};
