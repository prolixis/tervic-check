"use strict";

const fs = require("node:fs/promises");
const { createReadStream } = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { StringDecoder } = require("node:string_decoder");
const { runChecks } = require("./engine");
const { TervicSdkError } = require("./errors");
const DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024;

function assertFilePath(filePath, argumentName) {
    if (typeof filePath !== "string" || filePath.trim() === "") {
        throw new TervicSdkError(
            `${argumentName} must be a non-empty file path.`,
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    if (filePath.includes("\0")) {
        throw new TervicSdkError(
            `${argumentName} must not contain a null character.`,
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }
}

function assertOptions(options, argumentName) {
    if (
        !options ||
        typeof options !== "object" ||
        Array.isArray(options)
    ) {
        throw new TervicSdkError(
            `${argumentName} must be an object.`,
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }
}

function parseWorkflow(json) {
    if (typeof json !== "string") {
        throw new TervicSdkError(
            "Workflow JSON must be provided as a string.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    try {
        return JSON.parse(json);
    } catch (error) {
        throw new TervicSdkError(
            "Invalid workflow JSON.",
            "ERR_TERVIC_INVALID_JSON",
            error
        );
    }
}

async function readFileWithLimit(filePath, maxFileBytes) {
    const stream = createReadStream(filePath);
    const decoder = new StringDecoder("utf8");
    let bytes = 0;
    let contents = "";

    for await (const chunk of stream) {
        bytes += chunk.length;
        if (bytes > maxFileBytes) {
            stream.destroy();
            throw new TervicSdkError(
                `Workflow file exceeds the ${maxFileBytes}-byte limit.`,
                "ERR_TERVIC_FILE_TOO_LARGE"
            );
        }

        contents += decoder.write(chunk);
    }

    return contents + decoder.end();
}

async function importWorkflow(filePath, options = {}) {
    assertFilePath(filePath, "filePath");
    assertOptions(options, "options");
    const maxFileBytes = options.maxFileBytes === undefined
        ? DEFAULT_MAX_FILE_BYTES
        : options.maxFileBytes;
    if (!Number.isInteger(maxFileBytes) || maxFileBytes < 1) {
        throw new TervicSdkError(
            "options.maxFileBytes must be a positive integer.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    let contents;
    try {
        contents = await readFileWithLimit(filePath, maxFileBytes);
    } catch (error) {
        if (error.code === "ERR_TERVIC_FILE_TOO_LARGE") {
            throw error;
        }
        throw new TervicSdkError(
            `Unable to read workflow file "${filePath}": ${error.message}`,
            "ERR_TERVIC_READ_FAILED",
            error
        );
    }

    try {
        return parseWorkflow(contents);
    } catch (error) {
        if (error.code === "ERR_TERVIC_INVALID_JSON") {
            throw new TervicSdkError(
                `Unable to import workflow file "${filePath}": ${error.message}`,
                error.code,
                error
            );
        }

        throw error;
    }
}

function serializeWorkflow(workflow, options = {}) {
    assertOptions(options, "options");
    const space = options.space === undefined ? 2 : options.space;

    if (
        typeof space !== "number" &&
        typeof space !== "string"
    ) {
        throw new TervicSdkError(
            "options.space must be a number or string.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    let json;
    try {
        json = JSON.stringify(workflow, null, space);
    } catch (error) {
        throw new TervicSdkError(
            `Workflow cannot be serialized as JSON: ${error.message}`,
            "ERR_TERVIC_SERIALIZATION_FAILED",
            error
        );
    }

    if (typeof json !== "string") {
        throw new TervicSdkError(
            "Workflow cannot be serialized as JSON.",
            "ERR_TERVIC_SERIALIZATION_FAILED"
        );
    }

    return `${json}\n`;
}

async function exportWorkflow(workflow, filePath, options = {}) {
    assertFilePath(filePath, "filePath");
    assertOptions(options, "options");

    const outputPath = path.resolve(filePath);
    const json = serializeWorkflow(workflow, options);
    const temporaryPath = path.join(
        path.dirname(outputPath),
        `.${path.basename(outputPath)}.${randomUUID()}.tmp`
    );

    try {
        await fs.writeFile(temporaryPath, json, {
            encoding: "utf8",
            flag: "wx",
            mode: 0o600
        });
        await fs.rename(temporaryPath, outputPath);
    } catch (error) {
        try {
            await fs.unlink(temporaryPath);
        } catch (cleanupError) {
            if (cleanupError.code !== "ENOENT") {
                throw new TervicSdkError(
                    `Unable to export workflow to "${outputPath}" and could not remove temporary file "${temporaryPath}": ${error.message}; cleanup failed: ${cleanupError.message}`,
                    "ERR_TERVIC_WRITE_FAILED",
                    error
                );
            }
        }

        throw new TervicSdkError(
            `Unable to export workflow to "${outputPath}": ${error.message}`,
            "ERR_TERVIC_WRITE_FAILED",
            error
        );
    }

    return outputPath;
}

async function scanWorkflowFile(filePath, options = {}) {
    assertOptions(options, "options");
    const workflow = await importWorkflow(filePath, {
        maxFileBytes: options.maxFileBytes
    });
    return runChecks(workflow, options.platform === undefined ? "n8n" : options.platform);
}

module.exports = {
    exportWorkflow,
    importWorkflow,
    parseWorkflow,
    scanWorkflowFile,
    serializeWorkflow
};
