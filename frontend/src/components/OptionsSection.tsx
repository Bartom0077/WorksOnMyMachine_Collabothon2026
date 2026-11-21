import type { DecisionFlow } from "../hooks/useDecisionFlow";
import { formatEur } from "../lib/format";
import type { Dashboard, Recommendation, RescueOption } from "../types/api";
import { ErrorNotice } from "./Feedback";
import { Icon } from "./Icon";
import { OptionCard } from "./OptionCard";

interface Props {
  dashboard: Dashboard;
  recommendation: Recommendation | null;
  recommendedOptionId: string | null;
  previewOptionId: string | null;
  decisions: DecisionFlow;
  /** Selects the option drawn on the chart; null returns to the recommended one. */
  onPreview: (optionId: string | null) => void;
}

function NoActionNeeded({ dashboard }: { dashboard: Dashboard }) {
  return (
    <div className="calm">
      <p className="calm__line">
        <Icon name="check" size={16} />
        <span>
          <strong>No action needed</strong>{" "}
          {dashboard.risk.credit_limit_eur !== null
            ? `Within your credit line and safety buffer for the next ${dashboard.horizon_days} days.`
            : `Above your safety buffer for the next ${dashboard.horizon_days} days.`}
        </span>
      </p>
      {dashboard.insights.length > 0 ? (
        <ul className="insights" aria-label="Opportunities">
          {dashboard.insights.map((insight) => (
            <li key={insight.title} className="calm__line">
              <Icon name="star" size={16} />
              <span>
                <strong>
                  {insight.title}: {formatEur(insight.amount_eur)}
                </strong>{" "}
                {insight.detail}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** The options with the recommended one moved to the front; the others keep the backend's order. */
function recommendedFirst(options: RescueOption[], recommendedOptionId: string | null): RescueOption[] {
  return [
    ...options.filter((option) => option.id === recommendedOptionId),
    ...options.filter((option) => option.id !== recommendedOptionId),
  ];
}

/** Rescue options A–E computed by the backend, recommended first, or a one-line all-clear when nothing needs doing. */
export function OptionsSection({
  dashboard,
  recommendation,
  recommendedOptionId,
  previewOptionId,
  decisions,
  onPreview,
}: Props) {
  const hasOptions = dashboard.options.length > 0;
  const matchesRecommendation =
    recommendation !== null && recommendation.recommended_option_id === recommendedOptionId;
  const rationale = matchesRecommendation && recommendation.rationale !== "" ? recommendation.rationale : null;

  return (
    <section className="options-panel" aria-labelledby="options-title">
      <h2 id="options-title" className="visually-hidden">
        {hasOptions ? "Your options" : "Next steps"}
      </h2>

      {decisions.error !== null && decisions.awaitingApproval === null ? (
        <ErrorNotice
          title="Your decision was not saved"
          error={decisions.error}
          onDismiss={decisions.dismissError}
        />
      ) : null}

      {hasOptions ? (
        <ul className="options">
          {recommendedFirst(dashboard.options, recommendedOptionId).map((option) => (
            <OptionCard
              key={option.id}
              option={option}
              recommended={option.id === recommendedOptionId}
              rationale={rationale}
              comment={recommendation?.option_comments[option.id] ?? null}
              previewed={option.id === previewOptionId}
              busy={decisions.busyOptionId === option.id}
              disabled={!decisions.canDecide}
              onPreview={() => onPreview(option.id)}
              onApprove={() => decisions.requestApproval(option)}
              onReject={() => {
                // A rejected option should not stay on the chart just because it was being previewed.
                if (option.id === previewOptionId) onPreview(null);
                decisions.reject(option);
              }}
            />
          ))}
        </ul>
      ) : (
        <NoActionNeeded dashboard={dashboard} />
      )}
    </section>
  );
}
