import { formatDate, formatEur } from "../lib/format";
import type { RescueOption } from "../types/api";
import { Icon } from "./Icon";
import { StatusChip } from "./StatusChip";

interface Props {
  option: RescueOption;
  recommended: boolean;
  aiComment: string | null;
  previewed: boolean;
  busy: boolean;
  disabled: boolean;
  onPreview: () => void;
  onApprove: () => void;
  onReject: () => void;
}

function costText(option: RescueOption): string {
  return option.cost_eur === 0 ? "No cost" : formatEur(option.cost_eur);
}

/** One rescue option: outcome, cost and the approve / reject actions. */
export function OptionCard({
  option,
  recommended,
  aiComment,
  previewed,
  busy,
  disabled,
  onPreview,
  onApprove,
  onReject,
}: Props) {
  const titleId = `option-${option.id}-title`;
  const classes = ["option", previewed ? "is-previewed" : "", option.rejected ? "is-rejected" : ""];

  return (
    <li>
      <article className={classes.filter(Boolean).join(" ")} aria-labelledby={titleId} onFocus={onPreview}>
        <header className="option__head">
          <span className="option__letter" aria-hidden="true">
            {option.id}
          </span>
          <h3 id={titleId} className="option__title">
            <span className="visually-hidden">Option {option.id}: </span>
            {option.title}
          </h3>
          {recommended ? (
            <span className="badge badge--recommended">
              <Icon name="star" size={14} />
              Recommended
            </span>
          ) : null}
          {option.rejected ? <span className="badge badge--rejected">Rejected</span> : null}
        </header>

        <p className="option__description">{option.description}</p>

        <dl className="option__facts">
          <div>
            <dt>New lowest balance</dt>
            <dd>
              <span className="option__amount">{formatEur(option.new_min_balance_eur)}</span>
              <span className="muted"> on {formatDate(option.new_min_balance_date)}</span>
            </dd>
          </div>
          <div>
            <dt>Result</dt>
            <dd>
              <StatusChip status={option.new_status} />
            </dd>
          </div>
          <div>
            <dt>Cost</dt>
            <dd>
              <span className="option__amount">{costText(option)}</span>
              {option.cost_note !== null ? <span className="muted"> · {option.cost_note}</span> : null}
            </dd>
          </div>
          {option.non_financial_cost !== null ? (
            <div>
              <dt>Other cost</dt>
              <dd>{option.non_financial_cost}</dd>
            </div>
          ) : null}
        </dl>

        {aiComment !== null ? (
          <p className="option__comment">
            <Icon name="sparkles" size={14} />
            <span>
              <span className="visually-hidden">AI comment: </span>
              {aiComment}
            </span>
          </p>
        ) : null}

        <div className="option__actions">
          <button type="button" className="btn btn--ghost" aria-pressed={previewed} onClick={onPreview}>
            <Icon name="eye" size={14} />
            {previewed ? "Shown on chart" : "Preview on chart"}
          </button>
          <span className="option__decide">
            {option.rejected ? null : (
              <button type="button" className="btn" onClick={onReject} disabled={disabled}>
                {busy ? "Saving…" : "Reject"}
                <span className="visually-hidden"> option {option.id}</span>
              </button>
            )}
            <button type="button" className="btn btn--primary" onClick={onApprove} disabled={disabled}>
              Approve
              <span className="visually-hidden"> option {option.id}</span>
            </button>
          </span>
        </div>
      </article>
    </li>
  );
}
