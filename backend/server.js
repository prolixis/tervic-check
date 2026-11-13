"use strict";

require("dotenv").config({ path: require("node:path").join(__dirname, ".env") });

const express = require("express");
const { scanWorkflow } = require("../sdk");
const {
    createSessionToken,
    hashPassword,
    hashSessionToken,
    verifyLoginPassword
} = require("./auth");
const { createPool, runMigrations } = require("./db");
const { createRepository } = require("./repository");

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_HISTORY_LIMIT = 100;

function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function publicUser(user) {
    return {
        id: user.id,
        email: user.email,
        createdAt: user.created_at || user.createdAt
    };
}

function publicScan(scan) {
    const findings = scan.findings || scan.result?.findings || [];
    const errorCount = scan.error_finding_count === undefined
        ? findings.filter((finding) => finding.severity === "error").length
        : Number(scan.error_finding_count);
    const warningCount = scan.warning_finding_count === undefined
        ? findings.filter((finding) => finding.severity === "warning").length
        : Number(scan.warning_finding_count);
    return {
        id: scan.id,
        platform: scan.platform,
        workflow: {
            name: scan.workflow_name || null,
            externalId: scan.workflow_external_id || null,
            nodeCount: scan.workflow_node_count
        },
        status: scan.status,
        startedAt: scan.started_at,
        completedAt: scan.completed_at,
        durationMs: scan.duration_ms === null ? null : Number(scan.duration_ms),
        totalFindings: scan.total_findings,
        severitySummary: {
            critical: 0,
            high: errorCount,
            medium: warningCount,
            low: 0,
            informational: 0
        },
        workflowMetrics: scan.workflow_metrics || scan.result?.workflowMetrics || null,
        errorCode: scan.error_code || null,
        ...(scan.findings ? { findings: scan.findings } : {}),
        ...(scan.result ? { result: scan.result } : {})
    };
}

function validateCredentials(body) {
    if (!isRecord(body) || typeof body.email !== "string" ||
        typeof body.password !== "string") {
        return { error: "Provide an email address and password." };
    }

    const email = body.email.trim().toLowerCase();
    if (email.length > 254 || !EMAIL_PATTERN.test(email)) {
        return { error: "Provide a valid email address." };
    }
    if (body.password.length < 12 || body.password.length > 128 ||
        Buffer.byteLength(body.password, "utf8") > 1024) {
        return { error: "Password must be 12 to 128 characters long." };
    }
    return { email, password: body.password };
}

function metadataString(value, maxLength) {
    return typeof value === "string" && value.trim() !== ""
        ? value.trim().slice(0, maxLength)
        : null;
}

function safeWorkflowMetadata(workflow, findings) {
    const secretLocations = new Set(findings
        .filter((finding) => finding.ruleId === "SECRET001")
        .map((finding) => finding.location));
    return {
        workflowName: secretLocations.has("workflow.name")
            ? "[REDACTED]"
            : isRecord(workflow) ? metadataString(workflow.name, 500) : null,
        workflowExternalId: secretLocations.has("workflow.id")
            ? "[REDACTED]"
            : isRecord(workflow) ? metadataString(workflow.id, 255) : null
    };
}

function createApp({ repository } = {}) {
    if (!repository) {
        throw new Error("A PostgreSQL-backed repository must be provided to create the API.");
    }

    const app = express();
    app.disable("x-powered-by");
    app.use(express.json({ limit: "11mb", strict: true }));

    app.get("/api/health", async (_request, response) => {
        try {
            await repository.healthCheck();
            response.json({ status: "ok", database: "connected" });
        } catch (_error) {
            response.status(503).json({ status: "unavailable", database: "disconnected" });
        }
    });

    app.post("/api/v1/auth/register", async (request, response, next) => {
        if (!request.is("application/json")) {
            response.status(415).json({ error: "Content-Type must be application/json." });
            return;
        }

        const credentials = validateCredentials(request.body);
        if (credentials.error) {
            response.status(400).json({ error: credentials.error });
            return;
        }

        try {
            const passwordHash = await hashPassword(credentials.password);
            const user = await repository.createUser(credentials.email, passwordHash);
            const session = createSessionToken();
            await repository.createSession(session.tokenHash, user.id, session.expiresAt);
            response.status(201).json({
                user: publicUser(user),
                token: session.token,
                expiresAt: session.expiresAt.toISOString()
            });
        } catch (error) {
            if (error && error.code === "23505") {
                response.status(409).json({ error: "An account with that email already exists." });
                return;
            }
            next(error);
        }
    });

    app.post("/api/v1/auth/login", async (request, response, next) => {
        if (!request.is("application/json")) {
            response.status(415).json({ error: "Content-Type must be application/json." });
            return;
        }

        if (!isRecord(request.body) || typeof request.body.email !== "string" ||
            typeof request.body.password !== "string" ||
            request.body.password.length < 1 ||
            request.body.password.length > 128 ||
            Buffer.byteLength(request.body.password, "utf8") > 1024) {
            response.status(400).json({ error: "Provide an email address and password." });
            return;
        }

        const email = request.body.email.trim().toLowerCase();
        if (email.length > 254 || !EMAIL_PATTERN.test(email)) {
            response.status(400).json({ error: "Provide a valid email address." });
            return;
        }

        try {
            const user = await repository.findUserByEmail(email);
            const validPassword = await verifyLoginPassword(
                request.body.password,
                user && user.password_hash
            );
            if (!validPassword) {
                response.status(401).json({ error: "Email or password is incorrect." });
                return;
            }

            const session = createSessionToken();
            await repository.createSession(session.tokenHash, user.id, session.expiresAt);
            response.json({
                user: publicUser(user),
                token: session.token,
                expiresAt: session.expiresAt.toISOString()
            });
        } catch (error) {
            next(error);
        }
    });

    app.use("/api/v1", async (request, response, next) => {
        const authorization = request.get("authorization") || "";
        const match = /^Bearer ([A-Za-z0-9_-]{43})$/.exec(authorization);
        if (!match) {
            response.status(401).json({ error: "Authentication is required." });
            return;
        }

        try {
            const user = await repository.findUserBySession(hashSessionToken(match[1]));
            if (!user) {
                response.status(401).json({ error: "Session is invalid or expired." });
                return;
            }
            request.user = user;
            request.sessionTokenHash = hashSessionToken(match[1]);
            next();
        } catch (error) {
            next(error);
        }
    });

    app.get("/api/v1/auth/me", (request, response) => {
        response.json({ user: publicUser(request.user) });
    });

    app.post("/api/v1/auth/logout", async (request, response, next) => {
        try {
            await repository.deleteSession(request.sessionTokenHash);
            response.status(204).end();
        } catch (error) {
            next(error);
        }
    });

    app.get("/api/v1/scans", async (request, response, next) => {
        const rawLimit = request.query.limit;
        const limit = rawLimit === undefined ? 20 : Number(rawLimit);
        if (!Number.isInteger(limit) || limit < 1 || limit > MAX_HISTORY_LIMIT) {
            response.status(400).json({ error: `limit must be an integer from 1 to ${MAX_HISTORY_LIMIT}.` });
            return;
        }

        try {
            const scans = await repository.listScans(request.user.id, limit);
            response.json({ scans: scans.map(publicScan) });
        } catch (error) {
            next(error);
        }
    });

    app.get("/api/v1/scans/:scanId", async (request, response, next) => {
        if (!UUID_PATTERN.test(request.params.scanId)) {
            response.status(404).json({ error: "Scan not found." });
            return;
        }
        try {
            const scan = await repository.findScan(request.params.scanId, request.user.id);
            if (!scan) {
                response.status(404).json({ error: "Scan not found." });
                return;
            }
            response.json({ scan: publicScan(scan) });
        } catch (error) {
            next(error);
        }
    });

    app.post("/api/v1/scans", async (request, response, next) => {
        if (!request.is("application/json")) {
            response.status(415).json({ error: "Content-Type must be application/json." });
            return;
        }

        const body = request.body;
        if (!isRecord(body) || !Object.prototype.hasOwnProperty.call(body, "workflow")) {
            response.status(400).json({ error: "Request body must include a workflow." });
            return;
        }

        const platform = body.platform === undefined ? "n8n" : body.platform;
        const workflow = body.workflow;
        const startedAt = new Date();
        const startedClock = process.hrtime.bigint();
        let scan;
        try {
            scan = await repository.createScan({
                userId: request.user.id,
                platform: typeof platform === "string" ? platform.slice(0, 32) : "invalid",
                workflowName: null,
                workflowExternalId: null,
                workflowNodeCount: isRecord(workflow) && Array.isArray(workflow.nodes)
                    ? workflow.nodes.length
                    : null,
                startedAt
            });

            const result = scanWorkflow(workflow, { platform });
            const workflowMetadata = safeWorkflowMetadata(workflow, result.findings);
            const completedAt = new Date();
            const durationMs = Number((process.hrtime.bigint() - startedClock) / 1000000n);
            await repository.completeScan(
                scan.id,
                request.user.id,
                result,
                completedAt,
                durationMs,
                workflowMetadata
            );

            response.json({
                ...result,
                id: scan.id,
                status: "completed",
                workflow: {
                    name: workflowMetadata.workflowName,
                    externalId: workflowMetadata.workflowExternalId,
                    nodeCount: isRecord(workflow) && Array.isArray(workflow.nodes)
                        ? workflow.nodes.length
                        : null
                },
                startedAt: scan.started_at || startedAt,
                completedAt,
                durationMs
            });
        } catch (error) {
            if (scan) {
                try {
                    const completedAt = new Date();
                    const durationMs = Number((process.hrtime.bigint() - startedClock) / 1000000n);
                    await repository.failScan(
                        scan.id,
                        request.user.id,
                        completedAt,
                        durationMs,
                        error && typeof error.code === "string"
                            ? error.code.slice(0, 80)
                            : "ERR_SCAN_FAILED"
                    );
                } catch (persistenceError) {
                    next(persistenceError);
                    return;
                }
            }

            if (error && typeof error.code === "string" &&
                (error.code === "ERR_TERVIC_INVALID_ARGUMENT" ||
                    error.code === "ERR_TERVIC_UNSUPPORTED_PLATFORM")) {
                response.status(400).json({ error: error.message, code: error.code });
                return;
            }
            next(error);
        }
    });

    app.use((error, _request, response, _next) => {
        if (error && error.type === "entity.parse.failed") {
            response.status(400).json({ error: "Request body must be valid JSON." });
            return;
        }
        if (error && error.type === "entity.too.large") {
            response.status(413).json({ error: "Request body exceeds the 11 MB limit." });
            return;
        }

        console.error(`API request failed (${error && error.status ? error.status : "unknown"}).`);
        response.status(500).json({ error: "The request could not be completed." });
    });

    return app;
}

async function startServer() {
    const host = process.env.HOST || "127.0.0.1";
    const port = Number(process.env.PORT || 3000);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
        console.error("PORT must be an integer between 1 and 65535.");
        process.exitCode = 1;
        return;
    }

    let pool;
    try {
        pool = createPool();
        await pool.query("SELECT 1");
        await runMigrations(pool);
    } catch (error) {
        if (pool) {
            try {
                await pool.end();
            } catch (_closeError) {
                console.error("The PostgreSQL connection pool could not be closed cleanly.");
            }
        }
        if (!process.env.DATABASE_URL) {
            console.error("DATABASE_URL is missing. Add the connection string to backend/.env before starting the API.");
        } else {
            console.error("PostgreSQL initialization failed. Confirm the Windows service is running, the database exists, and backend/.env contains a valid connection string. Credentials are not logged.");
        }
        process.exitCode = 1;
        return;
    }

    const server = createApp({ repository: createRepository(pool) }).listen(port, host);
    let poolEndPromise;
    function endPool() {
        if (!poolEndPromise) poolEndPromise = pool.end();
        return poolEndPromise;
    }

    server.once("listening", () => {
        console.log(`Tervic Flowcheck API listening at http://${host}:${port}`);
    });
    server.once("error", () => {
        console.error("The Tervic Flowcheck API could not bind to the configured host and port.");
        void endPool().catch(() => {
            console.error("The PostgreSQL connection pool could not be closed cleanly.");
        });
        process.exitCode = 1;
    });
    server.on("close", () => {
        void endPool().catch(() => {
            console.error("The PostgreSQL connection pool could not be closed cleanly.");
        });
    });
}

if (require.main === module) {
    startServer().catch(() => {
        console.error("Could not start the Tervic Flowcheck API.");
        process.exitCode = 1;
    });
}

module.exports = { createApp };
