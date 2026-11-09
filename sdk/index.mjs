import sdk from "./index.js";

export const createN8nClient = sdk.createN8nClient;
export const exportWorkflow = sdk.exportWorkflow;
export const importWorkflow = sdk.importWorkflow;
export const parseWorkflow = sdk.parseWorkflow;
export const scanForSecrets = sdk.scanForSecrets;
export const scanWorkflow = sdk.scanWorkflow;
export const scanWorkflowFile = sdk.scanWorkflowFile;
export const serializeWorkflow = sdk.serializeWorkflow;
export const TervicSdkError = sdk.TervicSdkError;
export const validateN8n = sdk.validateN8n;
export default sdk;
