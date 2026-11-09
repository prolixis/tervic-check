# Tervic Check (v0.2)

Tervic Check is a lightweight, local static-analysis CLI for n8n workflow JSON. It checks basic workflow structure and node connections, and flags potential hardcoded secrets. It does not execute workflows or guarantee that a workflow is secure.

## Requirements

- Node.js 16 or newer

## Run a scan

```bash
node bin/tervic-check.js examples/sample_workflow.json
```

The CLI exits with code `0` when no findings are reported, `1` when the workflow has findings or cannot be parsed as JSON, and `2` for usage, file access, or validation errors.

## Run tests

Run the dependency-free regression suite with:

```bash
npm test
```

The tests cover the n8n adapter, shared engine, secret scanner, and CLI behavior including exit codes and secret-value redaction.

## JavaScript SDK

The standalone SDK in `sdk/` supports local JSON import/export, scanning, and remote n8n API workflow list/get/create/update/import/export operations. It supports CommonJS and native ESM, includes TypeScript declarations, stable error codes, bounded input sizes, and temporary-file exports. Runtime use has no third-party dependencies and supports Node.js 16 or newer.

```js
const { importWorkflow, scanWorkflow, exportWorkflow } = require("./sdk");

async function main() {
  const workflow = await importWorkflow("./workflow.json");
  const result = scanWorkflow(workflow);
  console.log(result.findings);
  await exportWorkflow(workflow, "./workflow-copy.json");
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
```

For the full API, local package installation, live-test setup, and limitations, see [sdk/README.md](./sdk/README.md). The CLI uses the same scanning implementation.

The SDK API-key client can change remote n8n data. Test remote writes only on a disposable instance and use HTTPS. The opt-in `npm run test:live` check is read-only and requires `N8N_BASE_URL` and `N8N_API_KEY`. CI covers Node.js 16, 18, 20, and 22; SDK declarations are type-checked separately.

## Frontend preview

The React frontend is an independent Vite app in `frontend/`; it does not change or add dependencies to the CLI.

```bash
cd frontend
npm install
npm run dev
```

The frontend development server requires Node.js 20.19 or newer. The existing CLI continues to support Node.js 16 or newer.

The app currently provides a local JSON file preview and a scan-results view. To connect a backend, copy `frontend/.env.example` to `frontend/.env.local`, set `VITE_API_BASE_URL`, and implement `POST /api/v1/scans`. The request body is `{ "platform": "n8n", "workflow": { ... } }`; the response must contain a `findings` array. Findings can include `ruleId`, `title`, `severity`, `message`, `location`, and `remediation`. The frontend deliberately does not show fabricated scan results when no backend is configured.
