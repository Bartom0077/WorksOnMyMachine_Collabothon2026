import { useEffect, useRef } from "react";
import type { AdviceFlow } from "../hooks/useAdviceFlow";
import type { AsyncResource } from "../hooks/useAsyncResource";
import type { ChartPreviewState } from "../hooks/useChartPreview";
import { ADVICE_GROUPS, type AdviceCard } from "../lib/advice";
import type { AdviceAgent, AdviceRecommendation, AdviceResponse, AdviceTip, BalancePoint } from "../types/api";
import { AdviceApprovalDialog } from "./AdviceApprovalDialog";
import { EmptyState, ErrorNotice, Skeleton } from "./Feedback";
import { Icon } from "./Icon";
import { ProposalCard } from "./ProposalCard";

interface Props {
  advice: AsyncResource<AdviceResponse>;
  recommendation: AsyncResource<AdviceRecommendation>;
  /** The proposals on offer in the wording shown to the owner. */
  cards: AdviceCard[];
  flow: AdviceFlow;
  chart: ChartPreviewState;
  /** The forecast on screen, on top of which the confirmation combines the selected proposals. */
  baseline: BalancePoint[];
}

function ProvenanceBadge({ summary }: { summary: AdviceRecommendation["summary"] }) {
  if (summary.source === "llm") {
    return (
      <span className="badge badge--ai">
        <Icon name="sparkles" size={14} />
        AI summary
      </span>
    );
  }
  return (
    <span className="badge badge--fallback" title={summary.fallback_reason ?? undefined}>
      <Icon name="database" size={14} />
      Standard summary (AI unavailable)
    </span>
  );
}

/** The coordinator's "what and why now"; the cards below stay usable while it loads or when it fails. */
function Summary({ recommendation }: Pick<Props, "recommendation">) {
  const { data, error, loading, reload } = recommendation;
  return (
    <div className="advice__summary" aria-live="polite">
      {loading ? <Skeleton label="Loading the summary of the recommendations" lines={2} /> : null}
      {error ? <ErrorNotice title="The summary could not be loaded" error={error} onRetry={reload} /> : null}
      {data ? (
        <>
          <p className="advice__headline">{data.summary.headline}</p>
          <p className="advice-summary__text">{data.summary.text}</p>
        </>
      ) : null}
    </div>
  );
}

function Tips({ tips }: { tips: AdviceTip[] }) {
  return (
    <ul className="tips" aria-label="Tips">
      {tips.map((tip) => (
        <li key={tip.title} className="tip">
          <span className="tip__label">Tip</span>
          <p>
            <strong>{tip.title}</strong> {tip.text}
          </p>
        </li>
      ))}
    </ul>
  );
}

function tipsOf(recommendation: AdviceRecommendation | null, agent: AdviceAgent): AdviceTip[] {
  return recommendation?.agents.find((entry) => entry.agent === agent)?.tips ?? [];
}

/** CFO recommendations of the tax, currency and investment advisors: select any of them, then decide together. */
export function AdviceSection({ advice, recommendation, cards, flow, chart, baseline }: Props) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const { settledSelections } = flow;
  const loaded = advice.data !== null;
  const hasProposals = cards.length > 0;
  const selectedIds = flow.selected.map((card) => card.proposal.id);
  const nothingToDecide = selectedIds.length === 0 || !flow.canDecide;

  // A settled selection disables the button that held the focus, which would drop the keyboard position to
  // the top of the page: keep it in this section. The confirmation is a child of this section, so React has
  // already closed it (and lifted its modal focus trap) when this effect runs.
  useEffect(() => {
    if (settledSelections > 0) headingRef.current?.focus();
  }, [settledSelections]);

  return (
    <section className="card" aria-labelledby="advice-title">
      <div className="card__head">
        <h2 id="advice-title" className="card__title" ref={headingRef} tabIndex={-1}>
          CFO recommendations
        </h2>
        {hasProposals && recommendation.data ? <ProvenanceBadge summary={recommendation.data.summary} /> : null}
      </div>

      {flow.error !== null && !flow.awaitingApproval ? (
        <ErrorNotice title="Your decision was not saved" error={flow.error} onDismiss={flow.dismissError} />
      ) : null}
      {advice.error ? (
        <ErrorNotice title="The recommendations could not be loaded" error={advice.error} onRetry={advice.reload} />
      ) : null}
      {!loaded && advice.loading ? <Skeleton label="Loading the CFO recommendations" lines={3} /> : null}
      {loaded && !hasProposals ? <EmptyState>No further recommendations right now.</EmptyState> : null}
      {hasProposals ? <Summary recommendation={recommendation} /> : null}

      {ADVICE_GROUPS.map(({ agent, heading }) => {
        const groupCards = cards.filter((card) => card.proposal.agent === agent);
        const tips = tipsOf(recommendation.data, agent);
        if (groupCards.length === 0 && tips.length === 0) return null;
        const headingId = `advice-group-${agent}`;
        return (
          <div key={agent} className="advice-group" role="group" aria-labelledby={headingId}>
            <h3 id={headingId} className="advice-group__title">
              {heading}
            </h3>
            {groupCards.length > 0 ? (
              <ul className="options">
                {groupCards.map((card) => (
                  <ProposalCard
                    key={card.proposal.id}
                    card={card}
                    selected={selectedIds.includes(card.proposal.id)}
                    previewed={chart.proposalId === card.proposal.id}
                    shownOnChart={chart.shownProposalIds.includes(card.proposal.id)}
                    disabled={flow.busyStatus !== null}
                    onToggle={() => {
                      flow.toggle(card.proposal.id);
                      chart.previewSelection();
                    }}
                    onPreview={() => chart.previewProposal(card.proposal.id)}
                  />
                ))}
              </ul>
            ) : null}
            {tips.length > 0 ? <Tips tips={tips} /> : null}
          </div>
        );
      })}

      {hasProposals ? (
        <div className="advice__actions">
          <p className="muted" role="status">
            {selectedIds.length === 0
              ? "Tick the recommendations you want to decide on. Any combination works together."
              : `${selectedIds.length} of ${cards.length} selected.`}
          </p>
          <span className="option__decide">
            <button type="button" className="btn" onClick={flow.rejectSelected} disabled={nothingToDecide}>
              {flow.busyStatus === "rejected" ? "Saving…" : "Reject selected"}
            </button>
            <button type="button" className="btn btn--primary" onClick={flow.requestApproval} disabled={nothingToDecide}>
              Approve selected
            </button>
          </span>
        </div>
      ) : null}

      <AdviceApprovalDialog flow={flow} baseline={baseline} />
    </section>
  );
}
