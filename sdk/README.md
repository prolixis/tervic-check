# Tervic Check JavaScript SDK

The SDK imports and exports local workflow JSON, can import/export workflows through the n8n public REST API, and runs structural and secret checks. It never executes a workflow.

## Requirements

- Node.js 16 or newer
- No third-party dependencies

## Use from a downloaded repository

```js
const {
  exportWorkflow,
  importWorkflow,
  scanWorkflow
} = require("./sdk");

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

The `./sdk` path is relative to the JavaScript file that calls `require`.

## Install as a local package

From another Node.js project, run:

```bash
npm install /path/to/tervic-check/sdk
```

Then import the API:

```js
const { importWorkflow, scanWorkflow } = require("tervic-check-sdk");
```

For native ES modules:

```js
import { importWorkflow, scanWorkflow } from "tervic-check-sdk";
```

## Connect to an n8n instance

Create an API key in n8n under **Settings > n8n API**. Pass it at runtime; do not commit keys to source control or print them in logs. The SDK sends it in the `X-N8N-API-KEY` header.

```js
const { createN8nClient } = require("tervic-check-sdk");

const n8n = createN8nClient({
  baseUrl: process.env.N8N_BASE_URL, // e.g. https://n8n.example.com/api/v1
  apiKey: process.env.N8N_API_KEY
});

async function main() {
  const workflows = await n8n.listWorkflows({ limit: 100 });
  const workflow = await n8n.getWorkflow(workflows[0].id);

  // API writes change the remote n8n instance.
  const created = await n8n.createWorkflow(workflow, {
    idempotencyKey: "import-customer-workflow-v1"
  });
  await n8n.updateWorkflow(created.id, workflow);

  // Local JSON file -> remote workflow create.
  await n8n.importWorkflow("./workflow.json");

  // Remote workflow -> local JSON file.
  await n8n.exportWorkflow(created.id, "./workflow-export.json");
}

main().catch((error) => {
  console.error(error.code, error.message);
  process.exitCode = 1;
});
```

`listWorkflows` follows n8n cursor pagination and returns a single array. `listWorkflowsPage` returns `{ data, nextCursor }` for callers that want to manage paging themselves. The list limit must be between 1 and 250; `maxPages` bounds the total number of requests. API calls have a 15-second total timeout and 10 MiB request and response limits by default, configurable via `timeoutMs`, `maxRequestBytes`, and `maxResponseBytes` when creating the client. Requests can be cancelled with an `AbortSignal`. Local file imports are limited to 50 MiB by default; pass `{ maxFileBytes }` to `importWorkflow`, `scanWorkflowFile`, or the client's `importWorkflow` to change this limit.

The client rejects redirects, does not include response bodies or API keys in errors, and requires HTTPS for remote hosts. Plain HTTP is permitted for loopback hosts; set `allowInsecureHttp: true` only for a trusted private network. `createWorkflow` supports the n8n `Idempotency-Key` header. Create/update requests send only the documented writable workflow fields (`name`, `nodes`, `connections`, `settings`, `staticData`, `pinData`, and `meta`), not server-managed identifiers or timestamps.

## API

- `createN8nClient(options)` — create an API client with `baseUrl` and `apiKey`.
- `client.listWorkflows(options?)` — list all workflows, following cursor pages.
- `client.listWorkflowsPage(options?)` — fetch one page; supports `limit`, `cursor`, and `signal`.
- `client.getWorkflow(id, options?)` — retrieve one remote workflow.
- `client.createWorkflow(workflow, options?)` — create a workflow remotely.
- `client.updateWorkflow(id, workflow, options?)` — update a remote workflow.
- `client.importWorkflow(filePath, options?)` — read local JSON and create it remotely.
- `client.exportWorkflow(id, filePath, options?)` — retrieve a remote workflow and save it locally.
- `importWorkflow(filePath, options?)` — asynchronously read and parse a workflow JSON file. `options.maxFileBytes` sets its maximum size (50 MiB by default).
- `parseWorkflow(json)` — parse JSON text synchronously.
- `serializeWorkflow(workflow, options?)` — serialize to formatted JSON text. Supports `options.space`.
- `exportWorkflow(workflow, filePath, options?)` — serialize and write JSON to a file; returns the absolute output path.
- `scanWorkflow(workflow, options?)` — scan an in-memory workflow; `options.platform` defaults to `"n8n"`.
- `scanWorkflowFile(filePath, options?)` — import and scan a workflow file.
- `validateN8n(workflow)` — run only n8n-specific structural checks.
- `scanForSecrets(workflow)` — run only the shared secret checks.
- `TervicSdkError` — typed SDK error with a stable `code` and the underlying `cause` when available.

The scan result shape is:

```js
{
  platform: "n8n",
  totalFindings: 0,
  findings: []
}
```

Findings do not include detected secret values. Secret detection is heuristic and can produce false positives or miss secrets; a clean result is not a security guarantee.

SDK errors use `TervicSdkError` with stable codes including `ERR_TERVIC_INVALID_ARGUMENT`, `ERR_TERVIC_INVALID_JSON`, `ERR_TERVIC_READ_FAILED`, `ERR_TERVIC_WRITE_FAILED`, `ERR_TERVIC_FILE_TOO_LARGE`, `ERR_TERVIC_SERIALIZATION_FAILED`, `ERR_TERVIC_UNSUPPORTED_PLATFORM`, `ERR_TERVIC_CYCLIC_DATA`, `ERR_TERVIC_MAX_DEPTH`, `ERR_TERVIC_N8N_API`, `ERR_TERVIC_TIMEOUT`, and `ERR_TERVIC_REQUEST_ABORTED`. HTTP errors expose `statusCode`; the underlying cause is available as `cause` when applicable. Error messages do not include API response bodies or credentials.

File exports are written to a unique temporary file in the destination directory and renamed into place, so failed writes do not leave a partially written destination. The destination directory must already exist.

## Compatibility and limitations

The API client targets n8n's public REST API (`/api/v1`) and expects an API key. It is tested with a local mock server, not against every n8n release, cloud plan, reverse proxy, or API-key scope. Confirm that your key has the needed permissions and test writes against a non-production instance first. The SDK does not activate, execute, delete, or manage credentials for workflows.

The n8n adapter validates common workflow structure and connection references; it is not a complete schema validator for every node version or n8n release. Secret checks are heuristic. The package supports both CommonJS and native ESM, includes a TypeScript compile fixture, and has CI/package-artifact workflows. These do not replace testing against your specific n8n release, proxy, and key scopes. No n8n credentials are stored by the SDK.

To run the live smoke test, set `N8N_BASE_URL` and `N8N_API_KEY` in your environment, then run `npm run test:live` from this package directory. The test only lists one workflow page and, if present, retrieves the first workflow. It never writes, executes, activates, or deletes workflows.

Workflow writes are not exercised against a live instance automatically. API schemas and permissions may vary by n8n release and configuration; verify create/update behavior on a disposable test instance before relying on it in production. Full node-parameter schema validation and a rule registry remain future work.

CI checks CLI and SDK behavior on Node.js 16, 18, 20, and 22, type-checks the declaration API, and validates the package contents. Tags matching `tervic-check-sdk-v*` create a downloadable package artifact; publication to npm is intentionally not automated.

API reference: [n8n public API](https://docs.n8n.io/connect/n8n-api/) · [Authentication](https://docs.n8n.io/connect/n8n-api/authentication/) · [Pagination](https://docs.n8n.io/connect/n8n-api/pagination/).
