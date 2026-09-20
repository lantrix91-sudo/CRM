import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api';

/** Shared request state, double-submit protection and expired-session handling. */
export function useTask(onUnauthorized: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const running = useRef(false);
  const alive = useRef(true);
  const handler = useRef(onUnauthorized);
  useEffect(() => { handler.current = onUnauthorized; }, [onUnauthorized]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const run = useCallback(async (task: () => Promise<void>) => {
    if (running.current) return;
    running.current = true;
    if (alive.current) { setBusy(true); setError(''); }
    try { await task(); }
    catch (e) {
      const expired = e instanceof ApiError && e.status === 401;
      if (expired) await handler.current().catch(() => undefined);
      if (alive.current) setError(expired ? 'Сессия завершена. Войдите снова.' : e instanceof Error ? e.message : 'Не удалось выполнить действие');
    } finally { running.current = false; if (alive.current) setBusy(false); }
  }, []);
  return { busy, error, setError, run };
}
