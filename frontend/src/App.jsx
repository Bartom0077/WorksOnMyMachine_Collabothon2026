import { useCallback, useEffect, useState } from "react";

export default function App() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/health");
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.error || "HTTP " + response.status);
      }
      setHealth(body);
    } catch (err) {
      setHealth(null);
      setError(err.message || "Nie udało się połączyć z backendem");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="page">
      <div className="stripe" />
      <header className="top">
        <div className="mark" aria-hidden="true" />
        <div className="wordmark">Commerzbank</div>
      </header>
      <main>
        <h1>Autonomous - CFO</h1>
        <div className="accent" />
        <section className="card">
          <div className="card-head">
            <h2>Backend health</h2>
            <button type="button" onClick={load} disabled={loading}>
              {loading ? "Sprawdzam..." : "Odśwież"}
            </button>
          </div>
          {error ? <p className="error">{error}</p> : null}
          {health ? (
            <dl>
              <div>
                <dt>Serwis</dt>
                <dd>{health.service}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{health.status}</dd>
              </div>
              <div>
                <dt>Wiadomość</dt>
                <dd>{health.message}</dd>
              </div>
              <div>
                <dt>Czas z backendu</dt>
                <dd>{health.time}</dd>
              </div>
            </dl>
          ) : null}
        </section>
      </main>
    </div>
  );
}
