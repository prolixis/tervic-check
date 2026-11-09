# Tervic Check

**Inspect workflow structure and potential security risks before deployment.**

Tervic Check is a lightweight workflow reliability and security project. Its current scanner targets exported **n8n workflow JSON** and checks basic structure, node references, and potential hardcoded secrets. It does not execute workflows and does not guarantee that a workflow is secure.

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
| Runtime dependencies | None for the CLI or SDK |

The frontend uses React and Vite. Its dependencies are isolated in `frontend/` and do not affect the CLI or SDK.

## CLI

Run a scan from the repository root:

```bash
node bin/tervic-check.js examples/sample_workflow.json
```

The CLI reads and parses the supplied JSON, runs the shared scan engine, prints findings, and exits with:

| Exit code | Meaning |
| --- | --- |
| `0` | No findings were reported. This is not a security guarantee. |
| `1` | The workflow could not be parsed as JSON or findings were reported. |
| `2` | Usage, file access, or validation error. |

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

## Frontend preview

The React app is an independent Vite project in [`frontend/`](./frontend/). Start it with:

```bash
cd frontend
npm install
npm run dev
```

The current UI can select or drop an n8n JSON file, parse it locally, and display a filename and node-count preview. It does **not** run the scanner in the browser, and scan results require a backend that this repository does not yet provide.

To configure the expected backend URL, copy `frontend/.env.example` to `frontend/.env.local` and set `VITE_API_BASE_URL`. The frontend expects:

```http
POST /api/v1/scans
Content-Type: application/json
```

Request body:

```json
{
  "platform": "n8n",
  "workflow": {}
}
```

The response must include a `findings` array. The UI shows an explicit error if the backend is not configured or cannot be reached; it does not fabricate scan results.

## Development and tests

Run the CLI, SDK, and mock n8n API regression tests from the repository root:

```bash
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

- Tervic Check performs static inspection; it does not execute workflows.
- Detected secret values are not included in scanner findings or CLI reports.
- Secret matching is heuristic and can have false positives or false negatives.
- A scan with zero findings is not a guarantee of security.
- Keep n8n API keys out of source code, logs, screenshots, and reports.
- Use HTTPS for remote n8n API connections.
- Review remote create/update operations and test them on a non-production instance first.
- The frontend only sends a selected workflow to the configured backend when the user starts a scan.

## Current limitations

- The n8n adapter checks common structure and references, not every node schema or every n8n version.
- Reliability checks such as error handling, retries, reachability, and deployment portability are not yet comprehensive.
- Secret detection is pattern-based and does not replace dedicated secret-management tools.
- The n8n API client has mock-server coverage; live compatibility depends on n8n version, permissions, plan, and reverse-proxy configuration.
- Live create/update API behavior is not automatically tested.
- The frontend has no backend implementation, user accounts, or persisted scan history.
- Apache Airflow, GitHub Actions, and other workflow adapters are not implemented.

## Project structure

```text
bin/                  CLI entry point
src/                  CLI-compatible modules forwarding to shared SDK code
sdk/                  JavaScript SDK, API client, types, docs, and examples
tests/                CLI, SDK, and mock API regression tests
examples/             Sample n8n workflow JSON files
frontend/             React/Vite user-interface preview
.github/workflows/    SDK CI and package-artifact workflows
```

The CLI and SDK share the same engine, n8n adapter, and secret scanner so that behavior remains consistent.

## Roadmap

Development is incremental; the items below are planned, not claims of completed functionality.

1. **Current foundation:** local n8n JSON scanning, CLI, regression tests, SDK, frontend preview.
2. **Improve n8n coverage:** broaden realistic workflow fixtures, refine structural validation, document rule behavior.
3. **Reliability analysis:** add carefully classified error-handling, retry, and configuration checks.
4. **Developer experience:** connect the frontend to a backend, improve report formats, and add configuration.
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
