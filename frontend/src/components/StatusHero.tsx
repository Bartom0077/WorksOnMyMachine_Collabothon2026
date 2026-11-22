import type { AsyncResource } from "../hooks/useAsyncResource";
import { STATUS_LABELS, formatDate, formatDateRange, formatDaysFromNow, formatEur } from "../lib/format";
import type { Dashboard, Recommendation, Risk, RiskDriver, RiskStatus } from "../types/api";
import { ErrorNotice, Skeleton } from "./Feedback";
import { Icon } from "./Icon";
import { STATUS_ICONS } from "./StatusChip";

const LAMPS: RiskStatus[] = ["RED", "YELLOW", "GREEN"];

interface Props {
  dashboard: Dashboard;
  recommendation: AsyncResource<Recommendation>;
}

function TrafficLight({ status }: { status: RiskStatus }) {
  return (
    <div className="traffic" aria-hidden="true">
      {LAMPS.map((lamp) => (
        <span
          key={lamp}
          className={`traffic__lamp traffic__lamp--${lamp.toLowerCase()}${lamp === status ? " is-lit" : ""}`}
        />
      ))}
    </div>
  );
}

/** Marks an explanation the AI wrote; the standard (fallback) text carries no badge. */
function AiBadge() {
  return (
    <span className="badge badge--ai">
      <Icon name="sparkles" size={14} />
      AI explanation
    </span>
  );
}

/** The assistant's plain-language explanation, or its loading / error state. */
function Explanation({ recommendation }: { recommendation: AsyncResource<Recommendation> }) {
  const { data, error, loading, reload } = recommendation;
  return (
    <div className="hero__explanation" aria-live="polite">
      {loading ? (
        <>
          <p className="analysing__label">
            <Icon name="sparkles" />
            The assistant is analysing your forecast…
          </p>
          <Skeleton label="Loading the explanation" lines={1} />
        </>
      ) : null}
      {error ? <ErrorNotice title="The explanation could not be loaded" error={error} onRetry={reload} /> : null}
      {data ? <p className="explanation">{data.explanation}</p> : null}
    </div>
  );
}

interface RiskDriversProps {
  riskDrivers: RiskDriver[];
  riskWindow: Risk["risk_window"];
}

function RiskDriversTable({ riskDrivers, riskWindow }: RiskDriversProps) {
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

/** Traffic light, headline, the assistant's explanation with its "Why?" breakdown, and the key figures. */
export function StatusHero({ dashboard, recommendation }: Props) {
  const { risk } = dashboard;
  const tone = risk.status.toLowerCase();
  const explanation = recommendation.data;

  return (
    <section className={`hero hero--${tone}`} aria-labelledby="hero-headline">
      <div className="hero__main">
        <TrafficLight status={risk.status} />
        <div className="hero__text">
          <div aria-live="polite">
            <p className={`hero__status hero__status--${tone}`}>
              <Icon name={STATUS_ICONS[risk.status]} size={18} />
              <span className="visually-hidden">Status: </span>
              {STATUS_LABELS[risk.status]}
            </p>
            <h2 id="hero-headline" className="hero__headline">
              {dashboard.headline}
            </h2>
          </div>
          <Explanation recommendation={recommendation} />
          {explanation !== null && explanation.source === "llm" ? (
            <p className="hero__provenance">
              <AiBadge />
            </p>
          ) : null}
          <details className="disclosure">
            <summary>Why?</summary>
            <RiskDriversTable riskDrivers={dashboard.risk_drivers} riskWindow={risk.risk_window} />
          </details>
        </div>
      </div>

      <dl className="figures">
        <div className="figure">
          <dt>Balance today</dt>
          <dd>{formatEur(dashboard.current_balance_eur)}</dd>
          <dd className="figure__note">{formatDate(dashboard.as_of_date)}</dd>
        </div>
        <div className="figure">
          <dt>Lowest projected balance</dt>
          <dd className={risk.min_balance_eur < risk.floor_eur ? "is-negative" : undefined}>
            {formatEur(risk.min_balance_eur)}
          </dd>
          <dd className="figure__note">on {formatDate(risk.min_balance_date)}</dd>
        </div>
        <div className="figure">
          <dt>Low point</dt>
          <dd>{formatDaysFromNow(risk.days_until_min)}</dd>
          <dd className="figure__note">next {dashboard.horizon_days} days</dd>
        </div>
        {risk.credit_limit_eur !== null ? (
          <div className="figure">
            <dt>Credit line</dt>
            <dd>{formatEur(risk.credit_limit_eur)}</dd>
            <dd className="figure__note">agreed limit</dd>
          </div>
        ) : null}
      </dl>
    </section>
  );
}
