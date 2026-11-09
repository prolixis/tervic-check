"use strict";

const {
    exportWorkflow,
    importWorkflow,
    scanWorkflow
} = require("..");
const path = require("node:path");

async function main() {
    const inputPath = process.argv[2] ||
        path.resolve(__dirname, "../../examples/sample_workflow.json");
    const workflow = await importWorkflow(inputPath);
    const result = scanWorkflow(workflow);

    console.log(`Platform: ${result.platform}`);
    console.log(`Findings: ${result.totalFindings}`);

    for (const finding of result.findings) {
        console.log(`[${finding.severity.toUpperCase()}] ${finding.ruleId}: ${finding.message}`);
        console.log(`Location: ${finding.location}`);
    }

    const outputPath = await exportWorkflow(workflow, "./workflow-copy.json");
    console.log(`Exported workflow to: ${outputPath}`);
}

main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
});
