import type { AgentMemory, CausalDag } from '../types';
import { mockAgentMemory, mockConsoleOutput, mockRCATier1, mockRCATier2, mockRCAFinal } from '../data/mockData';

/**
 * API base URL:
 * - In dev (Vite), requests to /api/* are proxied to http://localhost:8000/*
 * - In production, set VITE_API_URL env var or default to same-origin /api
 */
const API_BASE = import.meta.env.VITE_API_URL || '/api';

/**
 * Direct API URL for large uploads — bypasses the Nginx reverse proxy so the
 * browser talks straight to the backend pod via its own OpenShift route.
 * Resolved once at module load:
 *   1. Explicit VITE_API_DIRECT_URL env var (highest priority)
 *   2. Auto-detect from hostname: replace "frontend-route" → "api-route"
 *   3. Fall back to API_BASE (goes through Nginx — fine for dev / small files)
 */
const API_DIRECT_URL: string = (() => {
  if (import.meta.env.VITE_API_DIRECT_URL) return import.meta.env.VITE_API_DIRECT_URL;
  const host = window.location.hostname;
  if (host.includes('frontend-route')) {
    const apiHost = host.replace('frontend-route', 'api-route');
    return `${window.location.protocol}//${apiHost}`;
  }
  return API_BASE;
})();

let _backendAvailable: boolean | null = null;

async function checkBackend(): Promise<boolean> {
  if (_backendAvailable === true) return true;
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
    _backendAvailable = res.ok;
  } catch {
    _backendAvailable = null;
  }
  return _backendAvailable ?? false;
}

export async function getPodStatus(): Promise<{ busy: boolean; active_session_id: string | null }> {
  if (await checkBackend()) {
    try {
      const res = await fetch(`${API_BASE}/pod-status`, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return res.json();
    } catch { /* fall through */ }
  }
  return { busy: false, active_session_id: null };
}

/** Reset backend status so next call re-checks (call after starting backend). */
export function resetBackendCheck() {
  _backendAvailable = null;
}

export async function getAgentMemory(sessionId?: string | null): Promise<AgentMemory> {
  if (await checkBackend()) {
    try {
      const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
      const res = await fetch(`${API_BASE}/agent-memory${qs}`);
      if (res.ok) return res.json();
    } catch { /* fall through to mock */ }
  }
  return mockAgentMemory;
}

export async function getWorkflowConsole(sessionId?: string | null): Promise<string> {
  if (await checkBackend()) {
    try {
      const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
      const res = await fetch(`${API_BASE}/workflow-console${qs}`);
      if (res.ok) return res.text();
    } catch { /* fall through */ }
  }
  return mockConsoleOutput;
}

export async function getRCASummary(sessionId?: string | null): Promise<string> {
  if (await checkBackend()) {
    try {
      const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
      const res = await fetch(`${API_BASE}/rca-summary${qs}`);
      if (res.ok) return res.text();
    } catch { /* fall through */ }
  }
  return mockAgentMemory.sessions[0]?.results.rca_summary ?? '';
}

/** Fetch a single RCA stage file.
 * @param stage   Tier_1 | Tier_2 | Final
 * @param session Optional session ID for scoping.
 */
export async function getRCAStage(
  stage: 'Tier_1' | 'Tier_2' | 'Final',
  session?: string | null,
): Promise<string> {
  if (await checkBackend()) {
    try {
      const qs = session ? `?session_id=${encodeURIComponent(session)}` : '';
      const res = await fetch(`${API_BASE}/rca-stage/${stage}${qs}`);
      if (res.status === 204 || !res.ok) return '';
      return res.text();
    } catch { /* fall through */ }
  }
  const mocks: Record<'Tier_1' | 'Tier_2' | 'Final', string> = {
    Tier_1: mockRCATier1,
    Tier_2: mockRCATier2,
    Final:  mockRCAFinal,
  };
  return mocks[stage] ?? '';
}

/** Fetch the RCA executive summary text. */
export async function getRCAReportSummary(session?: string | null): Promise<string> {
  if (await checkBackend()) {
    try {
      const qs = session ? `?session_id=${encodeURIComponent(session)}` : '';
      const res = await fetch(`${API_BASE}/rca-report-summary${qs}`);
      if (res.status === 204 || !res.ok) return '';
      return res.text();
    } catch { /* fall through */ }
  }
  return '';
}

/** Fetch the causal-DAG JSON for a given stage. Returns null if not (yet) available —
 *  older sessions predating this feature, or best-effort generation failure. */
export async function getRCADag(
  stage: 'Tier_1' | 'Tier_2' | 'Final',
  session?: string | null,
): Promise<CausalDag | null> {
  if (await checkBackend()) {
    try {
      const qs = session ? `?session_id=${encodeURIComponent(session)}` : '';
      const res = await fetch(`${API_BASE}/rca-dag/${stage}${qs}`);
      if (res.status === 204 || !res.ok) return null;
      const data = await res.json();
      if (data && Array.isArray(data.nodes) && data.nodes.length > 0) return data as CausalDag;
      return null;
    } catch { /* fall through */ }
  }
  return null;
}

/** Fetch all three stage files in parallel; returns only those that have content. */
export async function getRCAStages(
  session?: string | null,
): Promise<{ stage: 'Tier_1' | 'Tier_2' | 'Final'; text: string }[]> {
  const stages = ['Tier_1', 'Tier_2', 'Final'] as const;
  const results = await Promise.all(stages.map(s => getRCAStage(s, session)));
  return stages
    .map((stage, i) => ({ stage, text: results[i] }))
    .filter(r => r.text.trim().length > 0);
}

/** Download all available RCA stage files as a ZIP bundle. */
export async function downloadRCABundleZip(session?: string | null): Promise<void> {
  const qs = session ? `?session_id=${encodeURIComponent(session)}` : '';
  const res = await fetch(`${API_BASE}/rca-bundle-zip${qs}`);
  if (!res.ok) throw new Error(`Bundle download failed (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'RCA_Reports_Bundle.zip';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Download the full bundle ZIP (all RCA stages + analytics PDF + logging/tracing doc). */
export async function downloadFullBundleZip(session?: string | null): Promise<void> {
  const qs = session ? `?session_id=${encodeURIComponent(session)}` : '';
  const res = await fetch(`${API_BASE}/full-bundle-zip${qs}`);
  if (!res.ok) throw new Error(`Full bundle download failed (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'RCA_Bundle.zip';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}


function _doUpload(
  url: string,
  formData: FormData,
  onProgress?: (pct: number) => void,
): Promise<{ local_file_id: string; filename: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);

    if (onProgress) {
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      });
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else if (xhr.status === 413) {
        reject(new Error(
          'Request Entity Too Large — please convert your archive to .tar.gz and upload again.',
        ));
      } else {
        const detail = (() => {
          try { return JSON.parse(xhr.responseText).detail; } catch { return xhr.statusText; }
        })();
        reject(new Error(detail || `Upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error('Network error during upload'));
    xhr.send(formData);
  });
}

export async function uploadMustGather(
  archiveBlob: Blob,
  filename: string,
  onProgress?: (pct: number) => void,
): Promise<{ local_file_id: string; filename: string }> {
  if (!(await checkBackend())) {
    throw new Error('Backend is not available');
  }

  const formData = new FormData();
  formData.append('file', archiveBlob, filename);

  // Try the direct API route first (bypasses Nginx proxy, better for large files).
  // Only fall back to the proxied path on network/CORS errors — not on 4xx responses.
  if (API_DIRECT_URL !== API_BASE) {
    try {
      return await _doUpload(`${API_DIRECT_URL}/upload-must-gather`, formData, onProgress);
    } catch (err) {
      // Propagate 413 (and other meaningful HTTP errors) immediately — retrying
      // through the proxy would produce the same result.
      const msg = err instanceof Error ? err.message : '';
      if (msg.includes('Too Large') || msg.includes('413')) throw err;
      onProgress?.(0);
    }
  }

  return _doUpload(`${API_BASE}/upload-must-gather`, formData, onProgress);
}

export async function startAnalysis(
  problemStatement: string,
  localFileId: string,
  rootFolder = '',
  caseNumber = '',
  owner = '',
) {
  if (await checkBackend()) {
    const body: Record<string, string> = {
      user_query: problemStatement,
      must_gather_root_folder: rootFolder,
      local_file_id: localFileId,
    };
    if (caseNumber.trim()) body.case_number = caseNumber.trim();
    if (owner.trim()) body.owner = owner.trim();
    const res = await fetch(`${API_BASE}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(detail.detail || `API error ${res.status}`);
    }
    return res.json();
  }
  return { session_id: `session_mock_${Date.now()}`, status: 'started' };
}

export async function submitFeedback(
  sessionId: string,
  satisfactory: boolean,
  feedbackText = '',
  originalSessionId = '',
) {
  if (await checkBackend()) {
    const res = await fetch(`${API_BASE}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        satisfactory,
        feedback_text: feedbackText,
        original_session_id: originalSessionId,
      }),
    });
    if (res.ok) return res.json();
    const errBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errBody.detail || `Feedback failed (${res.status})`);
  }
  return { status: 'ok' };
}

export async function initSession() {
  if (await checkBackend()) {
    await fetch(`${API_BASE}/init`, { method: 'POST' }).catch(() => {});
  }
}

export async function clearSession() {
  if (await checkBackend()) {
    await fetch(`${API_BASE}/clear`, { method: 'POST' }).catch(() => {});
  }
}

export async function cancelAnalysis(sessionId: string): Promise<void> {
  if (await checkBackend()) {
    await fetch(`${API_BASE}/cancel/${encodeURIComponent(sessionId)}`, { method: 'POST' }).catch(() => {});
  }
}

export async function getSessionStatus(sessionId: string) {
  if (await checkBackend()) {
    try {
      const res = await fetch(`${API_BASE}/status/${sessionId}`);
      if (res.ok) return res.json();
    } catch { /* fall through */ }
  }
  return { session_id: sessionId, status: 'unknown' };
}


export interface JobSession {
  session_id: string;
  status: string;
  phase: string;
  progress: number;
  message: string;
  case_number?: string;
  problem_statement?: string;
  created_at?: string;
  filename?: string;
  rca_summary?: string;
  error?: string;
  owner?: string;
  deepening_round?: number;
}

export async function getSessions(owner?: string): Promise<JobSession[]> {
  if (await checkBackend()) {
    try {
      const qs = owner ? `?owner=${encodeURIComponent(owner)}` : '';
      const res = await fetch(`${API_BASE}/sessions${qs}`);
      if (res.ok) return res.json();
    } catch { /* fall through */ }
  }
  return [];
}

/** Remove all completed / error / cancelled sessions from the server-side board. */
export async function clearSessionsHistory(): Promise<void> {
  if (await checkBackend()) {
    await fetch(`${API_BASE}/clear-sessions`, { method: 'POST' }).catch(() => {});
  }
}

export interface AttachmentOption {
  uuid: string;
  filename: string;
  size_bytes: number;
  created: string;
}

export type CaseAttachmentResult =
  | { status: 'downloading'; download_id: string; filename: string }
  | { status: 'downloaded'; local_file_id: string; filename: string }
  | { status: 'multiple'; attachments: AttachmentOption[] };

export type DownloadStatus =
  | { status: 'downloading'; filename: string }
  | { status: 'downloaded'; local_file_id: string; filename: string }
  | { status: 'error'; detail: string };

/** List must-gather attachments for a case (or kick off background download if only one). */
export async function fetchCaseAttachment(
  caseNumber: string,
): Promise<CaseAttachmentResult> {
  const res = await fetch(`${API_BASE}/case-attachments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_number: caseNumber }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `Failed to fetch case attachment (${res.status})`);
  }
  return res.json();
}

/** Download a specific attachment UUID chosen by the user from a 'multiple' listing. */
export async function downloadSpecificAttachment(
  caseNumber: string,
  attachmentUuid: string,
  filename: string,
): Promise<{ download_id: string; filename: string }> {
  const res = await fetch(`${API_BASE}/case-attachments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_number: caseNumber, attachment_uuid: attachmentUuid, filename }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `Download failed (${res.status})`);
  }
  return res.json();
}

/** Poll a background Hydra download until it completes or fails. */
export async function getDownloadStatus(downloadId: string): Promise<DownloadStatus> {
  const res = await fetch(`${API_BASE}/download-status/${encodeURIComponent(downloadId)}`);
  if (!res.ok) {
    throw new Error(`Download status check failed (${res.status})`);
  }
  return res.json();
}


// ---------------------------------------------------------------------------
// Admin API
// ---------------------------------------------------------------------------

export async function getWhoAmI(): Promise<{ name: string; email: string; is_admin: boolean }> {
  const res = await fetch(`${API_BASE}/whoami`, { credentials: 'same-origin' });
  if (!res.ok) return { name: '', email: '', is_admin: false };
  return res.json();
}

export interface AdminStats {
  total: number;
  running: number;
  completed: number;
  failed: number;
  active_pods: number;
  pvc_usage: {
    total_gb: number;
    used_gb: number;
    free_gb: number;
    used_pct: number;
  };
  owners: Record<string, number>;
}

export async function getAdminStats(): Promise<AdminStats> {
  const res = await fetch(`${API_BASE}/admin/stats`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Admin stats failed (${res.status})`);
  return res.json();
}

export async function getAdminSessions(): Promise<JobSession[]> {
  const res = await fetch(`${API_BASE}/admin/sessions`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Admin sessions failed (${res.status})`);
  return res.json();
}

export async function adminCancelJob(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/admin/cancel/${encodeURIComponent(sessionId)}`, {
    method: 'POST',
    credentials: 'same-origin',
  });
  if (!res.ok) throw new Error(`Admin cancel failed (${res.status})`);
}

export async function adminDeleteJob(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/admin/delete/${encodeURIComponent(sessionId)}`, {
    method: 'POST',
    credentials: 'same-origin',
  });
  if (!res.ok) throw new Error(`Admin delete failed (${res.status})`);
}