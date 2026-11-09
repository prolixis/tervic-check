import {
  createN8nClient,
  exportWorkflow,
  importWorkflow,
  parseWorkflow,
  scanForSecrets,
  scanWorkflow,
  scanWorkflowFile,
  serializeWorkflow,
  TervicSdkError,
  validateN8n,
  type JsonValue,
  type N8nClient
} from "../index.js";

const workflow: JsonValue = parseWorkflow(
  '{"name":"typed","nodes":[],"connections":{}}'
);
const scan = scanWorkflow(workflow);
scan.findings.forEach((finding) => {
  const ruleId: string = finding.ruleId;
  console.log(ruleId);
});

const client: N8nClient = createN8nClient({
  baseUrl: "https://n8n.example.com",
  apiKey: "test-key"
});

async function exerciseTypes(): Promise<void> {
  const localWorkflow = await importWorkflow("./workflow.json", {
    maxFileBytes: 1024
  });
  const localScan = await scanWorkflowFile("./workflow.json", {
    platform: "n8n",
    maxFileBytes: 1024
  });
  const serialized: string = serializeWorkflow(localWorkflow, { space: 2 });
  await exportWorkflow(localWorkflow, "./copy.json");
  await client.listWorkflows({ limit: 10, maxPages: 2 });
  await client.listWorkflowsPage({ limit: 10, cursor: "next" });
  await client.getWorkflow("workflow-id", { signal: new AbortController().signal });
  await client.createWorkflow(localWorkflow, { idempotencyKey: "test-key" });
  await client.updateWorkflow(1, localWorkflow);
  await client.importWorkflow("./workflow.json", { maxFileBytes: 2048 });
  await client.exportWorkflow("workflow-id", "./remote.json");
  validateN8n(localWorkflow);
  scanForSecrets(localWorkflow);
  if (localScan.totalFindings > 0) console.log(serialized);
}

void exerciseTypes;
void scan;
void TervicSdkError;
