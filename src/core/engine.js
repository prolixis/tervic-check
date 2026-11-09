
const { scanForSecrets } = require("./secretScanner");
const { validateN8n } = require("../adapters/n8n.adapter");

function runChecks(workflow, platform = "n8n") {
    let findings = [];

    // Run platform-specific validation
    if (platform === "n8n") {
        findings.push(...validateN8n(workflow));
    } else {
        throw new Error(`Unsupported platform: ${platform}`);
    }

    // Run universal security checks
    findings.push(...scanForSecrets(workflow));

    return {
        platform,
        totalFindings: findings.length,
        findings
    };
}

module.exports = { runChecks };
