import { Icon } from "./Icon";

interface Props {
  /** What was done and the execution messages it produced; null when there is nothing to announce. */
  outcome: { title: string; messages: string[] } | null;
  /** Marks an execution that was only simulated (approvals); false for something that really happened. */
  simulated: boolean;
  onDismiss: () => void;
}

/** Announces what was done after an approval or a demo reset. The live region is always mounted. */
export function OutcomeBanner({ outcome, simulated, onDismiss }: Props) {
  return (
    <div role="status" aria-live="polite">
      {outcome !== null ? (
        <div className="notice notice--success">
          <Icon name="check" size={18} />
          <div className="notice__body">
            <p className="notice__title">
              {outcome.title}
              {simulated ? (
                <>
                  {" "}
                  <span className="badge badge--simulated">SIMULATED</span>
                </>
              ) : null}
            </p>
            {outcome.messages.length > 0 ? (
              <ul className="notice__list">
                {outcome.messages.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            ) : null}
          </div>
          <button type="button" className="btn btn--ghost btn--icon" onClick={onDismiss} aria-label="Dismiss message">
            <Icon name="close" />
          </button>
        </div>
      ) : null}
    </div>
  );
}
