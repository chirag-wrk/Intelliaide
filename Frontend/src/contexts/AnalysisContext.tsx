import { createContext, useContext, useState, useRef, useCallback, useEffect, type ReactNode } from 'react';
import type { WorkflowStep, ConsoleMessage } from '../types';
import {
  startAnalysis as apiStartAnalysis,
  uploadMustGather as apiUploadMustGather,
  initSession,
  clearSession,
  cancelAnalysis as apiCancelAnalysis,
  getWorkflowConsole,
  getSessionStatus,
  getPodStatus,
  resetBackendCheck,
} from '../services/api';
import { mockWorkflowSteps, mockConsoleMessages } from '../data/mockData';

const REAL_STEPS: WorkflowStep[] = [
  { id: 'extract', label: 'Extracting Archive', phase: 'extracting', status: 'pending' },
  { id: 'init', label: 'Initialize Agent', phase: 'initializing', status: 'pending' },
  { id: 'files', label: 'File Selection', phase: 'file_selection', status: 'pending' },
  { id: 'yaml', label: 'YAML Processing', phase: 'yaml_processing', status: 'pending' },
  { id: 'logs', label: 'Log Processing', phase: 'log_processing', status: 'pending' },
  { id: 'aggregate', label: 'Data Aggregation', phase: 'data_aggregation', status: 'pending' },
  { id: 'rca', label: 'Root Cause Analysis', phase: 'rca_analysis', status: 'pending' },
];

const PHASE_SEQUENCE = [
  'extracting', 'initializing', 'file_selection', 'yaml_processing',
  'log_processing', 'data_aggregation', 'rca_analysis', 'completed',
];

function phaseIdx(phase: string): number {
  return PHASE_SEQUENCE.indexOf(phase);
}

function classifyLine(line: string): ConsoleMessage['type'] {
  const t = line.trim();
  if (/^={3,}/.test(t) || /^-{3,}/.test(t)) return 'dim';
  if (/=== Phase/.test(t) || /^AGENTIC/.test(t) || /^LLM API/.test(t)) return 'heading';
  if (/\[Success\]/.test(t)) return 'success';
  if (/\[Error\]/.test(t) || /\[Warning\]/.test(t) || /^ERROR/.test(t)) return 'error';
  if (/\[Tool/.test(t) || /\[Agent/.test(t)) return 'info';
  if (!t) return 'dim';
  return 'normal';
}

interface AnalysisContextValue {
  isRunning: boolean;
  isDeepening: boolean;
  isUploading: boolean;
  uploadProgress: number;
  podBusy: boolean;
  sessionId: string | null;
  setSessionId: (sid: string | null) => void;
  progress: number;
  steps: WorkflowStep[];
  messages: ConsoleMessage[];
  error: string | null;
  problem: string;
  setProblem: (v: string) => void;
  caseNumber: string;
  setCaseNumber: (v: string) => void;
  rootFolder: string;
  setRootFolder: (v: string) => void;
  selectedFile: File | null;
  fileName: string;
  setSelectedFile: (file: File | null) => void;
  startNewAnalysis: (problem: string, caseNumOverride?: string, localFileIdOverride?: string, owner?: string) => Promise<void>;
  startDeepening: (sessionId: string) => void;
  resumeSession: (sid: string, status: string) => Promise<void>;
  cancelAnalysis: () => Promise<void>;
  clearAnalysis: () => Promise<void>;
}

const AnalysisContext = createContext<AnalysisContextValue | null>(null);

export function useAnalysis() {
  const ctx = useContext(AnalysisContext);
  if (!ctx) throw new Error('useAnalysis must be used within AnalysisProvider');
  return ctx;
}

export function AnalysisProvider({ children }: { children: ReactNode }) {
  const [isRunning, setIsRunning] = useState(false);
  const [isDeepening, setIsDeepening] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [podBusy, setPodBusy] = useState(false);
  const [sessionId, setSessionIdRaw] = useState<string | null>(
    () => sessionStorage.getItem('rca_session_id'),
  );
  const setSessionId = useCallback((sid: string | null) => {
    setSessionIdRaw(sid);
    if (sid) sessionStorage.setItem('rca_session_id', sid);
    else sessionStorage.removeItem('rca_session_id');
  }, []);
  const [progress, setProgress] = useState(0);
  const [steps, setSteps] = useState<WorkflowStep[]>(REAL_STEPS.map(s => ({ ...s })));
  const [messages, setMessages] = useState<ConsoleMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [problem, setProblem] = useState('');
  const [caseNumber, setCaseNumber] = useState('');
  const [rootFolder, setRootFolder] = useState('');
  const [selectedFile, setSelectedFileRaw] = useState<File | null>(null);
  const [fileName, setFileName] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mockRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const podPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Disabled: calling initSession() on every new tab/load wiped result files
  // before the user could view them via deep-links. Cleanup now happens inside
  // run_workflow() on the backend at the start of each new analysis instead.
  // useEffect(() => {
  //   initSession();
  // }, []);

  const setSelectedFile = useCallback((file: File | null) => {
    setSelectedFileRaw(file);
    setFileName(file?.name ?? '');
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (mockRef.current) { clearInterval(mockRef.current); mockRef.current = null; }
  }, []);

  const updateStepsFromPhase = useCallback((phase: string, isError = false) => {
    const ci = phaseIdx(phase);
    setSteps(prev => {
      const base = prev.length === REAL_STEPS.length ? REAL_STEPS : mockWorkflowSteps;
      return base.map((step, i) => {
        if (isError && i === ci) return { ...step, status: 'error' as const };
        if (isError && i < ci) return { ...step, status: 'completed' as const };
        if (phase === 'completed') return { ...step, status: 'completed' as const };
        if (i < ci) return { ...step, status: 'completed' as const };
        if (i === ci) return { ...step, status: 'active' as const };
        return { ...step, status: 'pending' as const };
      });
    });
  }, []);

  const fetchConsole = useCallback(async (sid?: string | null) => {
    try {
      const text = await getWorkflowConsole(sid);
      if (text && text !== 'No workflow console output yet.') {
        setMessages(text.split('\n').map((line: string, i: number) => ({
          id: i + 1, text: line, type: classifyLine(line),
        })));
      }
    } catch { /* will retry */ }
  }, []);

  const startRealPolling = useCallback((sid: string, skipInitReset = false) => {
    stopPolling();
    if (!skipInitReset) {
      updateStepsFromPhase('initializing');
      setProgress(5);
    }

    pollRef.current = setInterval(async () => {
      try {
        const status = await getSessionStatus(sid);
        const phase = status.phase || 'initializing';
        const prog = typeof status.progress === 'number' ? status.progress : 0;
        setProgress(prog);

        if (status.status === 'completed') {
          updateStepsFromPhase('completed');
          setProgress(100);
          setIsRunning(false);
          setIsDeepening(false);
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          await fetchConsole(sid);
        } else if (status.status === 'cancelled') {
          updateStepsFromPhase(phase);
          setIsRunning(false);
          setIsDeepening(false);
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          await fetchConsole(sid);
        } else if (status.status === 'error') {
          updateStepsFromPhase(phase, true);
          setError(status.error || status.message || 'Analysis failed');
          setIsRunning(false);
          setIsDeepening(false);
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        } else if (status.status === 'queued') {
          updateStepsFromPhase('initializing');
          setProgress(0);
        } else {
          updateStepsFromPhase(phase);
        }
      } catch { /* will retry */ }

      await fetchConsole(sid);
    }, 2000);
  }, [stopPolling, updateStepsFromPhase, fetchConsole]);

  const simulateMockWorkflow = useCallback(() => {
    const totalSteps = mockWorkflowSteps.length;
    let step = 0;
    setMessages([]);
    setProgress(0);

    mockRef.current = setInterval(() => {
      if (step < totalSteps) {
        setSteps(prev =>
          prev.map((s, i) =>
            i < step ? { ...s, status: 'completed' as const }
              : i === step ? { ...s, status: 'active' as const }
                : s
          )
        );
        setProgress(Math.round(((step + 0.5) / totalSteps) * 100));

        const phaseMessages = mockConsoleMessages.filter(m => {
          if (step === 0) return m.id <= 4;
          if (step === 1) return m.id >= 5 && m.id <= 8;
          if (step === 2) return m.id >= 9 && m.id <= 15;
          if (step === 3) return m.id >= 16;
          return false;
        });
        phaseMessages.forEach((m, i) => {
          setTimeout(() => {
            setMessages(prev => prev.find(p => p.id === m.id) ? prev : [...prev, m]);
          }, i * 200);
        });
        step++;
      } else {
        setSteps(prev => prev.map(s => ({ ...s, status: 'completed' as const })));
        setProgress(100);
        setIsRunning(false);
        if (mockRef.current) clearInterval(mockRef.current);
      }
    }, 2000);
  }, []);

  const startNewAnalysis = useCallback(async (
    problem: string,
    caseNumOverride?: string,
    localFileIdOverride?: string,
    owner?: string,
  ) => {
    setIsRunning(true);
    setIsDeepening(false);
    setError(null);
    setMessages([]);
    setProgress(0);
    resetBackendCheck();

    try {
      let localFileId = localFileIdOverride || '';

      if (!localFileId && selectedFile) {
        const fname = selectedFile.name.toLowerCase();
        const validArchive = fname.endsWith('.zip') || fname.endsWith('.tar.gz');
        if (!validArchive) {
          throw new Error('Unsupported file type. Please select a .zip or .tar.gz archive.');
        }

        setIsUploading(true);
        setUploadProgress(0);

        const uploadResult = await apiUploadMustGather(selectedFile, selectedFile.name, (pct) => {
          setUploadProgress(pct);
        });
        localFileId = uploadResult.local_file_id;
        setIsUploading(false);
        setUploadProgress(100);
      }

      const result = await apiStartAnalysis(problem, localFileId, rootFolder, caseNumOverride ?? caseNumber, owner);
      const sid = result.session_id;
      setSessionId(sid);

      if (sid?.startsWith('session_mock_')) {
        setSteps(mockWorkflowSteps.map(s => ({ ...s, status: 'pending' as const })));
        simulateMockWorkflow();
      } else {
        setSteps(REAL_STEPS.map(s => ({ ...s, status: 'pending' as const })));
        startRealPolling(sid);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start analysis');
      setIsRunning(false);
      setIsUploading(false);
    }
  }, [selectedFile, rootFolder, caseNumber, simulateMockWorkflow, startRealPolling]);

  const startDeepening = useCallback((sid: string) => {
    setIsRunning(true);
    setIsDeepening(true);
    setError(null);
    setSessionId(sid);
    setMessages([]);
    setSteps(REAL_STEPS.map(s => ({ ...s, status: 'pending' as const })));
    updateStepsFromPhase('initializing');
    setProgress(5);
    startRealPolling(sid, true);
  }, [updateStepsFromPhase, startRealPolling]);

  const resumeSession = useCallback(async (sid: string, status: string) => {
    stopPolling();
    setSessionId(sid);
    setError(null);
    setSteps(REAL_STEPS.map(s => ({ ...s, status: 'pending' as const })));

    if (status === 'running' || status === 'in_progress' || status === 'queued') {
      setIsRunning(true);
      setMessages([]);
      setProgress(status === 'queued' ? 0 : 5);
      startRealPolling(sid, true);
    } else if (status === 'completed' || status === 'rca_completed') {
      setIsRunning(false);
      setProgress(100);
      updateStepsFromPhase('completed');
      await fetchConsole(sid);
    } else if (status === 'error') {
      setIsRunning(false);
      setProgress(0);
    }
  }, [stopPolling, startRealPolling, updateStepsFromPhase, fetchConsole]);


  const cancelAnalysis = useCallback(async () => {
    if (sessionId) {
      await apiCancelAnalysis(sessionId);
    }
    // Polling will detect the 'cancelled' status and stop itself,
    // but stop it immediately here too so the UI responds instantly.
    stopPolling();
    setIsRunning(false);
    setIsDeepening(false);
    setProgress(0);
    setSteps(REAL_STEPS.map(s => ({ ...s, status: 'pending' as const })));
  }, [sessionId, stopPolling]);

  const clearAnalysis = useCallback(async () => {
    stopPolling();
    setIsRunning(false);
    setIsDeepening(false);
    setIsUploading(false);
    setUploadProgress(0);
    setError(null);
    setMessages([]);
    setSteps(REAL_STEPS.map(s => ({ ...s, status: 'pending' as const })));
    setProgress(0);
    setSessionId(null);
    setSelectedFileRaw(null);
    setFileName('');
    await clearSession();
  }, [stopPolling]);

  useEffect(() => () => stopPolling(), [stopPolling]);

  // Poll pod-status so we know when another user is running on this pod
  useEffect(() => {
    const check = async () => {
      const { busy } = await getPodStatus();
      setPodBusy(busy && !isRunning);
    };
    check();
    podPollRef.current = setInterval(check, 10_000);
    return () => { if (podPollRef.current) clearInterval(podPollRef.current); };
  }, [isRunning]);

  return (
    <AnalysisContext.Provider value={{
      isRunning, isDeepening, isUploading, uploadProgress, podBusy,
      sessionId, setSessionId, progress, steps, messages, error,
      problem, setProblem, caseNumber, setCaseNumber,
      rootFolder, setRootFolder,
      selectedFile, fileName, setSelectedFile,
      startNewAnalysis, startDeepening, resumeSession, cancelAnalysis, clearAnalysis,
    }}>
      {children}
    </AnalysisContext.Provider>
  );
}
