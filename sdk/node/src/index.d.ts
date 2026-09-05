export type BuildAnchorResponse = Record<string, unknown>;

export interface BuildAnchorClientOptions {
  workspace?: string;
  endpoint?: string;
  token?: string;
  executable?: string;
  requestTimeoutMs?: number;
  fetch?: typeof globalThis.fetch;
}

export class BuildAnchorClientError extends Error {}
export class BuildAnchorHTTPError extends BuildAnchorClientError {
  statusCode: number;
  response: unknown;
}
export class BuildAnchorCLIError extends BuildAnchorClientError {
  exitCode: number;
  response: unknown;
  stderr: string;
}

export class BuildAnchorClient {
  constructor(options?: BuildAnchorClientOptions);
  llmPrompt(objective?: string): Promise<BuildAnchorResponse>;
  tokenEstimate(): Promise<BuildAnchorResponse>;
  inspect(options?: { freshness?: "cached" | "refresh" }): Promise<BuildAnchorResponse>;
  context(options?: { tokenBudget?: number }): Promise<BuildAnchorResponse>;
  preflight(options?: { objective?: string; tokenBudget?: number }): Promise<BuildAnchorResponse>;
  plan(objective: string, options?: { tokenBudget?: number }): Promise<BuildAnchorResponse>;
  changeImpact(options?: { baseline?: string; staged?: boolean }): Promise<BuildAnchorResponse>;
  validateChange(options?: { baseline?: string; execute?: boolean; timeoutSeconds?: number; staged?: boolean }): Promise<BuildAnchorResponse>;
  repairGuidance(options?: { baseline?: string; staged?: boolean }): Promise<BuildAnchorResponse>;
  compatibility(): Promise<BuildAnchorResponse>;
  explainDependency(dependency: string): Promise<BuildAnchorResponse>;
  findPackage(packageName: string, options?: { showUsage?: boolean; installedOnly?: boolean }): Promise<BuildAnchorResponse>;
  modules(): Promise<BuildAnchorResponse>;
  resolveCommand(phase?: string, options?: { scope?: string; changed?: boolean }): Promise<BuildAnchorResponse>;
}
