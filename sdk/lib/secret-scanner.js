"use strict";

const { TervicSdkError } = require("./errors");

const secretPattern =
    /(api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*["']?\s*[:=]\s*["']?([^\s"',}]+)/i;

const secretKeyPattern =
    /(api[_-]?key|access[_-]?token|client[_-]?secret|password)/i;

const placeholderPattern =
    /^(your[_-]|<|replace[_-]?me|example|placeholder|xxx)/i;
const MAX_SCAN_DEPTH = 1000;

function scanForSecrets(value, location = "workflow") {
    if (typeof location !== "string") {
        throw new TervicSdkError(
            "location must be a string.",
            "ERR_TERVIC_INVALID_ARGUMENT"
        );
    }

    const findings = [];
    const findingLocations = new Set();
    const ancestors = new WeakSet();
    const stack = [{ value, location, depth: 0 }];

    while (stack.length > 0) {
        const current = stack.pop();

        if (current.exit) {
            ancestors.delete(current.value);
            continue;
        }

        if (current.depth > MAX_SCAN_DEPTH) {
            throw new TervicSdkError(
                `Maximum scan depth of ${MAX_SCAN_DEPTH} exceeded at ${current.location}.`,
                "ERR_TERVIC_MAX_DEPTH"
            );
        }

        if (typeof current.value === "string") {
            if (
                current.key &&
                secretKeyPattern.test(current.key) &&
                current.value.trim() !== "" &&
                !placeholderPattern.test(current.value) &&
                !findingLocations.has(current.location)
            ) {
                findings.push({
                    ruleId: "SECRET001",
                    severity: "warning",
                    message: "Possible hardcoded secret detected. Review it locally; do not share its value.",
                    location: current.location
                });
                findingLocations.add(current.location);
            }

            const match = current.value.match(secretPattern);
            if (
                match &&
                !placeholderPattern.test(match[2]) &&
                !findingLocations.has(current.location)
            ) {
                findings.push({
                    ruleId: "SECRET001",
                    severity: "warning",
                    message: "Possible hardcoded secret detected. Review it locally; do not share its value.",
                    location: current.location
                });
                findingLocations.add(current.location);
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
                    stack.push({
                        value: current.value[index],
                        location: `${current.location}[${index}]`,
                        depth: current.depth + 1
                    });
                }
            } else {
                const entries = Object.entries(current.value);
                for (let index = entries.length - 1; index >= 0; index -= 1) {
                    const [key, item] = entries[index];
                    stack.push({
                        value: item,
                        key,
                        location: `${current.location}.${key}`,
                        depth: current.depth + 1
                    });
                }
            }
        }
    }

    return findings;
}

module.exports = { scanForSecrets };
