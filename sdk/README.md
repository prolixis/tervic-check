# Tervic Flowcheck JavaScript SDK

The SDK imports and exports local workflow JSON, can import/export workflows through the n8n public REST API, and runs structural, graph, selected reliability/security, and bounded data-flow checks. It never executes a workflow.

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
  scannerVersion: "0.1.0",
  totalFindings: 0,
  findings: [],
  workflowMetrics: {
    nodeCount: 2,
    connectionCount: 1
  },
  checkSummary: {
    totalChecks: 1,
    checksExecuted: 1,
    checksPassed: 1,
    checksSkipped: 0,
    checks: [{ ruleId: "N8N001", status: "passed" }]
  },
  analysisCoverage: {
    dataFlow: {
      status: "partial",
      supportedNodeTypes: ["n8n-nodes-base.set"],
      message: "Field-reference checks only model direct connections from explicit n8n-nodes-base.set v3+ assignments with includeOtherFields disabled; other node schemas are not inferred."
    }
  }
}
```

`workflowMetrics.connectionCount` counts listed connection entries and is `null` when the connection structure is malformed or the configured scan bound is exceeded. `checkSummary.checks` reports each rule check as `passed`, `finding`, `failed`, or `skipped`; `checksPassed` counts only completed checks that reported no finding. These values describe static checks, not runtime verification.

Each finding has the same fields: `ruleId`, `severity` (`error` or `warning`), `title`, `message`, nullable `nodeId` and `nodeName`, `recommendation`, `location`, and `certainty` (`confirmed` or `heuristic`). Structural findings are confirmed errors; a confirmed secret finding means a non-placeholder literal is configured in a sensitive field, not that it is a valid credential. Inline secret-pattern matches and graph, reliability, data-flow, and other security-pattern findings are heuristic risks, not proof of failure or exploitability. Secret values are redacted from findings. Scanning never modifies the workflow.

The independent rule groups are `N8N_STRUCTURE`, `N8N_NODES`, `N8N_CONNECTIONS`, `N8N_GRAPH`, `N8N_DATA_FLOW`, `N8N_RELIABILITY`, `SECURITY_CONFIGURATION`, and `SECURITY_SECRETS`. Select or disable a group or individual finding rule:

```js
const result = scanWorkflow(workflow, {
  enabledRules: ["N8N_STRUCTURE", "N8N006"],
  limits: { maxNodes: 10000, maxConnections: 50000, maxFindings: 500 }
});
```

Available limits are `maxNodes`, `maxConnections`, `maxDepth`, `maxValues`, and `maxFindings`. Unknown rule IDs and invalid limit values produce `ERR_TERVIC_INVALID_ARGUMENT`. Rule failures are reported as engine findings while independent rule groups continue. Local file scans are limited to 50 MiB by default; pass `maxFileBytes` to `importWorkflow` or `scanWorkflowFile` to adjust the cap.

### Rule audit and evidence

| Rule | Trigger and evidence | Severity / certainty | False-positive controls and regression coverage |
| --- | --- | --- | --- |
| `N8N001` | Input is not a JSON object. | Error / confirmed | Shape-only check; invalid input test. |
| `N8N002` | `nodes` is absent or not an array. | Error / confirmed | Shape-only check; missing collection test. |
| `N8N003` | `connections` is absent or not an object. | Error / confirmed | Shape-only check; missing collection test. |
| `N8N004` | A `nodes[]` entry is not an object. | Error / confirmed | Per-entry guard; malformed-node test. |
| `N8N005` | A node ID is missing, non-string, or blank. | Error / confirmed | Node identity test. |
| `N8N006` | An exact node ID is repeated. | Error / confirmed | Duplicate-ID test. |
| `N8N007` | An exact node name is repeated, making name-based references ambiguous. | Error / confirmed | Names are compared exactly as n8n references them; whitespace-distinct names test. |
| `N8N008` | A connection source name is absent from `nodes[].name`. | Error / confirmed | Source-reference test. |
| `N8N009` | A source's connection value is not an object. | Error / confirmed | Malformed-output test. |
| `N8N010` | A connection target name is absent from `nodes[].name`. | Error / confirmed | Target-reference test. |
| `N8N011` | An output's branch collection is not an array. | Error / confirmed | Malformed-branch test. |
| `N8N012` | A branch is not an array. | Error / confirmed | Malformed-branch test. |
| `N8N013` | A connection entry lacks an object shape, non-empty target name/type, or non-negative integer output index. | Error / confirmed | Checks common n8n connection fields; malformed-connection test. |
| `N8N014` | A node name is missing, non-string, or blank. | Error / confirmed | Node identity test. |
| `N8N015` | A node type is missing, non-string, or blank. | Error / confirmed | Checks the common n8n node discriminator; required-property test. |
| `N8N016` | A typed node has an invalid/missing positive finite-number `typeVersion`, two-number `position`, or object `parameters`. | Error / confirmed | Allows fractional n8n type versions; limited to common node properties; incomplete-node test. |
| `N8N017` | Present `name`, `id`, `versionId`, `active`, `settings`, `meta`, `staticData`, `pinData`, or `tags` metadata has an invalid type/value. | Error / confirmed | Optional metadata is not required; null is accepted for optional object/list metadata; invalid-metadata test. |
| `GRAPH001` | No recognizable trigger/entry type is present. | Warning / heuristic | Custom trigger types may not be recognized; does not assert the workflow is invalid. |
| `GRAPH002` | Nodes remain unreachable from recognized trigger nodes. | Warning / heuristic | Multiple trigger nodes are all entry points; intentional or unsupported paths may be unreachable. |
| `GRAPH003` | Directed edges form multiple weakly connected components. | Warning / heuristic | Separate triggers and dormant components can be intentional. |
| `GRAPH004` | Kahn traversal confirms a directed cycle. | Warning / heuristic | Cycles may implement intentional loops; no invalidity is asserted. |
| `SECURITY001` | A literal external `http://` URL is configured. | Warning / heuristic | Loopback hosts are excluded; dynamic URLs are not guessed. |
| `SECURITY002` | An HTTP(S) URL contains literal `user:password@` information. | Warning / confirmed | Values are redacted; the finding does not validate the credentials. |
| `SECURITY003` | An Execute Command string contains command substitution, privileged/destructive commands, or a download-to-shell pattern. | Warning / heuristic | Fixed simple commands are not flagged; pattern evidence does not establish maliciousness. |
| `SECURITY004` | A webhook is directly connected to a command node whose command references `$json`. | Warning / heuristic | Reports a potential input path only; validation and actual value flow are unknown. |
| `SECURITY005` | A Webhook node explicitly sets `authentication` to `none`. | Warning / heuristic | Notes that a gateway may authenticate upstream. |
| `SECURITY006` | A Code node's `jsCode`/`functionCode` matches dynamic evaluation or process-execution patterns. | Warning / heuristic | Pattern-based only; no code is executed. |
| `SECURITY007` | An HTTP Request references a sensitive-named field explicitly assigned by its sole supported Set-node predecessor and has a static external destination. | Warning / heuristic | Field names and graph path are evidence only; payload contents and destination handling are not known. |
| `SECURITY008` | An HTTP Request's sensitive header expression references `$json` and has a direct Webhook predecessor. | Warning / heuristic | Reports a potential input path only; it cannot establish validation or the resolved header value. |
| `SECRET001` | A sensitive key/header literal or credential-bearing URL is present, or an inline credential pattern matches. | Warning / confirmed for sensitive-field literals; heuristic for inline matches | Environment references and common placeholders are excluded; actual credential validity is not tested, and values are redacted. |
| `RELIABILITY001` | `onError: continueErrorOutput` is set without a connected second `main` branch. | Warning / heuristic | Requires explicit error-routing configuration. |
| `RELIABILITY002` | `onError: continueRegularOutput` may hide an error from downstream nodes. | Warning / heuristic | Requires the explicit continue-on-error setting. |
| `RELIABILITY003` | A retried HTTP mutation (`POST`, `PUT`, `PATCH`, `DELETE`) has `retryOnFail` and `maxTries > 1`. | Warning / heuristic | Requires both retry and mutation evidence; suggests idempotency, not that duplication occurred. |
| `DATAFLOW001` | A downstream `$json.field` reference is absent from a single directly connected Set v3+ node's explicit assignments. | Warning / heuristic | Only the described Set schema is modeled; multiple inputs and unsupported schemas are skipped. |
| `TERVIC.LIMIT.001` | A configured node/connection scan bound is exceeded. | Error / confirmed | Stops the affected traversal and reports the bound. |
| `TERVIC.LIMIT.002` | Finding output exceeds `maxFindings`. | Error / confirmed | Output is truncated with an explicit summary finding. |
| `TERVIC.ENGINE.001` | An individual rule cannot complete within the input or limits. | Error / confirmed | Reports the rule failure without embedding workflow values; independent rules continue. |

SDK errors use `TervicSdkError` with stable codes including `ERR_TERVIC_INVALID_ARGUMENT`, `ERR_TERVIC_INVALID_JSON`, `ERR_TERVIC_READ_FAILED`, `ERR_TERVIC_WRITE_FAILED`, `ERR_TERVIC_FILE_TOO_LARGE`, `ERR_TERVIC_SERIALIZATION_FAILED`, `ERR_TERVIC_UNSUPPORTED_PLATFORM`, `ERR_TERVIC_CYCLIC_DATA`, `ERR_TERVIC_MAX_DEPTH`, `ERR_TERVIC_MAX_VALUES`, `ERR_TERVIC_N8N_API`, `ERR_TERVIC_TIMEOUT`, and `ERR_TERVIC_REQUEST_ABORTED`. HTTP errors expose `statusCode`; the underlying cause is available as `cause` when applicable. Error messages do not include API response bodies or credentials.

The scanner does not emit generic missing-timeout, missing-retry, missing-validation, missing-description, or pagination findings: n8n defaults and node-version behavior are not enough evidence of a defect. It does not estimate runtime cost or claim that a suspicious pattern is exploitable.

File exports are written to a unique temporary file in the destination directory and renamed into place, so failed writes do not leave a partially written destination. The destination directory must already exist.

## Compatibility and limitations

The API client targets n8n's public REST API (`/api/v1`) and expects an API key. It is tested with a local mock server, not against every n8n release, cloud plan, reverse proxy, or API-key scope. Confirm that your key has the needed permissions and test writes against a non-production instance first. The SDK does not activate, execute, delete, or manage credentials for workflows.

The scanner does not validate every node parameter or n8n release schema. The `analysisCoverage` result explicitly limits data-flow inference to direct connections from supported Set-node v3+ configurations; unsupported node schemas are not inferred. Graph and pattern-based security/reliability findings are potential risks, and the absence of a finding is not proof of safety. Secret checks can still produce false positives or false negatives. The package supports both CommonJS and native ESM, includes a TypeScript compile fixture, and has CI/package-artifact workflows. These do not replace testing against your specific n8n release, proxy, and key scopes. No n8n credentials are stored by the SDK.

To run the live smoke test, set `N8N_BASE_URL` and `N8N_API_KEY` in your environment, then run `npm run test:live` from this package directory. The test only lists one workflow page and, if present, retrieves the first workflow. It never writes, executes, activates, or deletes workflows.

Workflow writes are not exercised against a live instance automatically. API schemas and permissions may vary by n8n release and configuration; verify create/update behavior on a disposable test instance before relying on it in production. Node-parameter schema coverage remains deliberately partial.

CI checks CLI and SDK behavior on Node.js 16, 18, 20, and 22, type-checks the declaration API, and validates the package contents. Tags matching `tervic-check-sdk-v*` create a downloadable package artifact; publication to npm is intentionally not automated.

API reference: [n8n public API](https://docs.n8n.io/connect/n8n-api/) · [Authentication](https://docs.n8n.io/connect/n8n-api/authentication/) · [Pagination](https://docs.n8n.io/connect/n8n-api/pagination/).
