import { useEffect, useState } from "react";
import { getStatus, type StatusResult } from "../api";

const POLL_MS = 5000;

export default function StatusPanel() {
    const [status, setStatus] = useState<StatusResult | null>(null);
    const [lastChecked, setLastChecked] = useState<Date | null>(null);

    useEffect(() => {
        let cancelled = false;

        async function refresh() {
            const result = await getStatus();
            if (!cancelled) {
                setStatus(result);
                setLastChecked(new Date());
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
            <h2>Status</h2>
            {status === null ? (
                <p>Checking...</p>
            ) : (
                <p>
                    <span className={`dot ${status.ok ? "dot-ok" : "dot-down"}`} />{" "}
                    {status.ok ? "Healthy" : `Down (${status.detail})`}
                </p>
            )}
            {lastChecked && <p className="muted">Last checked: {lastChecked.toLocaleTimeString()}</p>}
        </section>
    );
}
