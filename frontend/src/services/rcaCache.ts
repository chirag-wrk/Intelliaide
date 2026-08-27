export interface CachedStageEntry {
  stage: 'Tier_1' | 'Tier_2' | 'Final';
  text: string;
}

interface SessionCache {
  stages: CachedStageEntry[];
  summaryText: string;
}

const cache = new Map<string, SessionCache>();

export function getRcaCache(sessionId: string | null): SessionCache | null {
  if (!sessionId) return null;
  return cache.get(sessionId) ?? null;
}

export function setRcaStagesCache(sessionId: string | null, stages: CachedStageEntry[]) {
  if (!sessionId || stages.length === 0) return;
  const prev = cache.get(sessionId);
  cache.set(sessionId, { stages, summaryText: prev?.summaryText ?? '' });
}

export function setRcaSummaryCache(sessionId: string | null, summaryText: string) {
  if (!sessionId) return;
  const prev = cache.get(sessionId);
  cache.set(sessionId, {
    stages: prev?.stages ?? [],
    summaryText,
  });
}

export function clearRcaCache(sessionId?: string | null) {
  if (sessionId) cache.delete(sessionId);
  else cache.clear();
}
