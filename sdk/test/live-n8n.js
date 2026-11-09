"use strict";

const assert = require("node:assert/strict");
const { createN8nClient } = require("..");

async function main() {
    const baseUrl = process.env.N8N_BASE_URL;
    const apiKey = process.env.N8N_API_KEY;

    if (!baseUrl || !apiKey) {
        throw new Error(
            "Set N8N_BASE_URL and N8N_API_KEY to run the read-only live n8n smoke test."
        );
    }

    const client = createN8nClient({ baseUrl, apiKey });
    const page = await client.listWorkflowsPage({ limit: 1 });
    assert(Array.isArray(page.data), "Workflow page data must be an array.");

    if (page.data.length > 0) {
        const workflow = await client.getWorkflow(page.data[0].id);
        assert.equal(typeof workflow.name, "string");
        assert(Array.isArray(workflow.nodes));
        assert(workflow.connections && typeof workflow.connections === "object");
    }

    console.log("PASS live n8n read-only API smoke test");
}

main().catch((error) => {
    console.error(`${error.code || "ERR"}: ${error.message}`);
    process.exitCode = 1;
});
