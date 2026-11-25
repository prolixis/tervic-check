"use strict";

const { createFinding } = require("./findings");

function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function graphFindings(workflow, limits = {}) {
    if (
        !isRecord(workflow) ||
        !Array.isArray(workflow.nodes) ||
        !isRecord(workflow.connections)
    ) {
        return [];
    }

    const maxNodes = limits.maxNodes === undefined ? 10000 : limits.maxNodes;
    const maxConnections = limits.maxConnections === undefined ? 100000 : limits.maxConnections;
    if (workflow.nodes.length === 0 || workflow.nodes.length > maxNodes) return [];

    const nodes = new Map();
    const names = new Set();
    for (const node of workflow.nodes) {
        if (
            !isRecord(node) ||
            typeof node.name !== "string" ||
            node.name.trim() === "" ||
            typeof node.type !== "string" ||
            node.type.trim() === "" ||
            names.has(node.name)
        ) {
            return [];
        }
        names.add(node.name);
        nodes.set(node.name, node);
    }

    const adjacency = new Map([...names].map((name) => [name, new Set()]));
    const reverse = new Map([...names].map((name) => [name, new Set()]));
    let inspected = 0;
    for (const sourceName in workflow.connections) {
        if (!Object.prototype.hasOwnProperty.call(workflow.connections, sourceName)) continue;
        inspected += 1;
        if (inspected > maxConnections) return [];
        const outputs = workflow.connections[sourceName];
        if (!nodes.has(sourceName) || !isRecord(outputs)) return [];
        for (const outputType in outputs) {
            if (!Object.prototype.hasOwnProperty.call(outputs, outputType)) continue;
            const branches = outputs[outputType];
            inspected += 1;
            if (inspected > maxConnections || !Array.isArray(branches)) return [];
            for (const branch of branches) {
                inspected += 1;
                if (inspected > maxConnections || !Array.isArray(branch)) return [];
                for (const connection of branch) {
                    inspected += 1;
                    if (
                        inspected > maxConnections ||
                        !isRecord(connection) ||
                        typeof connection.node !== "string" ||
                        typeof connection.type !== "string" ||
                        connection.type.trim() === "" ||
                        !Number.isInteger(connection.index) ||
                        connection.index < 0 ||
                        !nodes.has(connection.node)
                    ) {
                        return [];
                    }
                    adjacency.get(sourceName).add(connection.node);
                    reverse.get(connection.node).add(sourceName);
                }
            }
        }
    }

    const findings = [];
    const triggerNames = [...nodes.entries()]
        .filter(([, node]) => typeof node.type === "string" &&
            /(trigger|webhook|schedule|cron)/i.test(node.type))
        .map(([name]) => name);

    if (triggerNames.length === 0) {
        findings.push(createFinding({
            ruleId: "GRAPH001",
            severity: "warning",
            certainty: "heuristic",
            message: "No trigger or entry node type was identifiable, so trigger-based reachability could not be established.",
            location: "nodes",
            recommendation: "Confirm that this workflow has an intentional entry point; custom or unsupported trigger types may not be recognized."
        }));
    } else {
        const reachable = new Set();
        const queue = [...triggerNames];
        for (let index = 0; index < queue.length; index += 1) {
            const name = queue[index];
            if (reachable.has(name)) continue;
            reachable.add(name);
            for (const target of adjacency.get(name)) {
                if (!reachable.has(target)) queue.push(target);
            }
        }
        const unreachable = [...nodes.keys()].filter((name) => !reachable.has(name));
        if (unreachable.length > 0) {
            const first = nodes.get(unreachable[0]);
            const firstIndex = workflow.nodes.indexOf(first);
            findings.push(createFinding({
                ruleId: "GRAPH002",
                severity: "warning",
                certainty: "heuristic",
                message: `${unreachable.length} node(s) are not reachable from the identifiable trigger node(s).`,
                location: `nodes[${firstIndex}]`,
                nodeId: typeof first.id === "string" ? first.id : null,
                nodeName: first.name,
                recommendation: "Check whether these nodes are intentionally retained, connected through an unsupported path, or missing a connection."
            }));
        }
    }

    const visited = new Set();
    let componentCount = 0;
    for (const name of nodes.keys()) {
        if (visited.has(name)) continue;
        componentCount += 1;
        const queue = [name];
        visited.add(name);
        for (let index = 0; index < queue.length; index += 1) {
            const current = queue[index];
            for (const neighbors of [adjacency.get(current), reverse.get(current)]) {
                for (const neighbor of neighbors) {
                    if (visited.has(neighbor)) continue;
                    visited.add(neighbor);
                    queue.push(neighbor);
                }
            }
        }
    }
    if (componentCount > 1) {
        findings.push(createFinding({
            ruleId: "GRAPH003",
            severity: "warning",
            certainty: "heuristic",
            message: `The connection graph contains ${componentCount} disconnected components.`,
            location: "connections",
            recommendation: "Review each component; separate triggers or intentionally dormant nodes can make multiple components valid."
        }));
    }

    const inDegree = new Map([...nodes.keys()].map((name) => [name, reverse.get(name).size]));
    const queue = [...nodes.keys()].filter((name) => inDegree.get(name) === 0);
    let visitedCount = 0;
    for (let index = 0; index < queue.length; index += 1) {
        const current = queue[index];
        visitedCount += 1;
        for (const target of adjacency.get(current)) {
            const degree = inDegree.get(target) - 1;
            inDegree.set(target, degree);
            if (degree === 0) queue.push(target);
        }
    }
    if (visitedCount !== nodes.size) {
        const remaining = [...inDegree.entries()]
            .filter(([, degree]) => degree > 0)
            .map(([name]) => name);
        findings.push(createFinding({
            ruleId: "GRAPH004",
            severity: "warning",
            certainty: "heuristic",
            message: `A directed cycle exists in the workflow graph; ${remaining.length} node(s) remain after acyclic traversal.`,
            location: "connections",
            nodeId: typeof nodes.get(remaining[0]).id === "string" ? nodes.get(remaining[0]).id : null,
            nodeName: remaining[0],
            recommendation: "Confirm that the cycle is an intentional loop and that its exit condition and iteration limits are bounded."
        }));
    }

    return findings;
}

const graphRules = [{
    id: "N8N_GRAPH",
    findingRuleIds: ["GRAPH001", "GRAPH002", "GRAPH003", "GRAPH004"],
    check: graphFindings
}];

module.exports = { graphFindings, graphRules };
