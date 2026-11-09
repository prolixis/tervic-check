"use strict";

const { runChecks } = require("./lib/engine");
const { TervicSdkError } = require("./lib/errors");
const { createN8nClient } = require("./lib/n8n-client");
const { scanForSecrets } = require("./lib/secret-scanner");
const { validateN8n } = require("./lib/n8n-adapter");
const {
    exportWorkflow,
    importWorkflow,
    parseWorkflow,
    scanWorkflowFile,
    serializeWorkflow
} = require("./lib/workflow-io");

function scanWorkflow(workflow, options = {}) {
    if (
        !options ||
        typeof options !== "object" ||
        Array.isArray(options)
    ) {
        throw new TervicSdkError(
            "options must be an object.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    const platform = options.platform === undefined ? "n8n" : options.platform;
    return runChecks(workflow, platform);
}

module.exports = {
    createN8nClient,
    exportWorkflow,
    importWorkflow,
    parseWorkflow,
    scanForSecrets,
    scanWorkflow,
    scanWorkflowFile,
    serializeWorkflow,
    TervicSdkError,
    validateN8n
};
