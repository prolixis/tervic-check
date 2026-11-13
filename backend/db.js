"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");
const { Pool } = require("pg");

function createPool(connectionString = process.env.DATABASE_URL) {
    if (typeof connectionString !== "string" || connectionString.trim() === "") {
        throw new Error("DATABASE_URL must be set to connect the Tervic Flowcheck API to PostgreSQL.");
    }

    const pool = new Pool({
        connectionString,
        max: 10,
        connectionTimeoutMillis: 5000,
        idleTimeoutMillis: 30000,
        application_name: "tervic-check-api"
    });
    pool.on("error", () => {
        console.error("Unexpected PostgreSQL connection-pool error.");
    });
    return pool;
}

async function runMigrations(pool) {
    const migration = await fs.readFile(
        path.join(__dirname, "migrations", "001_initial.sql"),
        "utf8"
    );
    const client = await pool.connect();

    try {
        await client.query("BEGIN");
        await client.query("SELECT pg_advisory_xact_lock($1)", [7419202601]);
        await client.query(`
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        `);
        const applied = await client.query(
            "SELECT version FROM schema_migrations WHERE version = $1",
            [1]
        );
        if (applied.rowCount === 0) {
            await client.query(migration);
            await client.query("INSERT INTO schema_migrations (version) VALUES ($1)", [1]);
        }
        await client.query("COMMIT");
    } catch (error) {
        await client.query("ROLLBACK");
        throw error;
    } finally {
        client.release();
    }
}

module.exports = { createPool, runMigrations };
