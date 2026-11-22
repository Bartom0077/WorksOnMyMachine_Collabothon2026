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

/** Marks a summary the AI wrote; the standard (fallback) text carries no badge. */
function AiBadge() {
  return (
    <span className="badge badge--ai">
      <Icon name="sparkles" size={14} />
      AI summary
    </span>
  );
}

/** The opening sentence of a text; a full stop inside an abbreviation ("p.a. on") does not end it. */
function firstSentence(text: string): string {
  return /^[\s\S]*?[.!?](?=\s+\p{Lu}|\s*$)/u.exec(text)?.[0] ?? text;
}

/**
 * The coordinator's "what and why now": its headline and opening sentence, the rest under "Read more".
 * The cards below stay usable while it loads or when it fails.
 */
function Summary({ recommendation }: Pick<Props, "recommendation">) {
  const { data, error, loading, reload } = recommendation;
  const lead = data !== null ? firstSentence(data.summary.text) : "";
  const rest = data !== null ? data.summary.text.slice(lead.length).trim() : "";
  return (
    <div className="advice__summary" aria-live="polite">
      {loading ? <Skeleton label="Loading the summary of the recommendations" lines={2} /> : null}
      {error ? <ErrorNotice title="The summary could not be loaded" error={error} onRetry={reload} /> : null}
      {data ? (
        <>
          <p className="advice__headline">
            {data.summary.headline} {data.summary.source === "llm" ? <AiBadge /> : null}
          </p>
          <p>{lead}</p>
          {rest !== "" ? (
            <details className="disclosure">
              <summary>
                Read more<span className="visually-hidden"> of the summary</span>
              </summary>
              <p className="muted">{rest}</p>
            </details>
          ) : null}
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
  const sectionRef = useRef<HTMLElement>(null);
  const { settledSelections } = flow;
  const loaded = advice.data !== null;
  const hasProposals = cards.length > 0;
  const selectedIds = flow.selected.map((card) => card.proposal.id);
  const nothingToDecide = selectedIds.length === 0 || !flow.canDecide;

  // A settled selection disables the button that held the focus, which would drop the keyboard position to
  // the top of the page: move it to this section, whose focus outline is visible (its heading is not). The
  // confirmation is a child of this section, so React has already closed it (and lifted its modal focus
  // trap) when this effect runs.
  // The panel is mounted anew each time its tab is opened: only a selection settled since then moves the focus.
  const focusedForSettled = useRef(settledSelections);
  useEffect(() => {
    if (settledSelections === focusedForSettled.current) return;
    focusedForSettled.current = settledSelections;
    sectionRef.current?.focus();
  }, [settledSelections]);

  return (
    <section className="advice" aria-labelledby="advice-title" ref={sectionRef} tabIndex={-1}>
      <h2 id="advice-title" className="visually-hidden">
        CFO recommendations
      </h2>

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
              ? "Tick what you want to decide on."
              : `${selectedIds.length} of ${cards.length} selected.`}
          </p>
          <span className="advice__buttons">
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
