import { useLayoutEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAnalysis } from '../contexts/AnalysisContext';

/**
 * Resolve the active RCA session from the URL (?session=) or AnalysisContext /
 * sessionStorage, keep them in sync, and ensure the session id stays in the URL
 * so sidebar navigation does not drop it on tab switches.
 */
export function useEffectiveSession() {
  const { sessionId, setSessionId } = useAnalysis();
  const [searchParams, setSearchParams] = useSearchParams();

  const urlSession = searchParams.get('session');
  const effectiveSession = urlSession || sessionId || null;

  const sessionSuffix = useMemo(
    () => (effectiveSession ? `?session=${encodeURIComponent(effectiveSession)}` : ''),
    [effectiveSession],
  );

  // Run before paint so the first data fetch on a remounted tab already has
  // the session in both context and the URL.
  useLayoutEffect(() => {
    if (urlSession) {
      if (urlSession !== sessionId) {
        setSessionId(urlSession);
      }
      return;
    }
    if (sessionId) {
      setSearchParams({ session: sessionId }, { replace: true });
    }
  }, [urlSession, sessionId, setSessionId, setSearchParams]);

  return { effectiveSession, sessionSuffix, urlSession };
}
