const PATHS = {
  check: "M20 6 9 17l-5-5",
  warning: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  alert: "M12 8v4m0 4h.01M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9L7.9 2Z",
  star: "m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21 7 14.2l-5-4.9 6.9-1L12 2Z",
  sparkles: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Zm7 12 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z",
  send: "M22 2 11 13M22 2l-7 20-4-9-9-4 20-7Z",
  trash: "M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m5 5v6m4-6v6",
  close: "M18 6 6 18M6 6l12 12",
  calendar: "M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z",
  arrowDown: "M12 5v14m7-7-7 7-7-7",
  arrowUp: "M12 19V5m-7 7 7-7 7 7",
  clock: "M12 6v6l4 2m6-2a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  bank: "M3 21h18M5 21V10m4 11V10m6 11V10m4 11V10M2 10l10-7 10 7H2Z",
  chat: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10Z",
  eye: "M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
  refresh: "M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6",
  lock: "M7 11V7a5 5 0 0 1 10 0v4M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Z",
  exchange: "m17 3 4 4-4 4M21 7H7m0 14-4-4 4-4m-4 4h14",
  database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
} as const;

export type IconName = keyof typeof PATHS;

interface Props {
  name: IconName;
  size?: number;
}

/** Decorative line icon; always paired with visible text or an aria-label on its parent. */
export function Icon({ name, size = 16 }: Props) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
