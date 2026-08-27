import { useState, useEffect, useCallback, useRef } from 'react';
import type { AgentMemory, Session } from '../types';
import { getAgentMemory } from '../services/api';

const POLL_INTERVAL_MS = 4000;

export function useAgentMemory(isRunning = false, sessionId?: string | null) {
  const [memory, setMemory] = useState<AgentMemory | null>(null);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const prevRunningRef = useRef(isRunning);
  const sessionIdRef = useRef(sessionId);
  const hasLoadedRef = useRef(false);
  sessionIdRef.current = sessionId;

  const refresh = useCallback(async () => {
    if (!hasLoadedRef.current) setLoading(true);
    setError(null);
    try {
      const data = await getAgentMemory(sessionIdRef.current);
      setMemory(data);
      setVersion(v => v + 1);
      hasLoadedRef.current = true;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load agent memory');
    } finally {
      setLoading(false);
    }
  }, []);

  const silentRefresh = useCallback(async () => {
    try {
      const data = await getAgentMemory(sessionIdRef.current);
      setMemory(data);
      setVersion(v => v + 1);
    } catch {
      // Swallow errors during background polling
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (!isRunning) {
      if (prevRunningRef.current) {
        silentRefresh();
        const t = setTimeout(silentRefresh, 1500);
        prevRunningRef.current = isRunning;
        return () => clearTimeout(t);
      }
      prevRunningRef.current = isRunning;
      return;
    }

    if (!prevRunningRef.current) {
      setMemory(null);
      setVersion(v => v + 1);
    }
    prevRunningRef.current = isRunning;

    silentRefresh();
    const interval = setInterval(silentRefresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [isRunning, silentRefresh]);

  // Re-fetch when sessionId changes (e.g. user switches to a different session)
  useEffect(() => {
    if (sessionId) {
      hasLoadedRef.current = false;
      silentRefresh();
    }
  }, [sessionId, silentRefresh]);

  const latestSession: Session | null = memory?.sessions?.[memory.sessions.length - 1] ?? null;

  return { memory, latestSession, loading, error, refresh, version };
}
