import { useEffect, useState } from "react";
import { getReady, type Ready } from "../api/client";

export function HealthPage() {
  const [ready, setReady] = useState<Ready | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getReady().then(setReady, (e: unknown) => setError(String(e)));
  }, []);

  return (
    <main style={{ maxWidth: 640, margin: "0 auto", padding: 16 }}>
      <h1>Team Tracker</h1>
      <p data-testid="status">
        {error ? `API unreachable: ${error}` : ready ? `API ${ready.status}` : "checking…"}
      </p>
      {ready && (
        <ul>
          {Object.entries(ready.checks).map(([name, state]) => (
            <li key={name}>
              {name}: {state}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
