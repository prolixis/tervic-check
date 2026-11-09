"use strict";

function validateN8n(workflow) {
    const findings = [];

    function addFinding(ruleId, message, location = "workflow") {
        findings.push({
            ruleId,
            severity: "error",
            message,
            location
        });
    }

    if (
        !workflow ||
        typeof workflow !== "object" ||
        Array.isArray(workflow)
    ) {
        addFinding("N8N001", "Workflow must be a JSON object.");
        return findings;
    }

    if (!Array.isArray(workflow.nodes)) {
        addFinding("N8N002", "Workflow is missing a valid nodes array.");
    }

    if (
        !workflow.connections ||
        typeof workflow.connections !== "object" ||
        Array.isArray(workflow.connections)
    ) {
        addFinding("N8N003", "Workflow is missing a valid connections object.");
    }

    const nodes = Array.isArray(workflow.nodes) ? workflow.nodes : [];
    const seenIds = new Set();
    const nodeNames = new Set();

    for (const node of nodes) {
        if (
            !node ||
            typeof node !== "object" ||
            Array.isArray(node)
        ) {
            addFinding("N8N004", "A node is not a valid object.");
            continue;
        }

        if (
            typeof node.id !== "string" ||
            !node.id.trim()
        ) {
            addFinding("N8N005", "A node has a missing or invalid ID.");
        } else if (seenIds.has(node.id)) {
            addFinding("N8N006", `Duplicate node ID: ${node.id}`);
        } else {
            seenIds.add(node.id);
        }

        if (typeof node.name === "string") {
            if (nodeNames.has(node.name)) {
                addFinding("N8N007", `Duplicate node name: ${node.name}`);
            }

            nodeNames.add(node.name);
        } else {
            addFinding("N8N014", "A node has a missing or invalid name.");
        }
    }

    const connections = workflow.connections;

    if (
        connections &&
        typeof connections === "object" &&
        !Array.isArray(connections)
    ) {
        for (const [sourceName, outputs] of Object.entries(connections)) {
            if (!nodeNames.has(sourceName)) {
                addFinding("N8N008", `Connection source does not exist: ${sourceName}`);
            }

            if (
                !outputs ||
                typeof outputs !== "object" ||
                Array.isArray(outputs)
            ) {
                addFinding("N8N009", `Invalid connections for node: ${sourceName}`);
                continue;
            }

            for (const [outputType, branches] of Object.entries(outputs)) {
                if (!Array.isArray(branches)) {
                    addFinding(
                        "N8N011",
                        `Invalid connection branches for node: ${sourceName}`,
                        `connections.${sourceName}.${outputType}`
                    );
                    continue;
                }

                for (const [branchIndex, branch] of branches.entries()) {
                    if (!Array.isArray(branch)) {
                        addFinding(
                            "N8N012",
                            `Invalid connection branch for node: ${sourceName}`,
                            `connections.${sourceName}.${outputType}[${branchIndex}]`
                        );
                        continue;
                    }

                    for (const [connectionIndex, connection] of branch.entries()) {
                        const location =
                            `connections.${sourceName}.${outputType}[${branchIndex}][${connectionIndex}]`;

                        if (
                            !connection ||
                            typeof connection !== "object" ||
                            Array.isArray(connection) ||
                            typeof connection.node !== "string" ||
                            !connection.node.trim()
                        ) {
                            addFinding(
                                "N8N013",
                                `Invalid connection target for node: ${sourceName}`,
                                location
                            );
                        } else if (!nodeNames.has(connection.node)) {
                            addFinding(
                                "N8N010",
                                `Connection target does not exist: ${connection.node}`,
                                location
                            );
                        }
                    }
                }
            }
        }
    }

    return findings;
}

module.exports = { validateN8n };
