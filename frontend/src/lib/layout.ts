/** The id of the assistant panel, referenced by the toggle that controls it. */
export const ASSISTANT_PANEL_ID = "assistant";

/** The width below which the assistant sits under the forecast instead of beside it; mirrors the breakpoint in styles.css. */
const SINGLE_COLUMN_QUERY = "(max-width: 1100px)";

/** True while the page is laid out as one column (tablets and phones). */
export function isSingleColumn(): boolean {
  return window.matchMedia(SINGLE_COLUMN_QUERY).matches;
}
