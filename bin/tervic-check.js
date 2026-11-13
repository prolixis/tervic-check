#!/usr/bin/env node

const path = require("node:path");

const { importWorkflow, scanWorkflow } = require("../sdk");

const packageInfo = require("../package.json");
const usage = `Usage: tervic-check [--help] [--version] [--] <workflow.json>

Scan a local n8n workflow JSON file.

Options:
  -h, --help      Show this help
  -v, --version   Show the CLI version
  --              End option parsing (for paths beginning with "-")`;

async function main() {
    const args = process.argv.slice(2);
    if (args.length === 1 && (args[0] === "--help" || args[0] === "-h")) {
        console.log(usage);
        return 0;
    }
    if (args.length === 1 && (args[0] === "--version" || args[0] === "-v")) {
        console.log(packageInfo.version);
        return 0;
    }

    let fileArgs = args;
    if (fileArgs[0] === "--") {
        fileArgs = fileArgs.slice(1);
    } else if (fileArgs.some((argument) => argument.startsWith("-"))) {
        console.error(`Unknown option: ${fileArgs.find((argument) => argument.startsWith("-"))}`);
        console.error(usage);
        return 2;
    }

    if (fileArgs.length !== 1 || fileArgs[0] === "") {
        console.error(fileArgs.length === 0
            ? usage
            : "Exactly one workflow file must be provided.");
        if (fileArgs.length !== 0) console.error(usage);
        return 2;
    }

    const filePath = fileArgs[0];
    const absolutePath = path.resolve(filePath);

    let workflow;
    try {
        workflow = await importWorkflow(absolutePath);
    } catch (error) {
        if (error.code === "ERR_TERVIC_INVALID_JSON") {
            console.error("FAIL: Invalid JSON file.");
            console.error(error.message);
            return 1;
        }
        if (error.code === "ERR_TERVIC_READ_FAILED" && error.cause && error.cause.code === "ENOENT") {
            console.error(`File not found: ${filePath}`);
            return 2;
        }
        console.error(`Unable to read workflow: ${error.message}`);
        return 2;
    }

    try {
        const result = scanWorkflow(workflow, { platform: "n8n" });

        console.log(`\n=== Tervic Flowcheck V${packageInfo.version} ===`);
        console.log(`Platform: ${result.platform}`);
        console.log(`File: ${path.basename(absolutePath)}`);

        if (Array.isArray(workflow.nodes)) {
            console.log(`Nodes in file: ${workflow.nodes.length}`);
        }
        if (result.analysisCoverage && result.analysisCoverage.dataFlow) {
            console.log(`Data-flow coverage: ${result.analysisCoverage.dataFlow.message}`);
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
                `${index + 1}. [${finding.severity.toUpperCase()}] ${finding.title}: ${finding.message}`
            );
            console.log(`   Rule: ${finding.ruleId}`);
            console.log(`   Location: ${finding.location}`);
            if (finding.nodeId !== null) console.log(`   Node ID: ${finding.nodeId}`);
            if (finding.nodeName !== null) console.log(`   Node: ${finding.nodeName}`);
            console.log(`   Certainty: ${finding.certainty}`);
            console.log(`   Recommendation: ${finding.recommendation}`);
        });

        console.log("\nCHECK COMPLETE: Review the findings above.");

        return 1;

    } catch (error) {
        console.error(`Validation error: ${error.message}`);
        return 2;
    }
}

main().then((exitCode) => {
    process.exitCode = exitCode;
}).catch((error) => {
    console.error(`Validation error: ${error.message}`);
    process.exitCode = 2;
});
