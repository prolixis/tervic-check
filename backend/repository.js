"use strict";

const { randomUUID } = require("node:crypto");

function createRepository(pool) {
    return {
        async healthCheck() {
            await pool.query("SELECT 1");
        },

        async createUser(email, passwordHash) {
            const result = await pool.query(
                `INSERT INTO users (id, email, password_hash)
                 VALUES ($1, $2, $3)
                 RETURNING id, email, created_at`,
                [randomUUID(), email, passwordHash]
            );
            return result.rows[0];
        },

        async findUserByEmail(email) {
            const result = await pool.query(
                "SELECT id, email, password_hash, created_at FROM users WHERE email = $1",
                [email]
            );
            return result.rows[0] || null;
        },

        async createSession(tokenHash, userId, expiresAt) {
            await pool.query(
                `INSERT INTO auth_sessions (token_hash, user_id, expires_at)
                 VALUES ($1, $2, $3)`,
                [tokenHash, userId, expiresAt]
            );
        },

        async findUserBySession(tokenHash) {
            const result = await pool.query(
                `SELECT users.id, users.email, users.created_at
                 FROM auth_sessions
                 JOIN users ON users.id = auth_sessions.user_id
                 WHERE auth_sessions.token_hash = $1
                   AND auth_sessions.expires_at > now()`,
                [tokenHash]
            );
            return result.rows[0] || null;
        },

        async deleteSession(tokenHash) {
            await pool.query("DELETE FROM auth_sessions WHERE token_hash = $1", [tokenHash]);
        },

        async createScan(scan) {
            const result = await pool.query(
                `INSERT INTO scans (
                    id, user_id, platform, workflow_name, workflow_external_id,
                    workflow_node_count, status, started_at
                 )
                 VALUES ($1, $2, $3, $4, $5, $6, 'running', $7)
                 RETURNING id, started_at`,
                [
                    randomUUID(),
                    scan.userId,
                    scan.platform,
                    scan.workflowName,
                    scan.workflowExternalId,
                    scan.workflowNodeCount,
                    scan.startedAt
                ]
            );
            return result.rows[0];
        },

        async completeScan(scanId, userId, result, completedAt, durationMs, workflowMetadata = {}) {
            const client = await pool.connect();
            try {
                await client.query("BEGIN");
                const updated = await client.query(
                    `UPDATE scans
                     SET status = 'completed', completed_at = $3, duration_ms = $4,
                         total_findings = $5, result = $6,
                         workflow_name = COALESCE($7, workflow_name),
                         workflow_external_id = COALESCE($8, workflow_external_id)
                     WHERE id = $1 AND user_id = $2 AND status = 'running'
                     RETURNING id`,
                    [
                        scanId,
                        userId,
                        completedAt,
                        durationMs,
                        result.totalFindings,
                        result,
                        workflowMetadata.workflowName || null,
                        workflowMetadata.workflowExternalId || null
                    ]
                );
                if (updated.rowCount !== 1) {
                    throw new Error("The scan could not be completed because its record is missing.");
                }

                for (const [findingIndex, finding] of result.findings.entries()) {
                    await client.query(
                        `INSERT INTO scan_findings (
                            scan_id, finding_index, rule_id, severity, title, message,
                            node_id, node_name, location, certainty, recommendation, details
                         )
                         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)`,
                        [
                            scanId,
                            findingIndex,
                            finding.ruleId,
                            finding.severity || null,
                            finding.title || null,
                            finding.message || null,
                            finding.nodeId || null,
                            finding.nodeName || null,
                            finding.location || null,
                            finding.certainty || null,
                            finding.recommendation || null,
                            finding
                        ]
                    );
                }
                await client.query("COMMIT");
            } catch (error) {
                await client.query("ROLLBACK");
                throw error;
            } finally {
                client.release();
            }
        },

        async failScan(scanId, userId, completedAt, durationMs, errorCode) {
            await pool.query(
                `UPDATE scans
                 SET status = 'failed', completed_at = $3, duration_ms = $4, error_code = $5
                 WHERE id = $1 AND user_id = $2 AND status = 'running'`,
                [scanId, userId, completedAt, durationMs, errorCode]
            );
        },

        async listScans(userId, limit) {
            const result = await pool.query(
                `SELECT id, platform, workflow_name, workflow_external_id,
                        workflow_node_count, status, started_at, completed_at,
                        duration_ms, total_findings, error_code,
                        result->'workflowMetrics' AS workflow_metrics,
                        (SELECT count(*)::int FROM scan_findings
                         WHERE scan_id = scans.id AND severity = 'error') AS error_finding_count,
                        (SELECT count(*)::int FROM scan_findings
                         WHERE scan_id = scans.id AND severity = 'warning') AS warning_finding_count
                 FROM scans
                 WHERE user_id = $1
                 ORDER BY started_at DESC
                 LIMIT $2`,
                [userId, limit]
            );
            return result.rows;
        },

        async findScan(scanId, userId) {
            const result = await pool.query(
                `SELECT id, platform, workflow_name, workflow_external_id,
                        workflow_node_count, status, started_at, completed_at,
                        duration_ms, total_findings, result, error_code
                 FROM scans
                 WHERE id = $1 AND user_id = $2`,
                [scanId, userId]
            );
            const scan = result.rows[0];
            if (!scan) return null;

            const findings = await pool.query(
                `SELECT details
                 FROM scan_findings
                 WHERE scan_id = $1
                 ORDER BY finding_index`,
                [scanId]
            );
            return { ...scan, findings: findings.rows.map((row) => row.details) };
        }
    };
}

module.exports = { createRepository };
