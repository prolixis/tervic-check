# Tervic Flowcheck

**Inspect workflow structure and potential security risks before deployment.**

Tervic Flowcheck is a lightweight workflow reliability and security project. Its scanner targets exported **n8n workflow JSON** and checks common structure, graph reachability and cycles, selected configuration risks, and potential hardcoded secrets. Data-flow coverage is deliberately limited to documented node schemas. It does not execute workflows or guarantee that a workflow is secure.

The project currently contains:

- A dependency-free Node.js command-line scanner.
- A reusable JavaScript SDK for local workflow files and selected n8n API operations.
- A React frontend preview for selecting and inspecting a workflow JSON file.

> **Development status:** The CLI and SDK are early releases. The frontend is a preview and is not connected to a project backend. The checks are deliberately limited; see [Current limitations](#current-limitations).

## Contents

- [Requirements](#requirements)
- [CLI](#cli)
- [JavaScript SDK](#javascript-sdk)
- [Frontend preview](#frontend-preview)
- [Development and tests](#development-and-tests)
- [Security](#security)
- [Project structure](#project-structure)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

## Requirements

| Component | Requirement |
| --- | --- |
| CLI and SDK | Node.js 16 or newer |
| Frontend development server | Node.js 20.19 or newer |
| Runtime dependencies | None for the CLI or SDK; Express and PostgreSQL driver for the API |

The frontend uses React and Vite. Its dependencies are isolated in `frontend/` and do not affect the CLI or SDK.

## CLI

Run a scan from the repository root:

```bash
node bin/tervic-check.js examples/sample_workflow.json
```

Use `node bin/tervic-check.js --help` for usage or `node bin/tervic-check.js --version` for the installed version. The CLI accepts exactly one workflow path; use `--` before a path that begins with a hyphen. The CLI reads and parses the supplied JSON, runs the shared scan engine, prints findings, and exits with:

| Exit code | Meaning |
| --- | --- |
| `0` | No findings were reported. This is not a security guarantee. |
| `1` | The workflow could not be parsed as JSON or findings were reported. |
| `2` | Usage, file access, or validation error. |

Workflow files are limited to 50 MiB by default. The scan is read-only and does not modify the supplied workflow.

Example files are provided in [`examples/`](./examples/):

- `sample_workflow.json` — structurally valid sample.
- `invalid_workflow.json` — malformed JSON.
- `secret_workflow.json` — test fixture for secret detection.

## JavaScript SDK

The standalone SDK lives in [`sdk/`](./sdk/). It supports Node.js 16+, CommonJS and native ESM imports, and TypeScript declarations. The SDK has no third-party runtime dependencies.

### Install locally

From another Node.js project, install the SDK directory:

```bash
npm install /path/to/tervic-check/sdk
```

### Scan and copy a local workflow

CommonJS:

```js
const {
  importWorkflow,
  scanWorkflow,
  exportWorkflow
} = require("tervic-check-sdk");

async function main() {
  const workflow = await importWorkflow("./workflow.json");
  const result = scanWorkflow(workflow);

  console.log(`${result.totalFindings} finding(s)`);
  for (const finding of result.findings) {
    console.log(`[${finding.severity}] ${finding.ruleId}: ${finding.message}`);
  }

  await exportWorkflow(workflow, "./workflow-copy.json");
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
```

Native ESM:

```js
import { importWorkflow, scanWorkflow } from "tervic-check-sdk";

const workflow = await importWorkflow("./workflow.json");
const result = scanWorkflow(workflow);
console.log(result.findings);
```

### SDK capabilities

- Parse, import, serialize, and export local workflow JSON.
- Scan an in-memory workflow or scan a local JSON file.
- Validate common n8n structure and connection references.
- Detect likely hardcoded secrets without returning their values.
- Create an n8n API client to list, retrieve, create, and update workflows.
- Import local JSON to n8n or export an n8n workflow to a local JSON file.
- Follow n8n workflow-list pagination automatically or fetch one page at a time.
- Bound file and API request/response sizes; support request timeouts and cancellation.
- Return stable SDK error codes through `TervicSdkError`.

### Core scan rules

The CLI and SDK use the same independent rule groups:

| Rule group | Checks | Classification |
| --- | --- | --- |
| `N8N_STRUCTURE` | Workflow shape and present metadata types | Confirmed errors |
| `N8N_NODES` | Node objects, required common fields, and duplicate IDs/names | Confirmed errors |
| `N8N_CONNECTIONS` | Connection source/target references and branch structure | Confirmed errors |
| `N8N_GRAPH` | Trigger recognition, reachability, components, and cycles | Potential-risk warnings |
| `N8N_DATA_FLOW` | Missing field references for supported Set-node configurations | Potential-risk warnings |
| `N8N_RELIABILITY` | Explicit error-routing gaps and retried mutation requests | Potential-risk warnings |
| `SECURITY_CONFIGURATION` | Insecure URLs, shell/code execution, webhook settings, and limited sensitive-field flows | Potential-risk warnings |
| `SECURITY_SECRETS` | Literal credentials and sensitive HTTP headers | Warnings; certainty distinguishes sensitive-key literals from inline pattern matches |

Every finding has `ruleId`, `severity`, `title`, `message`, nullable `nodeId`/`nodeName`, `recommendation`, `location`, and `certainty`. Rule IDs remain stable across output surfaces. Secret values are redacted from findings. Scan results include `analysisCoverage` describing partial data-flow schema coverage. Potential-risk warnings are not proof of failure or exploitability, and structural checks are not a complete schema validator for every n8n release. See the [SDK rule audit](./sdk/README.md#rule-audit-and-evidence) for each finding ID, its trigger evidence, severity, and false-positive controls.

The SDK can disable or select rules and tune bounded scan limits:

```js
const result = scanWorkflow(workflow, {
  disabledRules: ["SECURITY_SECRETS"],
  limits: { maxNodes: 5000, maxFindings: 250 }
});
```

`enabledRules` and `disabledRules` accept rule-group IDs or individual finding IDs (for example `N8N006` or `SECRET001`). Limits include `maxNodes`, `maxConnections`, `maxDepth`, `maxValues`, and `maxFindings`. Unknown selectors and invalid limits fail explicitly. Local file scans/imports use a 50 MiB default bound; `scanWorkflowFile` and `importWorkflow` accept `maxFileBytes`.

### Connect to n8n

Create an API key in n8n under **Settings > n8n API**. Supply the key through environment variables; do not commit it to source control.

```js
const { createN8nClient } = require("tervic-check-sdk");

const n8n = createN8nClient({
  baseUrl: process.env.N8N_BASE_URL,
  apiKey: process.env.N8N_API_KEY
});

async function main() {
  const workflows = await n8n.listWorkflows({ limit: 100 });
  if (workflows.length === 0) return;

  const workflow = await n8n.getWorkflow(workflows[0].id);
  console.log(workflow.name);
}

main().catch((error) => {
  console.error(error.code, error.message);
  process.exitCode = 1;
});
```

The SDK requires HTTPS for remote instances. Plain HTTP is permitted for loopback hosts; `allowInsecureHttp: true` is available only for trusted private networks. Remote create/update calls modify the n8n instance: test them against a disposable instance first.

For complete API details, safety options, live-test setup, and error codes, see the [SDK guide](./sdk/README.md).

## Backend and frontend

The Express API lives in [`backend/`](./backend/) and scans n8n workflow JSON through the same SDK engine used by the CLI. It requires a PostgreSQL database. Create the database and application user, then place the connection string in `backend/.env` using [`backend/.env.example`](./backend/.env.example) as a template. The local `.env` is ignored by Git. On startup, the API checks PostgreSQL connectivity and applies the versioned schema in `backend/migrations/` before listening.

```powershell
cd backend
npm ci
# On first use, copy .env.example to .env and edit its DATABASE_URL.
npm start
```

The API listens on `http://127.0.0.1:3000` by default. Set `PORT` or `HOST` in `backend/.env` to change the bind address. `GET /api/health` checks PostgreSQL connectivity. Accounts can be created with `POST /api/v1/auth/register` or accessed with `POST /api/v1/auth/login`; authenticated requests use the returned bearer token. `POST /api/v1/scans` accepts `{ "platform": "n8n", "workflow": {} }`, and `GET /api/v1/scans` and `GET /api/v1/scans/:scanId` provide a user's scan history and saved findings. `POST /api/v1/auth/logout` revokes the current session.

### PostgreSQL on Windows

To start PostgreSQL automatically with Windows, open **Services** (`services.msc`), find the PostgreSQL service, open **Properties**, set **Startup type** to **Automatic**, and start the service if it is stopped. Changing the startup type may require administrator permission. The service name and version vary by installation. The backend never changes Windows service settings; if PostgreSQL is unavailable or the database cannot be reached, startup fails with a message that does not expose credentials.

The database stores account email and a scrypt password hash, hashed bearer sessions, scan status and timestamps/duration, workflow name/ID/node-count metadata, scan result summaries, and individual findings. Uploaded workflow JSON is analyzed in memory and is not stored. Each scan query is scoped to the authenticated user. Request bodies are limited to 11 MB; scans are static checks and do not execute workflows.

In a second terminal, start the React/Vite frontend:

```sh
cd frontend
npm install
npm run dev
```

The Vite development server proxies `/api` requests to the local backend. To use a different backend, set `VITE_API_PROXY_TARGET` in `frontend/.env.local`. For production, route `/api` to the Express server through your web server or hosting platform so frontend and API share an origin; the API does not enable cross-origin requests by default.

The UI parses the selected JSON locally and sends it to the API only when the signed-in user starts a scan. It displays the findings returned by the scanner and the account's saved scan history, and reports request errors rather than fabricating results.

## Development and tests

Install the backend dependencies, then run the CLI, SDK, and API regression tests from the repository root:

```bash
cd backend
npm ci
cd ..
npm test
```

Type-check the SDK declarations:

```bash
cd sdk
npm ci
npm run typecheck
```

Build the frontend:

```bash
cd frontend
npm ci
npm run build
```

Check the SDK package contents:

```bash
cd sdk
npm pack --dry-run
```

### Optional live n8n smoke test

The live test is read-only. It lists at most one page and retrieves the first workflow if one exists. It does not create, update, activate, execute, or delete workflows.

```bash
# Set these in your shell; do not put real keys in committed files.
N8N_BASE_URL=https://your-instance.example.com/api/v1
N8N_API_KEY=your-api-key

cd sdk
npm run test:live
```

On PowerShell, set the values with `$env:N8N_BASE_URL` and `$env:N8N_API_KEY` before running the command.

GitHub Actions runs tests on Node.js 16, 18, 20, and 22, checks SDK CommonJS and ESM entry points, type-checks declarations, and verifies the SDK package. Tags matching `tervic-check-sdk-v*` create a downloadable package artifact; npm publication is not automated.

## Security

- Tervic Flowcheck performs static inspection; it does not execute workflows.
- Detected secret values are not included in scanner findings or CLI reports.
- Secret matching is heuristic and can have false positives or false negatives.
- A scan with zero findings is not a guarantee of security.
- Keep n8n API keys out of source code, logs, screenshots, and reports.
- Configure PostgreSQL credentials only through environment/secrets management; use a least-privilege database user and TLS for remote databases.
- Account passwords are scrypt-hashed and API sessions expire after seven days; only the SHA-256 hash of each session token is stored.
- Workflow contents are not persisted by the API; stored scan findings contain scanner output with detected secret values redacted.
- Use HTTPS for remote n8n API connections.
- Review remote create/update operations and test them on a non-production instance first.
- The frontend only sends a selected workflow to the configured backend when the user starts a scan.

## Current limitations

- The n8n adapter checks common structure and references, not every node schema or every n8n version.
- Data-flow analysis currently models only direct connections from explicit n8n Set-node v3+ assignments; the result and UI disclose this limited coverage.
- Reliability checks cover only explicit error-routing and retry configurations with relevant evidence; they do not prove recovery behavior or measure runtime performance.
- Secret detection is pattern-based and does not replace dedicated secret-management tools; it can produce false positives and false negatives.
- The n8n API client has mock-server coverage; live compatibility depends on n8n version, permissions, plan, and reverse-proxy configuration.
- Live create/update API behavior is not automatically tested.
- Account email verification, password reset, and account deletion are not implemented.
- Apache Airflow, GitHub Actions, and other workflow adapters are not implemented.

## Project structure

```text
bin/                  CLI entry point
src/                  CLI-compatible modules forwarding to shared SDK code
sdk/                  JavaScript SDK, API client, types, docs, and examples
tests/                CLI, SDK, and mock API regression tests
examples/             Sample n8n workflow JSON files
backend/              Express API and its isolated dependencies
frontend/             React/Vite frontend and development proxy
.github/workflows/    SDK CI and package-artifact workflows
```

The CLI and SDK share the same engine, n8n adapter, and secret scanner so that behavior remains consistent.

## Roadmap

Development is incremental; the items below are planned, not claims of completed functionality.

1. **Current foundation:** local n8n JSON scanning, CLI, SDK, PostgreSQL-backed accounts and scan history, frontend.
2. **Improve n8n coverage:** broaden realistic workflow fixtures, refine structural validation, document rule behavior.
3. **Reliability analysis:** add carefully classified error-handling, retry, and configuration checks.
4. **Developer experience:** improve report formats and add configuration.
5. **Platform expansion:** evaluate additional adapters such as GitHub Actions and Apache Airflow while keeping shared checks reusable.
6. **Release readiness:** add live compatibility coverage, release notes, and a reviewed package publication process.

## Contributing

Contributions and issue reports are welcome. Before opening a pull request:

1. Keep changes focused and preserve existing CLI behavior.
2. Add or update tests for behavior changes.
3. Run `npm test` and the relevant SDK or frontend checks.
4. Do not include real credentials, workflow secrets, or private customer workflow exports.
5. Update the relevant documentation and state known limitations accurately.

## License

This project is distributed under the MIT License. See the `license` field in [`package.json`](./package.json).
