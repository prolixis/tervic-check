"use strict";

const { scanForSecrets } = require("./secret-scanner");
const { validateN8n } = require("./n8n-adapter");
const { TervicSdkError } = require("./errors");

function runChecks(workflow, platform = "n8n") {
    if (typeof platform !== "string" || platform.trim() === "") {
        throw new TervicSdkError(
            "platform must be a non-empty string.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    if (platform === "n8n") {
        const findings = validateN8n(workflow);
        findings.push(...scanForSecrets(workflow));

        return {
            platform,
            totalFindings: findings.length,
            findings
        };
    } else {
        throw new TervicSdkError(
            `Unsupported platform: ${platform}`,
            "ERR_TERVIC_UNSUPPORTED_PLATFORM"
        );
    }
}

module.exports = { runChecks };
