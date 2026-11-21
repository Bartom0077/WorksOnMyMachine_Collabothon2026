import { type AdviceCard, changesForecast } from "../lib/advice";
import { formatDate, formatDateRange, formatEur } from "../lib/format";
import type { AdviceProposal } from "../types/api";
import { Icon } from "./Icon";
import { StatusChip } from "./StatusChip";

interface Props {
  card: AdviceCard;
  selected: boolean;
  /** True while this proposal is the one the owner asked to see on the chart. */
  previewed: boolean;
  /** True while the proposal is part of the scenario on the chart, alone or with others. */
  shownOnChart: boolean;
  disabled: boolean;
  onToggle: () => void;
  onPreview: () => void;
}

/** "16 Oct – 16 Nov 2025", or the one date that is known; null when the proposal has no dates. */
function datesText({ date_from, date_to }: AdviceProposal): string | null {
  if (date_from !== null && date_to !== null) return formatDateRange(date_from, date_to);
  if (date_from !== null) return `From ${formatDate(date_from)}`;
  if (date_to !== null) return `Until ${formatDate(date_to)}`;
  return null;
}

/** One CFO recommendation: what it does, its figures and assumptions, and a checkbox to select it. */
export function ProposalCard({ card, selected, previewed, shownOnChart, disabled, onToggle, onPreview }: Props) {
  const { proposal } = card;
  const titleId = `proposal-${proposal.id}-title`;
  const checkboxId = `proposal-${proposal.id}-select`;
  const dates = datesText(proposal);
  const classes = ["option", "proposal", selected ? "is-selected" : "", shownOnChart ? "is-previewed" : ""];

  return (
    <li>
      <div className={classes.filter(Boolean).join(" ")} role="group" aria-labelledby={titleId}>
        <div className="option__head">
          <input
            id={checkboxId}
            type="checkbox"
            className="proposal__checkbox"
            checked={selected}
            disabled={disabled}
            onChange={onToggle}
          />
          <h4 id={titleId} className="option__title">
            <label htmlFor={checkboxId} className="proposal__label">
              {card.title}
            </label>
          </h4>
          {card.recommended ? (
            <span className="badge badge--recommended">
              <Icon name="star" size={14} />
              Recommended
            </span>
          ) : null}
        </div>

        <p className="option__description">{card.explanation}</p>

        <dl className="option__facts">
          {proposal.amount_eur !== null ? (
            <div>
              <dt>Amount</dt>
              <dd className="option__amount">{formatEur(proposal.amount_eur)}</dd>
            </div>
          ) : null}
          {dates !== null ? (
            <div>
              <dt>Dates</dt>
              <dd>{dates}</dd>
            </div>
          ) : null}
          <div>
            <dt>Effect</dt>
            <dd>{proposal.effect}</dd>
          </div>
          {proposal.interest_eur !== null ? (
            <div>
              <dt>Interest earned</dt>
              <dd className="option__amount">{formatEur(proposal.interest_eur)}</dd>
            </div>
          ) : null}
          {proposal.risk_removed_eur !== null ? (
            <div>
              <dt>Risk removed</dt>
              <dd className="option__amount">{formatEur(proposal.risk_removed_eur)}</dd>
            </div>
          ) : null}
          {changesForecast(proposal) ? (
            <>
              <div>
                <dt>New lowest balance</dt>
                <dd>
                  <span className="option__amount">{formatEur(proposal.new_min_balance_eur)}</span>
                  <span className="muted"> on {formatDate(proposal.new_min_balance_date)}</span>
                </dd>
              </div>
              <div>
                <dt>Result</dt>
                <dd>
                  <StatusChip status={proposal.new_status} />
                </dd>
              </div>
            </>
          ) : null}
        </dl>

        {proposal.assumptions.length > 0 ? (
          <div className="proposal__assumptions">
            <p className="proposal__assumptions-label" id={`proposal-${proposal.id}-assumptions`}>
              Assumptions
            </p>
            <ul aria-labelledby={`proposal-${proposal.id}-assumptions`}>
              {proposal.assumptions.map((assumption) => (
                <li key={assumption}>{assumption}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {changesForecast(proposal) ? (
          <div className="option__actions">
            <button type="button" className="btn btn--ghost" aria-pressed={previewed} onClick={onPreview}>
              <Icon name="eye" size={14} />
              {previewed ? "Shown on chart" : "Preview on chart"}
              <span className="visually-hidden">: {card.title}</span>
            </button>
          </div>
        ) : null}
      </div>
    </li>
  );
}
