import { useCallback, useEffect, useState } from "react";
import { type ApiError, toApiError } from "../api/client";

export interface AsyncResource<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

interface Options {
  /** When false nothing is fetched and any in-flight request is aborted. */
  enabled?: boolean;
  /**
   * Keep the previous data while a reload is in flight (avoids skeleton flicker) and when a reload fails,
   * in which case `error` is set next to the now outdated `data`.
   */
  keepPreviousData?: boolean;
}

interface State<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
}

/**
 * Loads a resource whenever `load` changes identity (memoise it with useCallback) or `reload` is called.
 * Stale responses are discarded by aborting the previous request.
 */
export function useAsyncResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  { enabled = true, keepPreviousData = false }: Options = {},
): AsyncResource<T> {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: enabled });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setState((previous) => ({
      data: keepPreviousData ? previous.data : null,
      error: null,
      loading: true,
    }));
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ data, error: null, loading: false });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setState((previous) => ({
            data: keepPreviousData ? previous.data : null,
            error: toApiError(error),
            loading: false,
          }));
        }
      },
    );
    return () => controller.abort();
  }, [load, enabled, keepPreviousData, version]);

  const reload = useCallback(() => setVersion((current) => current + 1), []);
  return { ...state, reload };
}
