import { STATUS_LABELS, formatDate, formatDaysFromNow, formatEur } from "../lib/format";
import type { Dashboard, RiskStatus } from "../types/api";
import { Icon } from "./Icon";
import { STATUS_ICONS } from "./StatusChip";

const LAMPS: RiskStatus[] = ["RED", "YELLOW", "GREEN"];

interface Props {
  dashboard: Dashboard;
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

/** Traffic light, headline and the handful of figures the owner needs first. */
export function StatusHero({ dashboard }: Props) {
  const { risk } = dashboard;
  const tone = risk.status.toLowerCase();

  return (
    <section className={`hero hero--${tone}`} aria-labelledby="hero-headline">
      <div className="hero__main">
        <TrafficLight status={risk.status} />
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
          <dd className="figure__note">within the next {dashboard.horizon_days} days</dd>
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
