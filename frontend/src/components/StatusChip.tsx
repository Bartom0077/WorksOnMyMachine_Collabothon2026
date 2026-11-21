import { STATUS_LABELS } from "../lib/format";
import type { RiskStatus } from "../types/api";
import { Icon, type IconName } from "./Icon";

export const STATUS_ICONS: Record<RiskStatus, IconName> = {
  GREEN: "check",
  YELLOW: "warning",
  RED: "alert",
};

interface Props {
  status: RiskStatus;
}

/** Risk status as icon + text, so it never relies on colour alone. */
export function StatusChip({ status }: Props) {
  return (
    <span className={`chip chip--${status.toLowerCase()}`}>
      <Icon name={STATUS_ICONS[status]} size={14} />
      {STATUS_LABELS[status]}
    </span>
  );
}
