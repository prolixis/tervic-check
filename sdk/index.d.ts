export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type Workflow = { [key: string]: JsonValue };

export interface Finding {
  ruleId: string;
  severity: string;
  message: string;
  location: string;
}

export interface ScanResult {
  platform: string;
  totalFindings: number;
  findings: Finding[];
}

export interface ScanOptions {
  platform?: string;
}

export interface ScanFileOptions extends ScanOptions {
  maxFileBytes?: number;
}

export interface SerializeOptions {
  space?: number | string;
}

export interface N8nClientOptions {
  baseUrl: string;
  apiKey: string;
  allowInsecureHttp?: boolean;
  timeoutMs?: number;
  maxResponseBytes?: number;
  maxRequestBytes?: number;
}

export interface ImportOptions {
  maxFileBytes?: number;
}

export interface WorkflowListOptions {
  limit?: number;
  maxPages?: number;
  signal?: AbortSignal;
}

export interface WorkflowPageOptions {
  limit?: number;
  cursor?: string;
  signal?: AbortSignal;
}

export interface WorkflowPage {
  data: JsonValue[];
  nextCursor: string | null;
}

export interface N8nRequestOptions {
  signal?: AbortSignal;
}

export interface N8nCreateWorkflowOptions extends N8nRequestOptions {
  idempotencyKey?: string;
}

export interface N8nClient {
  listWorkflows(options?: WorkflowListOptions): Promise<JsonValue[]>;
  listWorkflowsPage(options?: WorkflowPageOptions): Promise<WorkflowPage>;
  getWorkflow(workflowId: string | number, options?: N8nRequestOptions): Promise<JsonValue>;
  createWorkflow(workflow: JsonValue, options?: N8nCreateWorkflowOptions): Promise<JsonValue>;
  updateWorkflow(
    workflowId: string | number,
    workflow: JsonValue,
    options?: N8nRequestOptions
  ): Promise<JsonValue>;
  importWorkflow(
    filePath: string,
    options?: N8nCreateWorkflowOptions & ImportOptions
  ): Promise<JsonValue>;
  exportWorkflow(
    workflowId: string | number,
    filePath: string,
    options?: N8nRequestOptions
  ): Promise<string>;
}

export class TervicSdkError extends Error {
  constructor(message: string, code: string, cause?: unknown);
  code: string;
  statusCode?: number;
  cause?: unknown;
}

export function createN8nClient(options: N8nClientOptions): N8nClient;
export function importWorkflow(filePath: string, options?: ImportOptions): Promise<JsonValue>;
export function parseWorkflow(json: string): JsonValue;
export function serializeWorkflow(workflow: JsonValue, options?: SerializeOptions): string;
export function exportWorkflow(
  workflow: JsonValue,
  filePath: string,
  options?: SerializeOptions
): Promise<string>;
export function scanWorkflow(workflow: unknown, options?: ScanOptions): ScanResult;
export function scanWorkflowFile(
  filePath: string,
  options?: ScanFileOptions
): Promise<ScanResult>;
export function validateN8n(workflow: unknown): Finding[];
export function scanForSecrets(value: unknown, location?: string): Finding[];
