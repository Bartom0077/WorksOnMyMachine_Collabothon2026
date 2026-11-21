import type { DecisionFlow } from "../hooks/useDecisionFlow";
import { formatEur } from "../lib/format";
import type { Dashboard, Recommendation } from "../types/api";
import { ErrorNotice } from "./Feedback";
import { Icon } from "./Icon";
import { OptionCard } from "./OptionCard";

interface Props {
  dashboard: Dashboard;
  recommendation: Recommendation | null;
  recommendedOptionId: string | null;
  previewOptionId: string | null;
  decisions: DecisionFlow;
  onPreview: (optionId: string) => void;
}

function NoActionNeeded({ dashboard }: { dashboard: Dashboard }) {
  return (
    <>
      <div className="calm">
        <span className="calm__icon" aria-hidden="true">
          <Icon name="check" size={20} />
        </span>
        <div>
          <p className="calm__title">No action needed</p>
          <p className="muted">
            {dashboard.risk.credit_limit_eur !== null
              ? `Your projected balance stays within your credit line, with your safety buffer to spare, for the next ${dashboard.horizon_days} days.`
              : `Your projected balance stays above your safety buffer for the next ${dashboard.horizon_days} days.`}
          </p>
        </div>
      </div>
      {dashboard.insights.length > 0 ? (
        <ul className="insights" aria-label="Opportunities">
          {dashboard.insights.map((insight) => (
            <li key={insight.title} className="insight">
              <p className="insight__title">{insight.title}</p>
              <p className="insight__amount">{formatEur(insight.amount_eur)}</p>
              <p className="muted">{insight.detail}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

/** Rescue options A–E computed by the backend, or a calm state when nothing needs doing. */
export function OptionsSection({
  dashboard,
  recommendation,
  recommendedOptionId,
  previewOptionId,
  decisions,
  onPreview,
}: Props) {
  const hasOptions = dashboard.options.length > 0;
  const rationale = recommendation !== null && recommendation.recommended_option_id === recommendedOptionId
    ? recommendation.rationale
    : "";

  return (
    <section className="card" aria-labelledby="options-title">
      <div className="card__head">
        <h2 id="options-title" className="card__title">
          {hasOptions ? "Your options" : "Next steps"}
        </h2>
        {hasOptions ? <p className="muted">Select an option to preview it on the chart.</p> : null}
      </div>

      {decisions.error !== null && decisions.awaitingApproval === null ? (
        <ErrorNotice
          title="Your decision was not saved"
          error={decisions.error}
          onDismiss={decisions.dismissError}
        />
      ) : null}

      {hasOptions && recommendedOptionId !== null && rationale !== "" ? (
        <p className="rationale">
          <Icon name="star" />
          <span>
            <strong>Why option {recommendedOptionId}:</strong> {rationale}
          </span>
        </p>
      ) : null}

      {hasOptions ? (
        <ul className="options">
          {dashboard.options.map((option) => (
            <OptionCard
              key={option.id}
              option={option}
              recommended={option.id === recommendedOptionId}
              aiComment={recommendation?.option_comments[option.id] ?? null}
              previewed={option.id === previewOptionId}
              busy={decisions.busyOptionId === option.id}
              disabled={decisions.busyOptionId !== null}
              onPreview={() => onPreview(option.id)}
              onApprove={() => decisions.requestApproval(option)}
              onReject={() => decisions.reject(option)}
            />
          ))}
        </ul>
      ) : (
        <NoActionNeeded dashboard={dashboard} />
      )}
    </section>
  );
}
