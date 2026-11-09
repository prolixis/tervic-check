
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

    // Check 1: Workflow must be a JSON object
    if (
        !workflow ||
        typeof workflow !== "object" ||
        Array.isArray(workflow)
    ) {
        addFinding(
            "N8N001",
            "Workflow must be a JSON object."
        );

        return findings;
    }

    // Check 2: Nodes array
    if (!Array.isArray(workflow.nodes)) {
        addFinding(
            "N8N002",
            "Workflow is missing a valid nodes array."
        );
    }

    // Check 3: Connections object
    if (
        !workflow.connections ||
        typeof workflow.connections !== "object" ||
        Array.isArray(workflow.connections)
    ) {
        addFinding(
            "N8N003",
            "Workflow is missing a valid connections object."
        );
    }

    const nodes = Array.isArray(workflow.nodes)
        ? workflow.nodes
        : [];

    const seenIds = new Set();
    const nodeNames = new Set();

    // Check 4: Duplicate or missing node IDs and names
    for (const node of nodes) {
        if (
            !node ||
            typeof node !== "object" ||
            Array.isArray(node)
        ) {
            addFinding(
                "N8N004",
                "A node is not a valid object."
            );
            continue;
        }

        if (
            typeof node.id !== "string" ||
            !node.id.trim()
        ) {
            addFinding(
                "N8N005",
                "A node has a missing or invalid ID."
            );
        } else if (seenIds.has(node.id)) {
            addFinding(
                "N8N006",
                `Duplicate node ID: ${node.id}`
            );
        } else {
            seenIds.add(node.id);
        }

        if (typeof node.name === "string") {
            if (nodeNames.has(node.name)) {
                addFinding(
                    "N8N007",
                    `Duplicate node name: ${node.name}`
                );
            }

            nodeNames.add(node.name);
        }
    }

    // Check 5: Connections referencing missing nodes
    const connections = workflow.connections;

    if (
        connections &&
        typeof connections === "object" &&
        !Array.isArray(connections)
    ) {
        for (const [sourceName, outputs] of Object.entries(connections)) {
            if (!nodeNames.has(sourceName)) {
                addFinding(
                    "N8N008",
                    `Connection source does not exist: ${sourceName}`
                );
            }

            if (
                !outputs ||
                typeof outputs !== "object" ||
                Array.isArray(outputs)
            ) {
                addFinding(
                    "N8N009",
                    `Invalid connections for node: ${sourceName}`
                );
                continue;
            }

            for (const branches of Object.values(outputs)) {
                if (!Array.isArray(branches)) continue;

                for (const branch of branches) {
                    if (!Array.isArray(branch)) continue;

                    for (const connection of branch) {
                        if (
                            connection &&
                            typeof connection.node === "string" &&
                            !nodeNames.has(connection.node)
                        ) {
                            addFinding(
                                "N8N010",
                                `Connection target does not exist: ${connection.node}`
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
