
const secretPattern =
    /(api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*["']?\s*[:=]\s*["']?([^\s"',}]+)/i;

const secretKeyPattern =
    /(api[_-]?key|access[_-]?token|client[_-]?secret|password)/i;

const placeholderPattern =
    /^(your[_-]|<|replace[_-]?me|example|placeholder|xxx)/i;

function scanForSecrets(value, location = "workflow") {
    const findings = [];

    if (typeof value === "string") {
        const match = value.match(secretPattern);

        if (match && !placeholderPattern.test(match[2])) {
            findings.push({
                ruleId: "SECRET001",
                severity: "warning",
                message: "Possible hardcoded secret detected. Review it locally; do not share its value.",
                location
            });
        }

        return findings;
    }

    if (Array.isArray(value)) {
        value.forEach((item, index) => {
            findings.push(
                ...scanForSecrets(item, `${location}[${index}]`)
            );
        });

        return findings;
    }

    if (value && typeof value === "object") {
        for (const [key, item] of Object.entries(value)) {
            if (
                secretKeyPattern.test(key) &&
                typeof item === "string" &&
                item.trim() !== "" &&
                !placeholderPattern.test(item)
            ) {
                findings.push({
                    ruleId: "SECRET001",
                    severity: "warning",
                    message: "Possible hardcoded secret detected. Review it locally; do not share its value.",
                    location: `${location}.${key}`
                });
            }

            findings.push(
                ...scanForSecrets(item, `${location}.${key}`)
            );
        }
    }

    return findings;
}

module.exports = { scanForSecrets };
