import type { ReactNode } from "react";
import type { ApiError } from "../api/client";
import { Icon } from "./Icon";

interface ErrorNoticeProps {
  title: string;
  error: ApiError;
  onRetry?: () => void;
  onDismiss?: () => void;
}

/** Inline, announced error with the server's message and machine code. */
export function ErrorNotice({ title, error, onRetry, onDismiss }: ErrorNoticeProps) {
  return (
    <div className="notice notice--error" role="alert">
      <Icon name="alert" size={18} />
      <div className="notice__body">
        <p className="notice__title">{title}</p>
        <p>
          {error.message} <span className="notice__code">({error.code})</span>
        </p>
      </div>
      {onRetry ? (
        <button type="button" className="btn btn--small" onClick={onRetry}>
          <Icon name="refresh" size={14} />
          Try again
        </button>
      ) : null}
      {onDismiss ? (
        <button type="button" className="btn btn--ghost btn--icon" onClick={onDismiss} aria-label="Dismiss error">
          <Icon name="close" />
        </button>
      ) : null}
    </div>
  );
}

interface SkeletonProps {
  label: string;
  lines?: number;
  block?: boolean;
}

/** Placeholder shown while an area loads; announces `label` to assistive technology. */
export function Skeleton({ label, lines = 3, block = false }: SkeletonProps) {
  return (
    <div className="skeleton" role="status" aria-label={label}>
      {block ? <div className="skeleton__block" /> : null}
      {Array.from({ length: lines }, (_, index) => (
        <div key={index} className="skeleton__line" />
      ))}
    </div>
  );
}

interface EmptyStateProps {
  children: ReactNode;
}

export function EmptyState({ children }: EmptyStateProps) {
  return <p className="empty">{children}</p>;
}
