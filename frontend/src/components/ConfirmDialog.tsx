import { type ReactNode, useEffect, useId, useRef } from "react";
import type { ApiError } from "../api/client";
import { ErrorNotice } from "./Feedback";

interface Props {
  open: boolean;
  title: string;
  confirmLabel: string;
  busyLabel: string;
  busy: boolean;
  error: ApiError | null;
  danger?: boolean;
  children: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}

/** In-page modal confirmation built on the native <dialog> (focus trap and Esc handling included). */
export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  busyLabel,
  busy,
  error,
  danger = false,
  children,
  onConfirm,
  onCancel,
}: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog === null) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className="dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <h2 id={titleId} className="dialog__title">
        {title}
      </h2>
      <div className="dialog__body">{children}</div>
      {error ? <ErrorNotice title="That did not work" error={error} /> : null}
      <div className="dialog__actions">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className={`btn ${danger ? "btn--danger" : "btn--primary"}`}
          onClick={onConfirm}
          disabled={busy}
        >
          {busy ? busyLabel : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
