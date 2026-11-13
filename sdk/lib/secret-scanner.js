"use strict";

const { TervicSdkError } = require("./errors");
const { createFinding } = require("./findings");

const secretPattern =
    /(api[_-]?key|access[_-]?token|auth[_-]?token|token|client[_-]?secret|secret|password|private[_-]?key)\s*["']?\s*[:=]\s*["']?([^\s"',}]+)/i;

const sensitiveNamePattern =
    /(?:^|_)(?:api_?key|access_?token|auth_?token|authorization|token|client_?secret|secret|password|passwd|private_?key|credentials?)(?:_|$)/;

const placeholderPattern =
    /^(?:\$\{[^}]*(?:\})?|\$env\.[\w.-]+|process\.env\.[\w.-]+|env:[\w.-]+|your[_-][\w.-]*|<[^>]*>|replace[_-]?me[\w.-]*|example[\w.-]*|placeholder[\w.-]*|xxx[\w.-]*|none|null|undefined|redacted|dummy|sample|test|changeme|not[_-]?set|n\/a)$/i;
const expressionPattern = /^\s*=?\s*\{\{[\s\S]*\}\}\s*$/;
const urlCredentialPattern = /^https?:\/\/([^/@\s:]+):([^/@\s]+)@/i;
const DEFAULT_MAX_SCAN_DEPTH = 1000;
const DEFAULT_MAX_VALUES = 100000;

function isSensitiveName(value) {
    const normalized = value
        .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
        .replace(/[^a-z0-9]+/gi, "_")
        .replace(/^_+|_+$/g, "")
        .toLowerCase();
    if (/(?:^|_)(?:count|length|name|type|index|enabled|required|exists)$/.test(normalized)) {
        return false;
    }
    return sensitiveNamePattern.test(normalized);
}

function scanForSecrets(value, location = "workflow", options = {}) {
    if (typeof location !== "string") {
        throw new TervicSdkError(
            "location must be a string.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }
    if (!options || typeof options !== "object" || Array.isArray(options)) {
        throw new TervicSdkError(
            "options must be an object.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    const maxDepth = options.maxDepth === undefined ? DEFAULT_MAX_SCAN_DEPTH : options.maxDepth;
    const maxValues = options.maxValues === undefined ? DEFAULT_MAX_VALUES : options.maxValues;
    if (!Number.isInteger(maxDepth) || maxDepth < 1 ||
        !Number.isInteger(maxValues) || maxValues < 1) {
        throw new TervicSdkError(
            "options.maxDepth and options.maxValues must be positive integers.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    const findings = [];
    const findingLocations = new Map();
    const ancestors = new WeakSet();
    const stack = [{ value, location, depth: 0 }];
    let scheduledValues = 1;

    function schedule(entry) {
        if (scheduledValues >= maxValues) {
            throw new TervicSdkError(
                `Maximum scanned value count of ${maxValues} exceeded.`,
                "ERR_TERVIC_MAX_VALUES"
            );
        }
        scheduledValues += 1;
        stack.push(entry);
    }

    function addSecretFinding(secretValue, secretLocation, node = null, certainty = "heuristic") {
        if (options.redactions instanceof Set && secretValue) {
            options.redactions.add(secretValue);
        }
        const existing = findingLocations.get(secretLocation);
        if (existing) {
            if (certainty === "confirmed") {
                existing.certainty = "confirmed";
                existing.message = "A non-placeholder literal appears in an authentication-related field or credential-bearing URL; its validity was not tested.";
            }
            return;
        }
        const finding = createFinding({
            ruleId: "SECRET001",
            severity: "warning",
            certainty,
            title: "Possible hardcoded credential",
            message: certainty === "confirmed"
                ? "A non-placeholder literal appears in an authentication-related field or credential-bearing URL; its validity was not tested."
                : "A string matches a credential pattern and may be exposed through shared workflow exports or logs.",
            location: secretLocation,
            nodeId: node && typeof node.id === "string" ? node.id : null,
            nodeName: node && typeof node.name === "string" ? node.name : null,
            recommendation: "Use secure credential storage or an environment reference instead of a literal value; rotate the value if this workflow was shared."
        });
        findings.push(finding);
        findingLocations.set(secretLocation, finding);
    }

    while (stack.length > 0) {
        const current = stack.pop();

        if (current.exit) {
            ancestors.delete(current.value);
            continue;
        }

        if (current.depth > maxDepth) {
            throw new TervicSdkError(
                `Maximum scan depth of ${maxDepth} exceeded at ${current.location}.`,
                "ERR_TERVIC_MAX_DEPTH"
            );
        }
        let nodeContext = current.node || null;
        const nodeMatch = current.location.match(/(?:^|\.)nodes\[(\d+)\]$/);
        if (
            nodeMatch &&
            current.value &&
            typeof current.value === "object" &&
            !Array.isArray(current.value)
        ) {
            nodeContext = current.value;
        }
        const sensitiveHeader = current.sensitiveHeader === true;
        const sensitiveNamedValue = current.value &&
                typeof current.value === "object" &&
                !Array.isArray(current.value) &&
                typeof current.value.name === "string" &&
        isSensitiveName(current.value.name);
        if (typeof current.value === "string") {
            const trimmedValue = current.value.trim();
            if (
                ((current.key && isSensitiveName(current.key)) || sensitiveHeader) &&
                trimmedValue !== "" &&
                !placeholderPattern.test(trimmedValue) &&
                !expressionPattern.test(trimmedValue)
            ) {
                addSecretFinding(current.value, current.location, nodeContext, "confirmed");
            }

            const match = current.value.match(secretPattern);
            if (
                match &&
                !placeholderPattern.test(match[2].trim()) &&
                !expressionPattern.test(match[2].trim())
            ) {
                addSecretFinding(match[2], current.location, nodeContext);
            }

            const urlCredential = current.value.match(urlCredentialPattern);
            if (urlCredential) {
                if (options.redactions instanceof Set) {
                    options.redactions.add(urlCredential[1]);
                    options.redactions.add(urlCredential[2]);
                    options.redactions.add(current.value);
                }
                addSecretFinding(
                    `${urlCredential[1]}:${urlCredential[2]}`,
                    current.location,
                    nodeContext,
                    "confirmed"
                );
            }
            continue;
        }

        if (current.value && typeof current.value === "object") {
            if (ancestors.has(current.value)) {
                throw new TervicSdkError(
                    `Cyclic data encountered while scanning at ${current.location}.`,
                    "ERR_TERVIC_CYCLIC_DATA"
                );
            }

            ancestors.add(current.value);
            stack.push({ exit: true, value: current.value });

            if (Array.isArray(current.value)) {
                for (let index = current.value.length - 1; index >= 0; index -= 1) {
                    schedule({
                        value: current.value[index],
                        location: `${current.location}[${index}]`,
                        depth: current.depth + 1,
                        node: nodeContext,
                        sensitiveHeader
                    });
                }
            } else {
                const children = [];
                for (const key in current.value) {
                    if (!Object.prototype.hasOwnProperty.call(current.value, key)) continue;
                    if (scheduledValues + children.length >= maxValues) {
                        throw new TervicSdkError(
                            `Maximum scanned value count of ${maxValues} exceeded.`,
                            "ERR_TERVIC_MAX_VALUES"
                        );
                    }
                    children.push({
                        value: current.value[key],
                        key,
                        location: `${current.location}.${key}`,
                        depth: current.depth + 1,
                        node: nodeContext,
                        sensitiveHeader: key === "value" && sensitiveNamedValue
                    });
                }
                for (let index = children.length - 1; index >= 0; index -= 1) {
                    schedule(children[index]);
                }
            }
        }
    }

    return findings;
}

module.exports = { scanForSecrets };
