"use strict";

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const { runChecks } = require("../src/core/engine");
const { validateN8n } = require("../src/adapters/n8n.adapter");
const { scanForSecrets } = require("../src/core/secretScanner");
const sdk = require("../sdk");

const projectRoot = path.resolve(__dirname, "..");
const cliPath = path.join(projectRoot, "bin", "tervic-check.js");
const samplePath = path.join(projectRoot, "examples", "sample_workflow.json");
const invalidJsonPath = path.join(projectRoot, "examples", "invalid_workflow.json");
const secretWorkflowPath = path.join(projectRoot, "examples", "secret_workflow.json");

let passed = 0;
let failed = 0;
const tests = [];

function test(name, callback) {
    tests.push({ name, callback });
}

async function runTests() {
    for (const { name, callback } of tests) {
        try {
            await callback();
            passed += 1;
            console.log(`PASS ${name}`);
        } catch (error) {
            failed += 1;
            console.error(`FAIL ${name}`);
            console.error(error.stack || error.message);
        }
    }

    console.log(`\n${passed} passed, ${failed} failed.`);
    process.exitCode = failed === 0 ? 0 : 1;
}

function runCli(args) {
    return spawnSync(process.execPath, [cliPath, ...args], {
        cwd: projectRoot,
        encoding: "utf8"
    });

    test("n8n client stops after its configured pagination bound", async () => {
        let pageCount = 0;
        const mock = await createMockN8n((request, response) => {
            pageCount += 1;
            respondJson(response, 200, {
                data: [],
                nextCursor: `page-${pageCount}`
            });
        });

        try {
            const client = sdk.createN8nClient({ baseUrl: mock.baseUrl, apiKey: "test-key" });
            await assert.rejects(
                client.listWorkflows({ maxPages: 2 }),
                (error) => error.code === "ERR_TERVIC_MAX_PAGES"
            );
            assert.equal(pageCount, 2);
        } finally {
            await mock.close();
        }
    });
}

function findingIds(findings) {
    return findings.map((finding) => finding.ruleId);
}

async function createMockN8n(handler) {
    const server = http.createServer(handler);
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();

    return {
        baseUrl: `http://127.0.0.1:${address.port}/instance`,
        close: () => new Promise((resolve, reject) => {
            server.close((error) => error ? reject(error) : resolve());
        })
    };
}

function respondJson(response, statusCode, value) {
    response.writeHead(statusCode, { "Content-Type": "application/json" });
    response.end(JSON.stringify(value));
}

test("valid sample workflow passes the engine", () => {
    const workflow = JSON.parse(fs.readFileSync(samplePath, "utf8"));
    const result = runChecks(workflow);

    assert.equal(result.platform, "n8n");
    assert.equal(result.totalFindings, 0);
    assert.deepEqual(result.findings, []);
});

test("n8n adapter reports nodes without names or IDs", () => {
    const findings = validateN8n({
        nodes: [{ id: "node-1" }, { name: "Unnamed ID" }],
        connections: {}
    });

    assert.deepEqual(findingIds(findings), ["N8N014", "N8N005"]);
});

test("non-object workflows are rejected", () => {
    const findings = validateN8n(null);

    assert.deepEqual(findingIds(findings), ["N8N001"]);
});

test("missing nodes and connections are reported", () => {
    const findings = validateN8n({});

    assert.deepEqual(findingIds(findings), ["N8N002", "N8N003"]);
});

test("invalid nodes and duplicate identifiers are reported", () => {
    const findings = validateN8n({
        nodes: [
            { id: "duplicate", name: "First" },
            { id: "duplicate", name: "Second" },
            null
        ],
        connections: {}
    });

    assert.deepEqual(findingIds(findings), ["N8N006", "N8N004"]);
});

test("duplicate node names are reported", () => {
    const findings = validateN8n({
        nodes: [
            { id: "first", name: "Repeated" },
            { id: "second", name: "Repeated" }
        ],
        connections: {}
    });

    assert.deepEqual(findingIds(findings), ["N8N007"]);
});

test("missing connection sources and targets are reported", () => {
    const findings = validateN8n({
        nodes: [{ id: "node-1", name: "Existing" }],
        connections: {
            MissingSource: { main: [[{ node: "MissingTarget" }]] }
        }
    });

    assert.deepEqual(findingIds(findings), ["N8N008", "N8N010"]);
});

test("invalid connection output structures are reported", () => {
    const findings = validateN8n({
        nodes: [{ id: "node-1", name: "Existing" }],
        connections: { Existing: null }
    });

    test("n8n adapter reports malformed branches and connections", () => {
        const findings = validateN8n({
            nodes: [{ id: "node-1", name: "Existing" }],
            connections: {
                Existing: {
                    main: [null, [{}]],
                    error: "not-an-array"
                }
            }
        });

        assert.deepEqual(findingIds(findings), ["N8N012", "N8N013", "N8N011"]);
        assert(findings.every((finding) => finding.location.startsWith("connections.")));
    });

    test("n8n adapter accepts branching, optional outputs, and extra export metadata", () => {
        const findings = validateN8n({
            name: "Branching workflow",
            nodes: [
                { id: "trigger", name: "Start", type: "n8n-nodes-base.manualTrigger" },
                { id: "if", name: "Condition", type: "n8n-nodes-base.if" },
                { id: "yes", name: "Yes path", type: "n8n-nodes-base.noOp" },
                { id: "no", name: "No path", type: "n8n-nodes-base.noOp" }
            ],
            connections: {
                Start: { main: [[{ node: "Condition", type: "main", index: 0 }]] },
                Condition: {
                    main: [
                        [{ node: "Yes path", type: "main", index: 0 }],
                        [{ node: "No path", type: "main", index: 0 }]
                    ]
                },
                "Yes path": { main: [] },
                "No path": { main: [] }
            },
            settings: { executionOrder: "v1" },
            tags: [{ id: "tag-1", name: "test" }]
        });

        assert.deepEqual(findings, []);
    });

    assert.deepEqual(findingIds(findings), ["N8N009"]);
});

test("secret findings identify locations without exposing values", () => {
    const secretValue = "private-test-value-123";
    const findings = scanForSecrets({
        credentials: { api_key: secretValue }
    });

    assert.equal(findings.length, 1);
    assert.equal(findings[0].ruleId, "SECRET001");
    assert.equal(findings[0].location, "workflow.credentials.api_key");
    assert.equal(JSON.stringify(findings).includes(secretValue), false);
});

test("secret placeholders are ignored", () => {
    const findings = scanForSecrets({
        api_key: "YOUR_API_KEY",
        password: "example-password",
        client_secret: "<replace-me>"
    });

    assert.deepEqual(findings, []);
});

test("unsupported platforms fail explicitly", () => {
    assert.throws(
        () => runChecks({}, "unsupported"),
        /Unsupported platform: unsupported/
    );
});

test("SDK parses and serializes workflow JSON", () => {
    const workflow = sdk.parseWorkflow('{"name":"SDK workflow","nodes":[]}');

    assert.equal(workflow.name, "SDK workflow");
    assert.deepEqual(JSON.parse(sdk.serializeWorkflow(workflow)), workflow);
    assert.match(sdk.serializeWorkflow(workflow, { space: 0 }), /"name":"SDK workflow"/);
    assert.throws(() => sdk.parseWorkflow("{invalid"), /Invalid workflow JSON/);
    assert.throws(
        () => sdk.serializeWorkflow({ value: 1 }, { space: null }),
        (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
    );
});

test("SDK imports, scans, and exports a workflow file", async () => {
    const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "tervic-sdk-"));
    const inputPath = path.join(temporaryDirectory, "input.json");
    const outputPath = path.join(temporaryDirectory, "output.json");

    try {
        fs.copyFileSync(samplePath, inputPath);
        const workflow = await sdk.importWorkflow(inputPath);
        const result = sdk.scanWorkflow(workflow);

        assert.equal(result.totalFindings, 0);
        assert.equal(await sdk.scanWorkflowFile(inputPath).then((scan) => scan.totalFindings), 0);
        assert.equal(await sdk.exportWorkflow(workflow, outputPath), outputPath);
        assert.deepEqual(JSON.parse(fs.readFileSync(outputPath, "utf8")), workflow);
    } finally {
        fs.rmSync(temporaryDirectory, { recursive: true, force: true });
    }
});

test("SDK enforces bounded local workflow imports", async () => {
    const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "tervic-sdk-size-"));
    const inputPath = path.join(temporaryDirectory, "workflow.json");

    try {
        fs.writeFileSync(inputPath, '{"name":"Workflow"}', "utf8");
        await assert.rejects(
            sdk.importWorkflow(inputPath, { maxFileBytes: 4 }),
            (error) => error.code === "ERR_TERVIC_FILE_TOO_LARGE"
        );
        await assert.rejects(
            sdk.importWorkflow(inputPath, { maxFileBytes: 0 }),
            (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
        );
    } finally {
        fs.rmSync(temporaryDirectory, { recursive: true, force: true });
    }
});

test("SDK scan result never exposes a detected secret", () => {
    const secretValue = "sdk-private-test-value-123";
    const result = sdk.scanWorkflow({
        nodes: [],
        connections: {},
        api_key: secretValue
    });

    test("SDK secret scanner reports only one finding per value location", () => {
        const findings = sdk.scanForSecrets({
            api_key: "api_key=another-private-value"
        });

        assert.equal(findings.length, 1);
        assert.equal(findings[0].location, "workflow.api_key");
    });

    test("SDK can be imported through its native ESM entry point", async () => {
        const moduleUrl = pathToFileURL(path.join(projectRoot, "sdk", "index.mjs"));
        const esmSdk = await import(moduleUrl.href);

        assert.equal(typeof esmSdk.createN8nClient, "function");
        assert.equal(typeof esmSdk.scanWorkflow, "function");
        assert.equal(typeof esmSdk.default.scanWorkflow, "function");
        assert.equal(
            esmSdk.scanWorkflow({ nodes: [], connections: {} }).totalFindings,
            0
        );
    });

    assert(result.findings.some((finding) => finding.ruleId === "SECRET001"));
    assert.equal(JSON.stringify(result).includes(secretValue), false);
});

test("SDK reports stable errors for invalid inputs and file failures", async () => {
    assert.throws(
        () => sdk.scanWorkflow({}, null),
        (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
    );
    assert.throws(
        () => sdk.scanWorkflow({}, { platform: "other" }),
        (error) => error.code === "ERR_TERVIC_UNSUPPORTED_PLATFORM"
    );
    assert.throws(
        () => sdk.serializeWorkflow({ value: 1 }, null),
        (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
    );
    await assert.rejects(
        sdk.importWorkflow(path.join(os.tmpdir(), `missing-${process.pid}.json`)),
        (error) => error.code === "ERR_TERVIC_READ_FAILED" && Boolean(error.cause)
    );

    const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "tervic-sdk-errors-"));
    const invalidJsonPath = path.join(temporaryDirectory, "invalid.json");
    try {
        fs.writeFileSync(invalidJsonPath, "{invalid", "utf8");
        await assert.rejects(
            sdk.importWorkflow(invalidJsonPath),
            (error) => error.code === "ERR_TERVIC_INVALID_JSON" && Boolean(error.cause)
        );
        await assert.rejects(
            sdk.exportWorkflow({ value: 1 }, path.join(temporaryDirectory, "missing", "out.json")),
            (error) => error.code === "ERR_TERVIC_WRITE_FAILED" && Boolean(error.cause)
        );
        assert.deepEqual(fs.readdirSync(temporaryDirectory), ["invalid.json"]);
    } finally {
        fs.rmSync(temporaryDirectory, { recursive: true, force: true });
    }
});

test("SDK detects cyclic in-memory data without overflowing", () => {
    const workflow = { nodes: [], connections: {} };
    workflow.self = workflow;

    assert.throws(
        () => sdk.scanWorkflow(workflow),
        (error) => error.code === "ERR_TERVIC_CYCLIC_DATA"
    );
});

test("SDK bounds very deeply nested input instead of overflowing", () => {
    const workflow = { nodes: [], connections: {} };
    let current = workflow;
    for (let depth = 0; depth < 1100; depth += 1) {
        current.next = {};
        current = current.next;
    }

    assert.throws(
        () => sdk.scanWorkflow(workflow),
        (error) => error.code === "ERR_TERVIC_MAX_DEPTH"
    );
});

test("SDK export replaces an existing file without partial output", async () => {
    const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "tervic-sdk-replace-"));
    const outputPath = path.join(temporaryDirectory, "workflow.json");

    try {
        fs.writeFileSync(outputPath, '{"old":true}', "utf8");
        await sdk.exportWorkflow({ replacement: true }, outputPath);
        assert.deepEqual(JSON.parse(fs.readFileSync(outputPath, "utf8")), { replacement: true });
        assert.deepEqual(fs.readdirSync(temporaryDirectory), ["workflow.json"]);
    } finally {
        fs.rmSync(temporaryDirectory, { recursive: true, force: true });
    }
});

test("n8n client validates configuration and protects remote transport", () => {
    assert.throws(
        () => sdk.createN8nClient({ baseUrl: "https://n8n.example.com", apiKey: "" }),
        (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
    );
    assert.throws(
        () => sdk.createN8nClient({ baseUrl: "http://n8n.example.com", apiKey: "test-key" }),
        (error) => error.code === "ERR_TERVIC_INSECURE_TRANSPORT"
    );
    assert.throws(
        () => sdk.createN8nClient({ baseUrl: "https://user:pass@n8n.example.com", apiKey: "test-key" }),
        (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
    );
    assert.throws(
        () => sdk.createN8nClient({
            baseUrl: "https://n8n.example.com",
            apiKey: "test-key",
            timeoutMs: 0
        }),
        (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
    );
});

test("n8n client paginates lists and handles get, create, and update", async () => {
    const seen = [];
    const mock = await createMockN8n((request, response) => {
        seen.push({
            method: request.method,
            url: request.url,
            apiKey: request.headers["x-n8n-api-key"],
            idempotencyKey: request.headers["idempotency-key"]
        });

        if (request.url.startsWith("/instance/api/v1/workflows?")) {
            const query = new URL(request.url, "http://localhost").searchParams;
            if (!query.has("cursor")) {
                respondJson(response, 200, {
                    data: [{ id: "one" }],
                    nextCursor: "next / cursor"
                });
            } else {
                respondJson(response, 200, { data: [{ id: "two" }], nextCursor: null });
            }
            return;
        }

        if (request.method === "GET") {
            respondJson(response, 200, { id: "id/with space", name: "Remote", nodes: [], connections: {} });
            return;
        }

        let body = "";
        request.setEncoding("utf8");
        request.on("data", (chunk) => { body += chunk; });
        request.on("end", () => {
            const parsed = JSON.parse(body);
            seen[seen.length - 1].body = parsed;
            respondJson(response, request.method === "POST" ? 201 : 200, {
                id: request.method === "POST" ? "created" : "updated",
                name: parsed.name,
                received: parsed
            });
        });
    });

    try {
        const client = sdk.createN8nClient({
            baseUrl: mock.baseUrl,
            apiKey: "test-api-key"
        });
        const workflows = await client.listWorkflows({ limit: 25 });
        assert.deepEqual(workflows.map((workflow) => workflow.id), ["one", "two"]);
        assert.equal(seen[0].url, "/instance/api/v1/workflows?limit=25");
        assert(seen[1].url.includes("cursor=next+%2F+cursor"));
        const page = await client.listWorkflowsPage({
            limit: 1,
            cursor: "next / cursor"
        });
        assert.deepEqual(page.data, [{ id: "two" }]);
        assert.equal(page.nextCursor, null);
        assert.equal((await client.getWorkflow("id/with space")).name, "Remote");

        const workflow = {
            name: "SDK workflow",
            nodes: [],
            connections: {},
            id: "source-id",
            active: true,
            createdAt: "server-managed",
            settings: { executionOrder: "v1" },
            unknownField: "not-an-api-field"
        };
        assert.equal((await client.createWorkflow(workflow, {
            idempotencyKey: "create-test-1"
        })).id, "created");
        assert.deepEqual(
            Object.keys(seen[4].body),
            ["name", "nodes", "connections", "settings"]
        );
        assert.equal(seen[4].body.id, undefined);
        assert.equal(seen[4].body.active, undefined);
        assert.equal((await client.updateWorkflow("workflow-1", workflow)).id, "updated");
        assert.deepEqual(seen[5].body, seen[4].body);
        assert(seen.every((request) => request.apiKey === "test-api-key"));
        assert.equal(seen[4].idempotencyKey, "create-test-1");
        assert.deepEqual(
            seen.map((request) => request.method),
            ["GET", "GET", "GET", "GET", "POST", "PUT"]
        );
    } finally {
        await mock.close();
    }
});

test("n8n client imports local JSON and exports remote workflows", async () => {
    const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "tervic-sdk-api-files-"));
    const inputPath = path.join(temporaryDirectory, "input.json");
    const outputPath = path.join(temporaryDirectory, "remote.json");
    fs.copyFileSync(samplePath, inputPath);

    const mock = await createMockN8n((request, response) => {
        if (request.method === "POST") {
            let body = "";
            request.setEncoding("utf8");
            request.on("data", (chunk) => { body += chunk; });
            request.on("end", () => {
                respondJson(response, 201, { id: "imported", workflow: JSON.parse(body) });
            });
            return;
        }

        respondJson(response, 200, JSON.parse(fs.readFileSync(samplePath, "utf8")));
    });

    try {
        const client = sdk.createN8nClient({
            baseUrl: mock.baseUrl,
            apiKey: "test-api-key"
        });
        const imported = await client.importWorkflow(inputPath);
        assert.equal(imported.id, "imported");
        assert.equal(imported.workflow.name, "Tervic Test Workflow");

        assert.equal(await client.exportWorkflow("remote-id", outputPath), outputPath);
        assert.deepEqual(
            JSON.parse(fs.readFileSync(outputPath, "utf8")),
            JSON.parse(fs.readFileSync(samplePath, "utf8"))
        );
    } finally {
        await mock.close();
        fs.rmSync(temporaryDirectory, { recursive: true, force: true });
    }
});

test("n8n client fails safely on API errors, invalid responses, and redirects", async () => {
    const apiKey = "do-not-leak-this-key";
    const mock = await createMockN8n((request, response) => {
        if (request.url.endsWith("/unauthorized")) {
            respondJson(response, 401, { message: apiKey });
        } else if (request.url.endsWith("/bad-json")) {
            response.writeHead(200, { "Content-Type": "application/json" });
            response.end("{not-json");
        } else {
            response.writeHead(302, { Location: "https://elsewhere.example/" });
            response.end();
        }
    });

    try {
        const client = sdk.createN8nClient({ baseUrl: mock.baseUrl, apiKey });
        await assert.rejects(
            client.getWorkflow("unauthorized"),
            (error) => error.code === "ERR_TERVIC_N8N_API" &&
                error.statusCode === 401 &&
                !error.message.includes(apiKey)
        );
        await assert.rejects(
            client.getWorkflow("bad-json"),
            (error) => error.code === "ERR_TERVIC_INVALID_API_RESPONSE"
        );
        await assert.rejects(
            client.getWorkflow("redirect"),
            (error) => error.code === "ERR_TERVIC_REDIRECT_REJECTED"
        );
    } finally {
        await mock.close();
    }
});

test("n8n client enforces response limits and request timeouts", async () => {
    const mock = await createMockN8n((request, response) => {
        if (request.url.endsWith("/slow")) {
            setTimeout(() => {
                if (!response.destroyed) respondJson(response, 200, { id: "slow" });
            }, 100);
            return;
        }

        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ payload: "x".repeat(300) }));
    });

    test("n8n client enforces request size limits before sending data", async () => {
        let requestCount = 0;
        const mock = await createMockN8n((request, response) => {
            requestCount += 1;
            respondJson(response, 201, { id: "created" });
        });

        try {
            const client = sdk.createN8nClient({
                baseUrl: mock.baseUrl,
                apiKey: "test-key",
                maxRequestBytes: 32
            });
            await assert.rejects(
                client.createWorkflow({
                    name: "Large",
                    nodes: [],
                    connections: {},
                    description: "x".repeat(100)
                }),
                (error) => error.code === "ERR_TERVIC_REQUEST_TOO_LARGE"
            );
            assert.equal(requestCount, 0);
        } finally {
            await mock.close();
        }
    });

    test("n8n client supports abort signals and validates idempotency keys", async () => {
        const mock = await createMockN8n((request, response) => {
            setTimeout(() => {
                if (!response.destroyed) respondJson(response, 200, { id: "late" });
            }, 100);
        });

        try {
            const client = sdk.createN8nClient({ baseUrl: mock.baseUrl, apiKey: "test-key" });
            const controller = new AbortController();
            const pending = client.getWorkflow("slow", { signal: controller.signal });
            controller.abort();
            await assert.rejects(
                pending,
                (error) => error.code === "ERR_TERVIC_REQUEST_ABORTED"
            );
            await assert.rejects(
                client.createWorkflow(
                    { name: "Workflow", nodes: [], connections: {} },
                    { idempotencyKey: "bad\nkey" }
                ),
                (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
            );
        } finally {
            await mock.close();
        }
    });

    try {
        const sizeLimitedClient = sdk.createN8nClient({
            baseUrl: mock.baseUrl,
            apiKey: "test-key",
            maxResponseBytes: 100
        });
        await assert.rejects(
            sizeLimitedClient.getWorkflow("large"),
            (error) => error.code === "ERR_TERVIC_RESPONSE_TOO_LARGE"
        );

        const timedClient = sdk.createN8nClient({
            baseUrl: mock.baseUrl,
            apiKey: "test-key",
            timeoutMs: 20
        });
        await assert.rejects(
            timedClient.getWorkflow("slow"),
            (error) => error.code === "ERR_TERVIC_TIMEOUT"
        );
    } finally {
        await mock.close();
    }
});

test("n8n client rejects invalid workflow inputs and repeated cursors", async () => {
    const mock = await createMockN8n((request, response) => {
        if (request.url.startsWith("/instance/api/v1/workflows?")) {
            respondJson(response, 200, { data: [], nextCursor: "same" });
        } else {
            respondJson(response, 200, {});
        }
    });

    try {
        const client = sdk.createN8nClient({ baseUrl: mock.baseUrl, apiKey: "test-key" });
        await assert.rejects(
            client.createWorkflow({ nodes: [], connections: {} }),
            (error) => error.code === "ERR_TERVIC_INVALID_WORKFLOW"
        );
        await assert.rejects(
            client.listWorkflows({ maxPages: 5 }),
            (error) => error.code === "ERR_TERVIC_PAGINATION_LOOP"
        );
        await assert.rejects(
            client.listWorkflows({ limit: 251 }),
            (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
        );
    } finally {
        await mock.close();
    }
});

test("CLI passes a valid workflow with exit code 0", () => {
    const result = runCli([samplePath]);

    assert.equal(result.error, undefined);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /PASS: No issues detected/);
});

test("CLI rejects invalid JSON with exit code 1", () => {
    const result = runCli([invalidJsonPath]);

    assert.equal(result.status, 1);
    assert.match(result.stderr, /Invalid JSON file/);
});

test("CLI reports invalid workflows with exit code 1", () => {
    const result = runCli([secretWorkflowPath]);

    assert.equal(result.status, 1);
    assert.match(result.stdout, /SECRET001/);
    assert.equal(result.stdout.includes("fake_demo_key_12345"), false);
});

test("CLI reports a missing file with exit code 2", () => {
    const missingPath = path.join(os.tmpdir(), `tervic-missing-${process.pid}.json`);
    const result = runCli([missingPath]);

    assert.equal(result.status, 2);
    assert.match(result.stderr, /File not found/);
});

test("CLI prints usage and returns exit code 2 without a file", () => {
    const result = runCli([]);

    assert.equal(result.status, 2);
    assert.match(result.stderr, /Usage: tervic-check/);
});

runTests();
