import { useEffect, useState } from "react";
import { getTail, type TailEvent } from "../api";

const POLL_MS = 5000;

export default function TailPanel() {
    const [events, setEvents] = useState<TailEvent[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [autoRefresh, setAutoRefresh] = useState(true);

    async function refresh() {
        try {
            const result = await getTail(100);
            setEvents(result);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    }

    useEffect(() => {
        refresh();
        if (!autoRefresh) {
            return;
        }
        const interval = setInterval(refresh, POLL_MS);
        return () => clearInterval(interval);
    }, [autoRefresh]);

    return (
        <section className="panel panel-wide">
            <div className="panel-header">
                <h2>Tail</h2>
                <label>
                    <input
                        type="checkbox"
                        checked={autoRefresh}
                        onChange={(e) => setAutoRefresh(e.target.checked)}
                    />{" "}
                    Auto-refresh
                </label>
                <button onClick={refresh}>Refresh now</button>
            </div>
            {error && <p className="error">{error}</p>}
            {events.length === 0 && !error ? (
                <p className="muted">No events yet.</p>
            ) : (
                <table>
                    <thead>
                        <tr>
                            <th>Received</th>
                            <th>Workspace</th>
                            <th>Deployment</th>
                            <th>Environment</th>
                            <th>Record type</th>
                            <th>Action</th>
                            <th>Outcome</th>
                        </tr>
                    </thead>
                    <tbody>
                        {events.map((event) => (
                            <tr key={`${event.execution_id}-${event.record_type}`}>
                                <td>{event.received_at ?? "-"}</td>
                                <td>{event.workspace ?? "-"}</td>
                                <td>{event.deployment ?? "-"}</td>
                                <td>{event.environment ?? "-"}</td>
                                <td>{event.record_type}</td>
                                <td>{event.action ?? "-"}</td>
                                <td>{event.outcome ?? "-"}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
        </section>
    );
}
