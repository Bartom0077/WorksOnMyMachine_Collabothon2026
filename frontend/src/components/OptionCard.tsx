import { creditLineHint, creditLineNote, formatDate, formatEur } from "../lib/format";
import type { RescueOption } from "../types/api";
import { Icon } from "./Icon";
import { StatusChip } from "./StatusChip";

interface Props {
  option: RescueOption;
  recommended: boolean;
  /** Why this option is the recommended one; shown on the featured card only. */
  rationale: string | null;
  /** The engine's one-line verdict on this option (code-written, not AI text). */
  comment: string | null;
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

/** Everything about an option that is not needed to decide at a glance, with the reject action. */
function OptionDetails({ option, comment, busy, disabled, onReject }: Pick<Props, "option" | "comment" | "busy" | "disabled" | "onReject">) {
  const creditNote = creditLineNote(option.credit_limit_eur, option.new_min_balance_eur);

  return (
    <details className="disclosure option__details">
      <summary>
        Details<span className="visually-hidden"> of option {option.id}</span>
      </summary>
      <div className="option__more">
        <p>{option.description}</p>
        <p>
          Lowest balance on {formatDate(option.new_min_balance_date)}.{creditNote !== null ? ` ${creditNote}` : ""}
        </p>
        {option.cost_note !== null ? <p>Cost: {option.cost_note}</p> : null}
        {option.non_financial_cost !== null ? <p>Other cost: {option.non_financial_cost}</p> : null}
        {comment !== null ? <p className="option__comment">{comment}</p> : null}
        {option.rejected ? null : (
          <button type="button" className="btn btn--small" onClick={onReject} disabled={disabled}>
            {busy ? "Saving…" : "Reject"}
            <span className="visually-hidden"> option {option.id}</span>
          </button>
        )}
      </div>
    </details>
  );
}

/** One rescue option: outcome, cost and approval at a glance; the rest and the reject action under "Details". */
export function OptionCard({
  option,
  recommended,
  rationale,
  comment,
  previewed,
  busy,
  disabled,
  onPreview,
  onApprove,
  onReject,
}: Props) {
  const titleId = `option-${option.id}-title`;
  const creditHint = creditLineHint(option.credit_limit_eur, option.new_min_balance_eur);
  const previewLabel = previewed ? "Shown on chart" : "Preview on chart";
  const classes = [
    "option",
    recommended ? "option--featured" : "",
    previewed ? "is-previewed" : "",
    option.rejected ? "is-rejected" : "",
  ];

  return (
    <li>
      <article className={classes.filter(Boolean).join(" ")} aria-labelledby={titleId}>
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

        {recommended && rationale !== null ? (
          <p className="option__reason">
            <strong>Why option {option.id}:</strong> {rationale}
          </p>
        ) : null}

        <dl className="option__facts">
          <div>
            <dt>New lowest balance</dt>
            <dd>
              <span className="option__amount">{formatEur(option.new_min_balance_eur)}</span>
              <StatusChip status={option.new_status} />
              {creditHint !== null ? <span className="option__hint">{creditHint}</span> : null}
            </dd>
          </div>
          <div>
            <dt>Cost</dt>
            <dd className="option__amount">{costText(option)}</dd>
          </div>
        </dl>

        <div className="option__actions">
          <button
            type="button"
            className="btn btn--ghost"
            aria-pressed={previewed}
            aria-label={`${previewLabel}: option ${option.id}`}
            onClick={onPreview}
          >
            <Icon name="eye" size={14} />
            {previewLabel}
          </button>
          <button
            type="button"
            className={recommended ? "btn btn--primary" : "btn"}
            onClick={onApprove}
            disabled={disabled}
          >
            Approve
            <span className="visually-hidden"> option {option.id}</span>
          </button>
        </div>

        <OptionDetails option={option} comment={comment} busy={busy} disabled={disabled} onReject={onReject} />
      </article>
    </li>
  );
}
