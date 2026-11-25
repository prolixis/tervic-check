"use strict";

const { scanForSecrets } = require("./secret-scanner");
const { n8nRules } = require("./n8n-adapter");
const { graphRules } = require("./graph-analysis");
const {
    dataFlowRules,
    nodeSchemaRegistry,
    reliabilityRules,
    securityRules
} = require("./n8n-risk-analysis");
const { createFinding } = require("./findings");
const { TervicSdkError } = require("./errors");
const sdkPackage = require("../package.json");

const DEFAULT_LIMITS = {
    maxNodes: 10000,
    maxConnections: 100000,
    maxDepth: 1000,
    maxValues: 100000,
    maxFindings: 1000
};

const secretRule = {
    id: "SECURITY_SECRETS",
    findingRuleIds: ["SECRET001"],
    check: (workflow, limits, redactions) => scanForSecrets(workflow, "workflow", {
        maxDepth: limits.maxDepth,
        maxValues: limits.maxValues,
        redactions
    })
};

const rules = [
    ...n8nRules,
    ...graphRules,
    ...dataFlowRules,
    ...reliabilityRules,
    ...securityRules,
    secretRule
];
const ruleSelectors = new Set([
    "all",
    ...rules.map((rule) => rule.id),
    ...rules.flatMap((rule) => rule.findingRuleIds),
    "TERVIC.LIMIT.001",
    "TERVIC.LIMIT.002"
]);
const checkRuleIds = [...new Set(rules.flatMap((rule) => rule.findingRuleIds))]
    .filter((ruleId) => !ruleId.startsWith("TERVIC."));

function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function getWorkflowMetrics(workflow, maxConnections) {
    const nodeCount = isRecord(workflow) && Array.isArray(workflow.nodes)
        ? workflow.nodes.length
        : null;
    if (!isRecord(workflow) || !isRecord(workflow.connections)) {
        return { nodeCount, connectionCount: null };
    }

    let connectionCount = 0;
    let visited = 0;
    const withinLimit = () => {
        visited += 1;
        return visited <= maxConnections;
    };
    for (const sourceName in workflow.connections) {
        if (!Object.prototype.hasOwnProperty.call(workflow.connections, sourceName)) continue;
        if (!withinLimit()) return { nodeCount, connectionCount: null };
        const outputs = workflow.connections[sourceName];
        if (!isRecord(outputs)) return { nodeCount, connectionCount: null };
        for (const outputName in outputs) {
            if (!Object.prototype.hasOwnProperty.call(outputs, outputName)) continue;
            if (!withinLimit()) return { nodeCount, connectionCount: null };
            const branches = outputs[outputName];
            if (!Array.isArray(branches)) return { nodeCount, connectionCount: null };
            for (const branch of branches) {
                if (!withinLimit() || !Array.isArray(branch)) {
                    return { nodeCount, connectionCount: null };
                }
                for (let index = 0; index < branch.length; index += 1) {
                    if (!withinLimit()) return { nodeCount, connectionCount: null };
                    connectionCount += 1;
                }
            }
        }
    }
    return { nodeCount, connectionCount };
}

function hasSupportedDataFlowNode(workflow) {
    if (!isRecord(workflow) || !Array.isArray(workflow.nodes)) return false;
    return workflow.nodes.some((node) => {
        if (!isRecord(node) || typeof node.type !== "string") return false;
        const schema = nodeSchemaRegistry.get(node.type);
        return Boolean(schema && schema.outputFields(node) !== null);
    });
}

function assertRuleSelection(value, name) {
    if (value === undefined) return;
    if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) {
        throw new TervicSdkError(
            `${name} must be an array of rule IDs.`,
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    for (const selector of value) {
        if (!ruleSelectors.has(selector)) {
            throw new TervicSdkError(
                `Unknown rule ID in ${name}: ${selector}`,
                "ERR_TERVIC_INVALID_ARGUMENT"
            );
        }
    }
}

function getLimits(options) {
    const supplied = options.limits === undefined ? {} : options.limits;
    if (!supplied || typeof supplied !== "object" || Array.isArray(supplied)) {
        throw new TervicSdkError(
            "options.limits must be an object.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    const limits = { ...DEFAULT_LIMITS };
    for (const [name, value] of Object.entries(supplied)) {
        if (!Object.prototype.hasOwnProperty.call(DEFAULT_LIMITS, name)) {
            throw new TervicSdkError(
                `Unknown scan limit: ${name}`,
                "ERR_TERVIC_INVALID_ARGUMENT"
            );
        }
        if (!Number.isInteger(value) || value < 1) {
            throw new TervicSdkError(
                `options.limits.${name} must be a positive integer.`,
                "ERR_TERVIC_INVALID_ARGUMENT"
            );
        }
        limits[name] = value;
    }
    return limits;
}

function matchesRuleSelector(selectors, rule) {
    return selectors.includes("all") ||
        selectors.includes(rule.id) ||
        rule.findingRuleIds.some((ruleId) => selectors.includes(ruleId));
}

function redactFindings(findings, redactions) {
    const secretValues = [...redactions].filter(Boolean).sort((a, b) => b.length - a.length);
    if (secretValues.length === 0) return findings;

    return findings.map((finding) => {
        const redacted = { ...finding };
        for (const key of Object.keys(redacted)) {
            if (typeof redacted[key] !== "string") continue;
            for (const secret of secretValues) {
                if (secret.length >= 4) {
                    redacted[key] = redacted[key].split(secret).join("[REDACTED]");
                } else if (redacted[key] === secret) {
                    redacted[key] = "[REDACTED]";
                } else if (
                    ["nodeId", "nodeName", "location"].includes(key) &&
                    redacted[key].includes(secret)
                ) {
                    redacted[key] = "[REDACTED]";
                }
            }
        }
        return redacted;
    });
}

function runChecks(workflow, platform = "n8n", options = {}) {
    if (typeof platform !== "string" || platform.trim() === "") {
        throw new TervicSdkError(
            "platform must be a non-empty string.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }
    if (!options || typeof options !== "object" || Array.isArray(options)) {
        throw new TervicSdkError(
            "options must be an object.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }
    if (platform !== "n8n") {
        throw new TervicSdkError(
            `Unsupported platform: ${platform}`,
            "ERR_TERVIC_UNSUPPORTED_PLATFORM"
        );
    }

    assertRuleSelection(options.enabledRules, "options.enabledRules");
    assertRuleSelection(options.disabledRules, "options.disabledRules");
    const limits = getLimits(options);
    const enabledRules = options.enabledRules;
    const disabledRules = options.disabledRules || [];
    const redactions = new Set();
    const findings = [];
    const checkStatuses = new Map(checkRuleIds.map((ruleId) => [ruleId, "skipped"]));

    let secretFindings = [];
    let secretRuleError = false;
    try {
        secretFindings = secretRule.check(workflow, limits, redactions);
    } catch (_error) {
        secretRuleError = true;
    }

    for (const rule of rules) {
        if (rule === secretRule) continue;
        if (enabledRules && !matchesRuleSelector(enabledRules, rule)) continue;
        if (disabledRules.includes("all") || disabledRules.includes(rule.id)) continue;

        const selectedCheckIds = rule.findingRuleIds.filter((ruleId) =>
            checkStatuses.has(ruleId) &&
            (!enabledRules || enabledRules.includes("all") ||
                enabledRules.includes(rule.id) || enabledRules.includes(ruleId)) &&
            !disabledRules.includes(ruleId)
        );
        try {
            const ruleFindings = rule.check(workflow, limits, redactions);
            const limited = ruleFindings.some((finding) => finding.ruleId === "TERVIC.LIMIT.001");
            const failed = ruleFindings.some((finding) => finding.ruleId === "TERVIC.ENGINE.001");
            for (const ruleId of selectedCheckIds) {
                if (ruleId === "DATAFLOW001" && !hasSupportedDataFlowNode(workflow)) {
                    checkStatuses.set(ruleId, "skipped");
                    continue;
                }
                checkStatuses.set(
                    ruleId,
                    limited ? "skipped" : failed ? "failed" :
                        ruleFindings.some((finding) => finding.ruleId === ruleId)
                            ? "finding"
                            : "passed"
                );
            }
            for (const finding of ruleFindings) {
                if (finding.ruleId === "TERVIC.LIMIT.002" &&
                    !disabledRules.includes("TERVIC.LIMIT.002")) {
                    findings.push(finding);
                    continue;
                }
                if (enabledRules && !enabledRules.includes("all") &&
                    !enabledRules.includes(rule.id) &&
                    !enabledRules.includes(finding.ruleId)) {
                    continue;
                }
                if (disabledRules.includes(finding.ruleId)) continue;
                findings.push(finding);
            }
        } catch (_error) {
            for (const ruleId of selectedCheckIds) checkStatuses.set(ruleId, "failed");
            findings.push(createFinding({
                ruleId: "TERVIC.ENGINE.001",
                severity: "error",
                certainty: "confirmed",
                message: `The ${rule.id} validation rule could not complete.`,
                location: `rules.${rule.id}`,
                recommendation: "Review the workflow input and scan limits; other rules continued independently."
            }));
        }
    }

    const secretsEnabled = (!enabledRules || matchesRuleSelector(enabledRules, secretRule)) &&
        !matchesRuleSelector(disabledRules, secretRule);
    if (secretsEnabled) {
        if (secretRuleError) {
            checkStatuses.set("SECRET001", "failed");
            findings.push(createFinding({
                ruleId: "TERVIC.ENGINE.001",
                severity: "error",
                certainty: "confirmed",
                message: "The SECURITY_SECRETS validation rule could not complete.",
                location: "rules.SECURITY_SECRETS",
                recommendation: "Review the workflow input and scan limits; other rules continued independently."
            }));
        } else {
            checkStatuses.set(
                "SECRET001",
                secretFindings.some((finding) => finding.ruleId === "SECRET001")
                    ? "finding"
                    : "passed"
            );
            findings.push(...secretFindings);
        }
    }

    const normalizeLocation = (location) =>
        location.startsWith("workflow.") ? location.slice("workflow.".length) : location;
    const embeddedCredentialLocations = new Set(findings
        .filter((finding) => finding.ruleId === "SECURITY002")
        .map((finding) => normalizeLocation(finding.location)));
    const deduplicatedFindings = findings.filter((finding) =>
        finding.ruleId !== "SECRET001" ||
        !embeddedCredentialLocations.has(normalizeLocation(finding.location))
    );
    let safeFindings = redactFindings(deduplicatedFindings, redactions);
    if (secretRuleError) {
        safeFindings = safeFindings.map((finding) => ({
            ...finding,
            nodeId: null,
            nodeName: null
        }));
    }
    if (safeFindings.length > limits.maxFindings) {
        safeFindings = safeFindings.slice(0, Math.max(0, limits.maxFindings - 1));
        safeFindings.push(createFinding({
            ruleId: "TERVIC.LIMIT.002",
            severity: "error",
            certainty: "confirmed",
            message: `Finding output was truncated at the configured maximum of ${limits.maxFindings}.`,
            location: "findings",
            recommendation: "Review the workflow in smaller sections or increase limits.maxFindings."
        }));
    }

    const checks = [...checkStatuses].map(([ruleId, status]) => ({ ruleId, status }));
    const checksPassed = checks.filter((check) => check.status === "passed").length;
    const checksSkipped = checks.filter((check) => check.status === "skipped").length;
    const checksExecuted = checks.length - checksSkipped;

    return {
        platform,
        scannerVersion: sdkPackage.version,
        totalFindings: safeFindings.length,
        findings: safeFindings,
        workflowMetrics: getWorkflowMetrics(workflow, limits.maxConnections),
        checkSummary: {
            totalChecks: checks.length,
            checksExecuted,
            checksPassed,
            checksSkipped,
            checks
        },
        analysisCoverage: {
            dataFlow: {
                status: "partial",
                supportedNodeTypes: [...nodeSchemaRegistry.keys()],
                message: "Field-reference checks only model direct connections from explicit n8n-nodes-base.set v3+ assignments with includeOtherFields disabled; other node schemas are not inferred."
            }
        }
    };
}

module.exports = { DEFAULT_LIMITS, runChecks };
