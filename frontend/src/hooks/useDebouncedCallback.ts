import { useCallback, useEffect, useRef } from "react";

/** Returns a stable function that invokes `callback` once calls have stopped for `delayMs`. */
export function useDebouncedCallback<T>(callback: (value: T) => void, delayMs: number): (value: T) => void {
  const callbackRef = useRef(callback);
  const timerRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => () => window.clearTimeout(timerRef.current), []);

  return useCallback(
    (value: T) => {
      window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => callbackRef.current(value), delayMs);
    },
    [delayMs],
  );
}
