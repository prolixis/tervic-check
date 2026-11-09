#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

const { runChecks } = require("../src/core/engine");

function main() {
    const filePath = process.argv[2];

    if (!filePath) {
        console.error(
            "Usage: tervic-check <workflow.json>"
        );
        return 2;
    }

    const absolutePath = path.resolve(filePath);

    // Check file existence
    if (!fs.existsSync(absolutePath)) {
        console.error(`File not found: ${filePath}`);
        return 2;
    }

    let workflow;

    // Parse JSON
    try {
        const content = fs.readFileSync(absolutePath, "utf8");
        workflow = JSON.parse(content);
    } catch (error) {
        console.error("FAIL: Invalid JSON file.");
        console.error(error.message);
        return 1;
    }

    // Run validation engine
    try {
        const result = runChecks(workflow, "n8n");

        console.log("\n=== Tervic Check V0.2 ===");
        console.log(`Platform: ${result.platform}`);
        console.log(`File: ${path.basename(absolutePath)}`);

        if (Array.isArray(workflow.nodes)) {
            console.log(`Nodes inspected: ${workflow.nodes.length}`);
        }

        if (result.totalFindings === 0) {
            console.log("\nPASS: No issues detected.");
            console.log("Note: This is not a guarantee of security.");
            return 0;
        }

        console.log(
            `\nFound ${result.totalFindings} issue(s):\n`
        );

        result.findings.forEach((finding, index) => {
            console.log(
                `${index + 1}. [${finding.severity.toUpperCase()}] ${finding.message}`
            );
            console.log(`   Rule: ${finding.ruleId}`);
            console.log(`   Location: ${finding.location}`);
        });

        console.log("\nCHECK COMPLETE: Review the findings above.");

        return 1;

    } catch (error) {
        console.error(`Validation error: ${error.message}`);
        return 2;
    }
}

process.exitCode = main();
