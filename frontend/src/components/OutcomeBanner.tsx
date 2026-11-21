import { Icon } from "./Icon";

interface Props {
  /** What was approved and the execution messages it produced; null when there is nothing to announce. */
  outcome: { title: string; messages: string[] } | null;
  onDismiss: () => void;
}

/** Announces what was (simulated to be) executed after an approval. The live region is always mounted. */
export function OutcomeBanner({ outcome, onDismiss }: Props) {
  return (
    <div role="status" aria-live="polite">
      {outcome !== null ? (
        <div className="notice notice--success">
          <Icon name="check" size={18} />
          <div className="notice__body">
            <p className="notice__title">
              {outcome.title}{" "}
              <span className="badge badge--simulated">SIMULATED</span>
            </p>
            <ul className="notice__list">
              {outcome.messages.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          </div>
          <button type="button" className="btn btn--ghost btn--icon" onClick={onDismiss} aria-label="Dismiss message">
            <Icon name="close" />
          </button>
        </div>
      ) : null}
    </div>
  );
}
