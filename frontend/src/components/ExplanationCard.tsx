import { useId, useState } from "react";
import type { AsyncResource } from "../hooks/useAsyncResource";
import { formatDateRange, formatEur } from "../lib/format";
import type { Recommendation, Risk, RiskDriver } from "../types/api";
import { ErrorNotice, Skeleton } from "./Feedback";
import { Icon } from "./Icon";

interface Props {
  recommendation: AsyncResource<Recommendation>;
  riskDrivers: RiskDriver[];
  riskWindow: Risk["risk_window"];
}

function ProvenanceBadge({ recommendation }: { recommendation: Recommendation }) {
  if (recommendation.source === "llm") {
    return (
      <span className="badge badge--ai">
        <Icon name="sparkles" size={14} />
        AI explanation
      </span>
    );
  }
  return (
    <span className="badge badge--fallback" title={recommendation.fallback_reason ?? undefined}>
      <Icon name="database" size={14} />
      Standard explanation (AI unavailable)
    </span>
  );
}

function RiskDriversTable({ riskDrivers, riskWindow }: Pick<Props, "riskDrivers" | "riskWindow">) {
  if (riskWindow === null || riskDrivers.length === 0) {
    return <p className="empty">No fixed costs fall inside a risk window for this forecast.</p>;
  }
  return (
    <>
      <p className="muted">
        Fixed costs due while your balance is under pressure ({formatDateRange(riskWindow.start, riskWindow.end)}),
        largest first.
      </p>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Cost</th>
              <th scope="col" className="num">
                Total
              </th>
              <th scope="col">Can it wait?</th>
            </tr>
          </thead>
          <tbody>
            {riskDrivers.map((driver) => (
              <tr key={driver.category}>
                <th scope="row">{driver.label}</th>
                <td className="num">{formatEur(driver.total_eur)}</td>
                <td>{driver.deferrable ? "Can be postponed" : "Must be paid"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** The assistant's plain-language explanation with provenance, plus the "Why?" breakdown. */
export function ExplanationCard({ recommendation, riskDrivers, riskWindow }: Props) {
  const [showWhy, setShowWhy] = useState(false);
  const whyId = useId();
  const { data, error, loading, reload } = recommendation;

  return (
    <section className="card" aria-labelledby="explanation-title">
      <div className="card__head">
        <h2 id="explanation-title" className="card__title">
          What is going on
        </h2>
        {data ? <ProvenanceBadge recommendation={data} /> : null}
      </div>

      <div aria-live="polite">
        {loading ? (
          <div className="analysing">
            <p className="analysing__label">
              <Icon name="sparkles" />
              The assistant is analysing your forecast…
            </p>
            <Skeleton label="Loading the explanation" lines={3} />
          </div>
        ) : null}
        {error ? <ErrorNotice title="The explanation could not be loaded" error={error} onRetry={reload} /> : null}
        {data ? (
          <>
            <p className="explanation">{data.explanation}</p>
            {data.source === "fallback" && data.fallback_reason !== null ? (
              <p className="muted">Why no AI text: {data.fallback_reason}</p>
            ) : null}
          </>
        ) : null}
      </div>

      <div className="card__footer">
        <button
          type="button"
          className="btn"
          aria-expanded={showWhy}
          aria-controls={whyId}
          onClick={() => setShowWhy((current) => !current)}
        >
          {showWhy ? "Hide details" : "Why?"}
        </button>
        <p className="trust-note">
          <Icon name="database" size={14} />
          Every figure is calculated by deterministic code from your bank transactions. The AI only explains the
          numbers and suggests an option. Nothing happens without your approval.
        </p>
      </div>

      <div id={whyId} hidden={!showWhy} className="why">
        <RiskDriversTable riskDrivers={riskDrivers} riskWindow={riskWindow} />
      </div>
    </section>
  );
}
