import { useEffect, useState } from "react";
import { getWorkspaces } from "../api";

const POLL_MS = 5000;

export default function WorkspacesPanel() {
    const [workspaces, setWorkspaces] = useState<string[]>([]);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;

        async function refresh() {
            try {
                const result = await getWorkspaces();
                if (!cancelled) {
                    setWorkspaces(result);
                    setError(null);
                }
            } catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err.message : String(err));
                }
            }
        }

        refresh();
        const interval = setInterval(refresh, POLL_MS);
        return () => {
            cancelled = true;
            clearInterval(interval);
        };
    }, []);

    return (
        <section className="panel">
            <h2>Workspaces</h2>
            {error && <p className="error">{error}</p>}
            {workspaces.length === 0 && !error ? (
                <p className="muted">No workspaces seen yet.</p>
            ) : (
                <ul>
                    {workspaces.map((name) => (
                        <li key={name}>{name}</li>
                    ))}
                </ul>
            )}
        </section>
    );
}
