// Thin fetch wrappers around the strata state-service's read-only routes
// (ADR-0065). No auth headers are sent anywhere yet — this is a v0, read-only
// dashboard; the server currently allows unauthenticated reads of these routes
// to match (see src/strata/server/app.py's `resolve_read_scope`).

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8443";

export interface StatusResult {
    ok: boolean;
    detail?: string;
}

export interface TailEvent {
    execution_id: string;
    record_type: string;
    recorded_at: string | null;
    received_at: string | null;
    workspace: string | null;
    deployment: string | null;
    environment: string | null;
    action: string | null;
    outcome: string | null;
}

async function getJson<T>(path: string): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`);
    if (!response.ok) {
        throw new Error(`${path} -> HTTP ${response.status}`);
    }
    return response.json() as Promise<T>;
}

export async function getStatus(): Promise<StatusResult> {
    try {
        await getJson<{ status: string }>("/healthz");
        return { ok: true };
    } catch (err) {
        return { ok: false, detail: err instanceof Error ? err.message : String(err) };
    }
}

export async function getWorkspaces(): Promise<string[]> {
    const data = await getJson<{ workspaces: string[] }>("/v1/workspaces");
    return data.workspaces;
}

export async function getTail(limit = 100, workspace?: string): Promise<TailEvent[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (workspace) {
        params.set("workspace", workspace);
    }
    const data = await getJson<{ events: TailEvent[] }>(`/v1/events/tail?${params.toString()}`);
    return data.events;
}
