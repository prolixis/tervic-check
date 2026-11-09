"use strict";

const http = require("node:http");
const https = require("node:https");
const { TervicSdkError } = require("./errors");
const {
    exportWorkflow: exportWorkflowFile,
    importWorkflow: importWorkflowFile
} = require("./workflow-io");

const DEFAULT_TIMEOUT_MS = 15000;
const DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024;
const DEFAULT_MAX_REQUEST_BYTES = 10 * 1024 * 1024;
const MAX_PAGE_LIMIT = 250;
const WRITABLE_WORKFLOW_FIELDS = [
    "name",
    "nodes",
    "connections",
    "settings",
    "staticData",
    "pinData",
    "meta"
];

function sdkError(message, code, cause) {
    return new TervicSdkError(message, code, cause);
}

function normalizeBaseUrl(baseUrl, allowInsecureHttp) {
    if (typeof baseUrl !== "string" || baseUrl.trim() === "") {
        throw sdkError("baseUrl must be a non-empty URL.", "ERR_TERVIC_INVALID_ARGUMENT");
    }

    let url;
    try {
        url = new URL(baseUrl);
    } catch (error) {
        throw sdkError("baseUrl must be a valid absolute URL.", "ERR_TERVIC_INVALID_ARGUMENT", error);
    }

    if (
        url.username ||
        url.password ||
        url.search ||
        url.hash
    ) {
        throw sdkError(
            "baseUrl must not include credentials, a query string, or a fragment.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    if (url.protocol !== "https:" && url.protocol !== "http:") {
        throw sdkError("baseUrl must use HTTP or HTTPS.", "ERR_TERVIC_INVALID_ARGUMENT");
    }

    const isLoopback =
        url.hostname === "localhost" ||
        url.hostname === "127.0.0.1" ||
        url.hostname === "[::1]" ||
        url.hostname === "::1";

    if (url.protocol === "http:" && !isLoopback && !allowInsecureHttp) {
        throw sdkError(
            "HTTPS is required for remote n8n instances. HTTP is allowed for loopback hosts, or set allowInsecureHttp explicitly.",
            "ERR_TERVIC_INSECURE_TRANSPORT"
        );
    }

    const normalizedPath = url.pathname.replace(/\/+$/, "");
    if (!normalizedPath.endsWith("/api/v1")) {
        url.pathname = `${normalizedPath}/api/v1`;
    } else {
        url.pathname = normalizedPath;
    }
    url.pathname = `${url.pathname.replace(/\/+$/, "")}/`;

    return url;
}

function validateWorkflow(workflow) {
    if (
        !workflow ||
        typeof workflow !== "object" ||
        Array.isArray(workflow)
    ) {
        throw sdkError(
            "workflow must be a JSON object.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    if (
        typeof workflow.name !== "string" ||
        !workflow.name.trim() ||
        !Array.isArray(workflow.nodes) ||
        !workflow.connections ||
        typeof workflow.connections !== "object" ||
        Array.isArray(workflow.connections)
    ) {
        throw sdkError(
            "workflow must include a non-empty name, a nodes array, and a connections object.",
            "ERR_TERVIC_INVALID_WORKFLOW"
        );
    }
}

function toWritableWorkflow(workflow) {
    const body = {};
    for (const field of WRITABLE_WORKFLOW_FIELDS) {
        if (Object.prototype.hasOwnProperty.call(workflow, field)) {
            body[field] = workflow[field];
        }
    }
    return body;
}

function validateRequestOptions(options, label) {
    if (!options || typeof options !== "object" || Array.isArray(options)) {
        throw sdkError(`${label} must be an object.`, "ERR_TERVIC_INVALID_ARGUMENT");
    }

    const { signal } = options;
    if (
        signal !== undefined &&
        (
            !signal ||
            typeof signal.aborted !== "boolean" ||
            typeof signal.addEventListener !== "function" ||
            typeof signal.removeEventListener !== "function"
        )
    ) {
        throw sdkError("signal must be an AbortSignal.", "ERR_TERVIC_INVALID_ARGUMENT");
    }
}

function validateIdempotencyKey(key) {
    if (key === undefined) return;

    if (
        typeof key !== "string" ||
        key.length < 1 ||
        key.length > 128 ||
        !/^[\x21-\x7e]+$/.test(key)
    ) {
        throw sdkError(
            "idempotencyKey must contain 1 to 128 visible ASCII characters.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }
}

function requestJson(baseUrl, apiKey, method, resourcePath, options) {
    const {
        body,
        timeoutMs,
        maxRequestBytes,
        maxResponseBytes,
        signal,
        idempotencyKey
    } = options;

    return new Promise((resolve, reject) => {
        const requestUrl = new URL(resourcePath.replace(/^\/+/, ""), baseUrl);
        const transport = requestUrl.protocol === "https:" ? https : http;
        const headers = {
            Accept: "application/json",
            "X-N8N-API-KEY": apiKey
        };
        if (idempotencyKey !== undefined) {
            headers["Idempotency-Key"] = idempotencyKey;
        }
        let payload;

        if (body !== undefined) {
            try {
                payload = JSON.stringify(body);
            } catch (error) {
                reject(sdkError(
                    "The n8n API request body could not be serialized as JSON.",
                    "ERR_TERVIC_SERIALIZATION_FAILED",
                    error
                ));
                return;
            }

            if (typeof payload !== "string") {
                reject(sdkError(
                    "The n8n API request body could not be serialized as JSON.",
                    "ERR_TERVIC_SERIALIZATION_FAILED"
                ));
                return;
            }

            const payloadBytes = Buffer.byteLength(payload);
            if (payloadBytes > maxRequestBytes) {
                reject(sdkError(
                    `The n8n API request exceeded the ${maxRequestBytes}-byte limit.`,
                    "ERR_TERVIC_REQUEST_TOO_LARGE"
                ));
                return;
            }

            headers["Content-Type"] = "application/json";
            headers["Content-Length"] = payloadBytes;
        }

        if (signal && signal.aborted) {
            reject(sdkError("The n8n API request was aborted.", "ERR_TERVIC_REQUEST_ABORTED"));
            return;
        }

        let settled = false;
        let timedOut = false;
        let timeoutTimer;
        const finish = (error, value) => {
            if (settled) return;
            settled = true;
            clearTimeout(timeoutTimer);
            if (signal) signal.removeEventListener("abort", onAbort);
            if (error) reject(error);
            else resolve(value);
        };

        const request = transport.request(requestUrl, {
            method,
            headers,
            timeout: timeoutMs
        }, (response) => {
            const chunks = [];
            let bytes = 0;

            response.on("data", (chunk) => {
                bytes += chunk.length;
                if (bytes > maxResponseBytes) {
                    const error = sdkError(
                        `The n8n API response exceeded the ${maxResponseBytes}-byte limit.`,
                        "ERR_TERVIC_RESPONSE_TOO_LARGE"
                    );
                    response.destroy(error);
                    request.destroy(error);
                    finish(error);
                    return;
                }

                chunks.push(chunk);
            });

            response.on("error", (error) => {
                if (error.code === "ERR_TERVIC_RESPONSE_TOO_LARGE") {
                    finish(error);
                    return;
                }
                finish(sdkError(
                    "The n8n API response could not be read.",
                    "ERR_TERVIC_NETWORK",
                    error
                ));
            });

            response.on("end", () => {
                if (settled) return;
                const statusCode = response.statusCode || 0;

                if (statusCode < 200 || statusCode >= 300) {
                    const error = sdkError(
                        `The n8n API request failed with HTTP ${statusCode}.`,
                        statusCode >= 300 && statusCode < 400
                            ? "ERR_TERVIC_REDIRECT_REJECTED"
                            : "ERR_TERVIC_N8N_API"
                    );
                    error.statusCode = statusCode;
                    finish(error);
                    return;
                }

                if (statusCode === 204 || bytes === 0) {
                    finish(null, null);
                    return;
                }

                let result;
                try {
                    result = JSON.parse(Buffer.concat(chunks).toString("utf8"));
                } catch (error) {
                    finish(sdkError(
                        "The n8n API returned an invalid JSON response.",
                        "ERR_TERVIC_INVALID_API_RESPONSE",
                        error
                    ));
                    return;
                }

                finish(null, result);
            });
        });

        const onAbort = () => {
            request.destroy();
            finish(sdkError("The n8n API request was aborted.", "ERR_TERVIC_REQUEST_ABORTED"));
        };

        if (signal) signal.addEventListener("abort", onAbort, { once: true });

        timeoutTimer = setTimeout(() => {
            timedOut = true;
            request.destroy();
            finish(sdkError(
                `The n8n API request timed out after ${timeoutMs} ms.`,
                "ERR_TERVIC_TIMEOUT"
            ));
        }, timeoutMs);

        request.on("error", (error) => {
            if (error instanceof TervicSdkError) {
                finish(error);
            } else if (timedOut) {
                finish(sdkError(
                    `The n8n API request timed out after ${timeoutMs} ms.`,
                    "ERR_TERVIC_TIMEOUT",
                    error
                ));
            } else if (!settled) {
                finish(sdkError(
                    "Could not connect to the n8n API.",
                    "ERR_TERVIC_NETWORK",
                    error
                ));
            }
        });

        if (payload !== undefined) request.write(payload);
        request.end();
    });
}

function createN8nClient(options) {
    if (!options || typeof options !== "object" || Array.isArray(options)) {
        throw sdkError("options must be an object.", "ERR_TERVIC_INVALID_ARGUMENT");
    }

    if (typeof options.apiKey !== "string" || options.apiKey.trim() === "") {
        throw sdkError("apiKey must be a non-empty string.", "ERR_TERVIC_INVALID_ARGUMENT");
    }

    if (!/^[\x21-\x7e]+$/.test(options.apiKey)) {
        throw sdkError(
            "apiKey must contain visible ASCII characters only.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    const baseUrl = normalizeBaseUrl(options.baseUrl, options.allowInsecureHttp === true);
    const timeoutMs = options.timeoutMs === undefined ? DEFAULT_TIMEOUT_MS : options.timeoutMs;
    const maxResponseBytes = options.maxResponseBytes === undefined
        ? DEFAULT_MAX_RESPONSE_BYTES
        : options.maxResponseBytes;
    const maxRequestBytes = options.maxRequestBytes === undefined
        ? DEFAULT_MAX_REQUEST_BYTES
        : options.maxRequestBytes;

    if (!Number.isInteger(timeoutMs) || timeoutMs < 1) {
        throw sdkError("timeoutMs must be a positive integer.", "ERR_TERVIC_INVALID_ARGUMENT");
    }

    if (!Number.isInteger(maxResponseBytes) || maxResponseBytes < 1) {
        throw sdkError(
            "maxResponseBytes must be a positive integer.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }
    if (!Number.isInteger(maxRequestBytes) || maxRequestBytes < 1) {
        throw sdkError(
            "maxRequestBytes must be a positive integer.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    const apiKey = options.apiKey;

    function request(method, resourcePath, requestOptions = {}) {
        return requestJson(baseUrl, apiKey, method, resourcePath, {
            body: requestOptions.body,
            idempotencyKey: requestOptions.idempotencyKey,
            timeoutMs,
            maxRequestBytes,
            maxResponseBytes,
            signal: requestOptions.signal
        });
    }

    function workflowIdPath(workflowId) {
        if (
            typeof workflowId !== "string" &&
            typeof workflowId !== "number"
        ) {
            throw sdkError("workflowId must be a string or number.", "ERR_TERVIC_INVALID_ARGUMENT");
        }

        const id = String(workflowId);
        if (!id.trim()) {
            throw sdkError("workflowId must not be empty.", "ERR_TERVIC_INVALID_ARGUMENT");
        }

        return `/workflows/${encodeURIComponent(id)}`;
    }

    async function listWorkflowsPage(pageOptions = {}) {
        validateRequestOptions(pageOptions, "page options");
        const limit = pageOptions.limit === undefined ? 100 : pageOptions.limit;
        if (!Number.isInteger(limit) || limit < 1 || limit > MAX_PAGE_LIMIT) {
            throw sdkError(
                `limit must be an integer between 1 and ${MAX_PAGE_LIMIT}.`,
                "ERR_TERVIC_INVALID_ARGUMENT"
            );
        }

        if (
            pageOptions.cursor !== undefined &&
            typeof pageOptions.cursor !== "string"
        ) {
            throw sdkError("cursor must be a string.", "ERR_TERVIC_INVALID_ARGUMENT");
        }

        const query = new URLSearchParams({ limit: String(limit) });
        if (pageOptions.cursor !== undefined) query.set("cursor", pageOptions.cursor);

        const result = await request("GET", `/workflows?${query.toString()}`, {
            signal: pageOptions.signal
        });

        if (!result || !Array.isArray(result.data)) {
            throw sdkError(
                "The n8n API returned an unexpected workflow-list response.",
                "ERR_TERVIC_INVALID_API_RESPONSE"
            );
        }

        if (
            result.nextCursor !== undefined &&
            result.nextCursor !== null &&
            result.nextCursor !== "" &&
            typeof result.nextCursor !== "string"
        ) {
            throw sdkError(
                "The n8n API returned an invalid pagination cursor.",
                "ERR_TERVIC_INVALID_API_RESPONSE"
            );
        }

        return {
            data: result.data,
            nextCursor: result.nextCursor || null
        };
    }

    async function listWorkflows(listOptions = {}) {
        validateRequestOptions(listOptions, "list options");
        const maxPages = listOptions.maxPages === undefined ? 1000 : listOptions.maxPages;
        if (!Number.isInteger(maxPages) || maxPages < 1) {
            throw sdkError("maxPages must be a positive integer.", "ERR_TERVIC_INVALID_ARGUMENT");
        }

        const workflows = [];
        const cursors = new Set();
        let cursor;

        for (let page = 0; page < maxPages; page += 1) {
            const result = await listWorkflowsPage({
                limit: listOptions.limit,
                cursor,
                signal: listOptions.signal
            });

            workflows.push(...result.data);
            if (result.nextCursor === null) {
                return workflows;
            }
            if (cursors.has(result.nextCursor)) {
                throw sdkError(
                    "The n8n API repeated a pagination cursor.",
                    "ERR_TERVIC_PAGINATION_LOOP"
                );
            }

            cursors.add(result.nextCursor);
            cursor = result.nextCursor;
        }

        throw sdkError(
            `Workflow listing exceeded the configured maximum of ${maxPages} pages.`,
            "ERR_TERVIC_MAX_PAGES"
        );
    }

    async function getWorkflow(workflowId, requestOptions = {}) {
        validateRequestOptions(requestOptions, "request options");
        const result = await request("GET", workflowIdPath(workflowId), requestOptions);
        if (!result || typeof result !== "object" || Array.isArray(result)) {
            throw sdkError(
                "The n8n API returned an unexpected workflow response.",
                "ERR_TERVIC_INVALID_API_RESPONSE"
            );
        }
        return result;
    }

    async function createWorkflow(workflow, requestOptions = {}) {
        validateRequestOptions(requestOptions, "request options");
        validateIdempotencyKey(requestOptions.idempotencyKey);
        validateWorkflow(workflow);
        return request("POST", "/workflows", {
            body: toWritableWorkflow(workflow),
            idempotencyKey: requestOptions.idempotencyKey,
            signal: requestOptions.signal
        });
    }

    async function updateWorkflow(workflowId, workflow, requestOptions = {}) {
        validateRequestOptions(requestOptions, "request options");
        validateWorkflow(workflow);
        return request("PUT", workflowIdPath(workflowId), {
            body: toWritableWorkflow(workflow),
            signal: requestOptions.signal
        });
    }

    async function importWorkflow(filePath, requestOptions = {}) {
        validateRequestOptions(requestOptions, "request options");
        validateIdempotencyKey(requestOptions.idempotencyKey);
        const workflow = await importWorkflowFile(filePath, {
            maxFileBytes: requestOptions.maxFileBytes
        });
        return createWorkflow(workflow, requestOptions);
    }

    async function exportWorkflowToFile(workflowId, filePath, requestOptions = {}) {
        const workflow = await getWorkflow(workflowId, requestOptions);
        return exportWorkflowFile(workflow, filePath);
    }

    return Object.freeze({
        createWorkflow,
        exportWorkflow: exportWorkflowToFile,
        getWorkflow,
        importWorkflow,
        listWorkflows,
        listWorkflowsPage,
        updateWorkflow
    });
}

module.exports = { createN8nClient };
