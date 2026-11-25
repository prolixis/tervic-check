"use strict";

const { createFinding } = require("./findings");

function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function nodeValues(node, nodeIndex, limits) {
    const maxDepth = limits.maxDepth === undefined ? 1000 : limits.maxDepth;
    const budget = limits.valueBudget;
    const values = [];
    const stack = [{ value: node.parameters, path: `nodes[${nodeIndex}].parameters`, depth: 0 }];
    const visited = new WeakSet();

    while (stack.length > 0) {
        const current = stack.pop();
        if (current.depth > maxDepth) {
            throw new Error("Node configuration exceeded the configured scan depth.");
        }
        if (budget) {
            budget.remaining -= 1;
            if (budget.remaining < 0) {
                throw new Error("Node configuration exceeded the configured scan value count.");
            }
        }
        if (typeof current.value === "string") {
            values.push({ ...current, value: current.value });
            continue;
        }
        if (!current.value || typeof current.value !== "object" || visited.has(current.value)) continue;
        visited.add(current.value);
        if (Array.isArray(current.value)) {
            if (current.value.length + stack.length > budget.remaining) {
                throw new Error("Node configuration exceeded the configured scan value count.");
            }
            for (let index = current.value.length - 1; index >= 0; index -= 1) {
                stack.push({
                    value: current.value[index],
                    path: `${current.path}[${index}]`,
                    depth: current.depth + 1
                });
            }
        } else {
            for (const key in current.value) {
                if (!Object.prototype.hasOwnProperty.call(current.value, key)) continue;
                if (stack.length + 1 > budget.remaining) {
                    throw new Error("Node configuration exceeded the configured scan value count.");
                }
                stack.push({
                    value: current.value[key],
                    path: `${current.path}.${key}`,
                    depth: current.depth + 1
                });
            }
        }
    }

    return values;
}

function addNodeFinding(findings, ruleId, message, recommendation, node, location, certainty = "heuristic") {
    findings.push(createFinding({
        ruleId,
        severity: "warning",
        certainty,
        message,
        recommendation,
        nodeId: typeof node.id === "string" ? node.id : null,
        nodeName: typeof node.name === "string" ? node.name : null,
        location
    }));
}

function parseHttpUrl(value) {
    if (typeof value !== "string" || !/^https?:\/\//i.test(value)) return null;
    try {
        return new URL(value);
    } catch (_error) {
        return null;
    }
}

function isLoopbackHostname(hostname) {
    const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
    return normalized === "localhost" ||
        normalized.endsWith(".localhost") ||
        normalized === "::1" ||
        /^127(?:\.\d{1,3}){3}$/.test(normalized);
}

function isSensitiveFieldName(field) {
    const normalized = field.replace(/[_-]/g, "").toLowerCase();
    return /^(?:apikey|privatekey|secretkey|accesskey|accesstoken|authtoken|token|secret|clientsecret|password|passwd|authorization|credential|credentials)$/.test(normalized);
}

function createIncomingMap(workflow, maxConnections) {
    const incoming = new Map();
    const names = new Map();
    for (const [index, node] of workflow.nodes.entries()) {
        if (isRecord(node) && typeof node.name === "string") {
            if (names.has(node.name)) return null;
            names.set(node.name, index);
        }
    }

    let count = 0;
    const consume = () => {
        count += 1;
        return count <= maxConnections;
    };
    for (const source in workflow.connections) {
        if (!Object.prototype.hasOwnProperty.call(workflow.connections, source)) continue;
        if (!consume()) return null;
        const outputs = workflow.connections[source];
        if (!names.has(source) || !isRecord(outputs)) return null;
        for (const outputType in outputs) {
            if (!Object.prototype.hasOwnProperty.call(outputs, outputType)) continue;
            if (!consume()) return null;
            const branches = outputs[outputType];
            if (!Array.isArray(branches)) return null;
            for (const branch of branches) {
                if (!consume()) return null;
                if (!Array.isArray(branch)) return null;
                for (const connection of branch) {
                    if (!consume()) return null;
                    if (!isRecord(connection) ||
                        typeof connection.node !== "string" ||
                        typeof connection.type !== "string" ||
                    connection.type.trim() === "" ||
                        !Number.isInteger(connection.index) ||
                        connection.index < 0 ||
                        !names.has(connection.node)) return null;
                    const targetIndex = names.get(connection.node);
                    if (!incoming.has(targetIndex)) incoming.set(targetIndex, new Set());
                    incoming.get(targetIndex).add(source);
                }
            }
        }
    }
    return incoming;
}

function securityFindings(workflow, limits = {}, redactions) {
    if (!isRecord(workflow) || !Array.isArray(workflow.nodes)) return [];
    const maxNodes = limits.maxNodes === undefined ? 10000 : limits.maxNodes;
    if (workflow.nodes.length > maxNodes) return [];
    const findings = [];
    const maxConnections = limits.maxConnections === undefined ? 100000 : limits.maxConnections;
    const incoming = isRecord(workflow.connections)
        ? createIncomingMap(workflow, maxConnections)
        : null;
    const nodesByName = new Map();
    for (const node of workflow.nodes) {
        if (isRecord(node) && typeof node.name === "string" && !nodesByName.has(node.name)) {
            nodesByName.set(node.name, node);
        }
    }
    const boundedLimits = {
        ...limits,
        valueBudget: { remaining: limits.maxValues === undefined ? 100000 : limits.maxValues }
    };

    for (const [index, node] of workflow.nodes.entries()) {
        if (!isRecord(node)) continue;
        const type = typeof node.type === "string" ? node.type.toLowerCase() : "";
        const parameters = isRecord(node.parameters) ? node.parameters : {};
        const values = nodeValues(node, index, boundedLimits);
        const upstream = incoming && incoming.get(index);
        let sensitiveUpstreamFields = null;
        if (type.includes("httprequest") && upstream && upstream.size === 1) {
            const sourceName = [...upstream][0];
            const source = nodesByName.get(sourceName);
            const schema = source && typeof source.type === "string"
                ? nodeSchemaRegistry.get(source.type)
                : null;
            const fields = schema && schema.outputFields(source);
            if (fields) {
                sensitiveUpstreamFields = new Set([...fields].filter(isSensitiveFieldName));
            }
        }
        const connectedToWebhook = upstream && [...upstream].some((sourceName) => {
            const source = nodesByName.get(sourceName);
            return source && typeof source.type === "string" && /webhook/i.test(source.type);
        });

        for (const current of values) {
            const url = parseHttpUrl(current.value);
            const isUrlConfiguration = /\.(?:url|endpoint|uri)$/i.test(current.path);
            if (
                isUrlConfiguration &&
                url &&
                url.protocol === "http:" &&
                !isLoopbackHostname(url.hostname)
            ) {
                addNodeFinding(
                    findings,
                    "SECURITY001",
                    "This node uses an external HTTP endpoint, so request data may travel without transport encryption.",
                    "Use HTTPS where supported, and verify any trusted network boundary before sending sensitive data.",
                    node,
                    current.path
                );
            }

            const destination = parseHttpUrl(parameters.url);
            if (
                destination &&
                !isLoopbackHostname(destination.hostname) &&
                sensitiveUpstreamFields
            ) {
                const expression = /\{\{\s*\$json(?:\.([A-Za-z_$][\w$]*)|\[\s*['"]([^'"]+)['"]\s*\])/g;
                let match;
                while ((match = expression.exec(current.value)) !== null) {
                    if (!sensitiveUpstreamFields.has(match[1] || match[2])) continue;
                    addNodeFinding(
                        findings,
                        "SECURITY007",
                        "An HTTP request expression references a sensitive-named field from a supported upstream Set node and targets an external endpoint; actual payload contents are not known.",
                        "Avoid forwarding credentials or personal data unless required, and use scoped credentials and an approved destination.",
                        node,
                        current.path
                    );
                    break;
                }
            }

            const credentialMatch = current.value.match(/^https?:\/\/([^/@\s:]+):([^/@\s]+)@/i);
            if (credentialMatch) {
                if (redactions instanceof Set) {
                    redactions.add(credentialMatch[1]);
                    redactions.add(credentialMatch[2]);
                    redactions.add(current.value);
                }
                addNodeFinding(
                    findings,
                    "SECURITY002",
                    "A URL contains embedded user information that may be exposed in workflow exports or logs.",
                    "Remove credentials from the URL and use the platform's credential store; rotate any value that was shared.",
                    node,
                    current.path,
                    "confirmed"
                );
            }
        }

        const command = typeof parameters.command === "string" ? parameters.command : "";
        const riskyShellPattern =
            /(?:\$\(|`|\bsudo\b|\brm\s+-rf\b|\bchmod\s+777\b|\b(?:curl|wget)\b[^\r\n]*\|\s*(?:sh|bash)\b)/i;
        if (type.includes("executecommand") && riskyShellPattern.test(command)) {
            addNodeFinding(
                findings,
                "SECURITY003",
                "The shell command contains a command-substitution, privileged/destructive operation, or download-to-shell pattern.",
                "Review whether the operation is necessary, restrict execution permissions, and avoid using untrusted input in shell commands.",
                node,
                `nodes[${index}].parameters.command`
            );

        }
        if (type.includes("executecommand") && command.trim() !== "") {
            const commandUsesInput = /\{\{\s*\$json(?:\.|\[)/i.test(command);
            if (commandUsesInput && connectedToWebhook) {
                addNodeFinding(
                    findings,
                    "SECURITY004",
                    "A command expression references current input and is connected directly from a webhook node; whether the value is validated is unknown.",
                    "Validate and allow-list external input before use, and prefer fixed command arguments over shell interpolation.",
                    node,
                    `nodes[${index}].parameters.command`
                );
            }
        }

        if (type.includes("httprequest") && connectedToWebhook) {
            const headerEntries = parameters.headerParameters &&
                parameters.headerParameters.parameters;
            if (Array.isArray(headerEntries)) {
                for (const [headerIndex, header] of headerEntries.entries()) {
                    if (
                        !isRecord(header) ||
                        typeof header.name !== "string" ||
                        !/^(?:authorization|proxy-authorization|x-api-key|x-auth-token)$/i.test(header.name) ||
                        typeof header.value !== "string" ||
                        !/\{\{\s*\$json(?:\.|\[)/i.test(header.value)
                    ) {
                        continue;
                    }
                    addNodeFinding(
                        findings,
                        "SECURITY008",
                        "A sensitive HTTP header references current input on a path directly connected from a webhook; validation and the actual header value are unknown.",
                        "Validate and allow-list external input before using it as authentication material; prefer managed credentials.",
                        node,
                        `nodes[${index}].parameters.headerParameters.parameters[${headerIndex}].value`
                    );
                }
            }
        }

        if (
            /(?:^|\.)webhook(?:$|[^a-z])/i.test(type) &&
            parameters.authentication === "none"
        ) {
            addNodeFinding(
                findings,
                "SECURITY005",
                "The webhook node explicitly disables node-level authentication; an upstream gateway may still provide protection.",
                "Confirm the endpoint is intended to be public or configure authentication and request validation.",
                node,
                `nodes[${index}].parameters.authentication`
            );
        }

        for (const current of values) {
            if (
                /(?:jscode|functioncode)$/i.test(current.path) &&
                /\b(?:eval\s*\(|new\s+Function\s*\(|require\s*\(\s*['"](?:node:)?child_process['"]|(?:exec|spawn)\s*\()/i.test(current.value)
            ) {
                addNodeFinding(
                    findings,
                    "SECURITY006",
                    "Code configuration contains a dynamic evaluation or process-execution pattern; its runtime effect is not established by this scan.",
                    "Review the code and its inputs, remove unnecessary dynamic execution, and apply least-privilege runtime controls.",
                    node,
                    current.path
                );
            }
        }
    }

    return findings;
}

function reliabilityFindings(workflow, limits = {}) {
    if (
        !isRecord(workflow) ||
        !Array.isArray(workflow.nodes) ||
        !isRecord(workflow.connections)
    ) {
        return [];
    }
    const maxNodes = limits.maxNodes === undefined ? 10000 : limits.maxNodes;
    if (workflow.nodes.length > maxNodes) return [];
    const findings = [];
    const nameCounts = new Map();
    for (const node of workflow.nodes) {
        if (isRecord(node) && typeof node.name === "string") {
            nameCounts.set(node.name, (nameCounts.get(node.name) || 0) + 1);
        }
    }

    for (const [index, node] of workflow.nodes.entries()) {
        if (!isRecord(node)) continue;
        const parameters = isRecord(node.parameters) ? node.parameters : {};
        const location = `nodes[${index}]`;
        const hasUniqueName = typeof node.name === "string" && nameCounts.get(node.name) === 1;
        const errorOutput = hasUniqueName && isRecord(workflow.connections[node.name]) &&
            Array.isArray(workflow.connections[node.name].main) &&
            Array.isArray(workflow.connections[node.name].main[1]) &&
            workflow.connections[node.name].main[1].length > 0;

        if (node.onError === "continueErrorOutput" && hasUniqueName && !errorOutput) {
            addNodeFinding(
                findings,
                "RELIABILITY001",
                "The node is configured to route failures to an error output, but no second main-output branch is connected.",
                "Connect an error-handling path or change the error behavior if failures should stop execution.",
                node,
                `${location}.onError`
            );
        } else if (node.onError === "continueRegularOutput") {
            addNodeFinding(
                findings,
                "RELIABILITY002",
                "The node is configured to continue on its regular output after an error, which may hide failure from downstream steps.",
                "Confirm this fallback is intentional and make the failure state observable where needed.",
                node,
                `${location}.onError`
            );
        }

        const type = typeof node.type === "string" ? node.type.toLowerCase() : "";
        const method = typeof parameters.requestMethod === "string"
            ? parameters.requestMethod.toUpperCase()
            : typeof parameters.method === "string"
                ? parameters.method.toUpperCase()
                : "";
        if (
            type.includes("httprequest") &&
            node.retryOnFail === true &&
            Number.isInteger(node.maxTries) &&
            node.maxTries > 1 &&
            ["POST", "PUT", "PATCH", "DELETE"].includes(method)
        ) {
            addNodeFinding(
                findings,
                "RELIABILITY003",
                `This ${method} request retries up to ${node.maxTries} times; a timeout after the remote operation succeeds could cause repeated side effects.`,
                "Use an idempotency key or verify the remote operation is safe to repeat before enabling retries.",
                node,
                `${location}.retryOnFail`
            );
        }
    }

    return findings;
}

const nodeSchemaRegistry = new Map([
    ["n8n-nodes-base.set", {
        description: "n8n Set node v3+ with explicit assignments and includeOtherFields disabled",
        outputFields(node) {
            const assignments = node.parameters &&
                node.parameters.assignments &&
                node.parameters.assignments.assignments;
            const parameters = isRecord(node.parameters) ? node.parameters : {};
            if (
                !Number.isFinite(node.typeVersion) ||
                node.typeVersion < 3 ||
                parameters.includeOtherFields !== false ||
                !Array.isArray(assignments)
            ) {
                return null;
            }
            const fields = new Set();
            for (const assignment of assignments) {
                if (!isRecord(assignment) || typeof assignment.name !== "string" || assignment.name === "") {
                    return null;
                }
                fields.add(assignment.name);
            }
            return fields;
        }
    }]
]);

function dataFlowFindings(workflow, limits = {}) {
    if (
        !isRecord(workflow) ||
        !Array.isArray(workflow.nodes) ||
        !isRecord(workflow.connections)
    ) {
        return [];
    }
    const maxNodes = limits.maxNodes === undefined ? 10000 : limits.maxNodes;
    if (workflow.nodes.length > maxNodes) return [];
    const maxConnections = limits.maxConnections === undefined ? 100000 : limits.maxConnections;
    const incoming = createIncomingMap(workflow, maxConnections);
    if (!incoming) return [];
    const namesToIndex = new Map();
    for (const [index, node] of workflow.nodes.entries()) {
        if (isRecord(node) && typeof node.name === "string" && !namesToIndex.has(node.name)) {
            namesToIndex.set(node.name, index);
        }
    }

    const findings = [];
    const boundedLimits = {
        ...limits,
        valueBudget: { remaining: limits.maxValues === undefined ? 100000 : limits.maxValues }
    };
    for (const [index, node] of workflow.nodes.entries()) {
        if (!isRecord(node)) continue;
        const upstream = incoming.get(index);
        if (!upstream || upstream.size !== 1) continue;
        const sourceName = [...upstream][0];
        const sourceIndex = namesToIndex.get(sourceName);
        const source = sourceIndex === undefined ? null : workflow.nodes[sourceIndex];
        if (!isRecord(source) || typeof source.type !== "string") continue;
        const schema = nodeSchemaRegistry.get(source.type);
        if (!schema) continue;
        const outputFields = schema.outputFields(source);
        if (!outputFields) continue;
        const assignments = nodeValues(node, index, boundedLimits);
        for (const current of assignments) {
            const expressionPattern = /\{\{\s*\$json(?:\.([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)|\[\s*['"]([^'"]+)['"]\s*\])/g;
            let match;
            while ((match = expressionPattern.exec(current.value)) !== null) {
                const field = (match[1] || match[2]).split(".")[0];
                if (outputFields.has(field)) continue;
                findings.push(createFinding({
                    ruleId: "DATAFLOW001",
                    severity: "warning",
                    certainty: "heuristic",
                    message: "This expression reads a field that is not among the directly connected Set node's explicit output fields.",
                    recommendation: "Confirm the field name and upstream assignments; this check models only simple n8n Set-node v3+ configurations.",
                    nodeId: typeof node.id === "string" ? node.id : null,
                    nodeName: typeof node.name === "string" ? node.name : null,
                    location: current.path
                }));
                break;
            }
        }
    }

    return findings;
}

const securityRules = [{
    id: "SECURITY_CONFIGURATION",
    findingRuleIds: [
        "SECURITY001", "SECURITY002", "SECURITY003", "SECURITY004", "SECURITY005",
        "SECURITY006", "SECURITY007", "SECURITY008"
    ],
    check: securityFindings
}];

const reliabilityRules = [{
    id: "N8N_RELIABILITY",
    findingRuleIds: ["RELIABILITY001", "RELIABILITY002", "RELIABILITY003"],
    check: reliabilityFindings
}];

const dataFlowRules = [{
    id: "N8N_DATA_FLOW",
    findingRuleIds: ["DATAFLOW001"],
    check: dataFlowFindings
}];

module.exports = {
    dataFlowFindings,
    dataFlowRules,
    nodeSchemaRegistry,
    reliabilityFindings,
    reliabilityRules,
    securityFindings,
    securityRules
};
