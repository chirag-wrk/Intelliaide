import { useRef, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CheckCircle2, Loader2, Circle, AlertCircle, StopCircle } from 'lucide-react';
import type { WorkflowStep, ConsoleMessage } from '../types';
import { useAnalysis } from '../contexts/AnalysisContext';
import { useAgentMemory } from '../hooks/useAgentMemory';
import { getSessions } from '../services/api';
import type { JobSession } from '../services/api';

function StepIcon({ status }: { status: WorkflowStep['status'] }) {
  switch (status) {
    case 'completed': return <CheckCircle2 className="w-6 h-6 text-brand-red" />;
    case 'active': return <Loader2 className="w-6 h-6 text-brand-red animate-spin" />;
    case 'error': return <AlertCircle className="w-6 h-6 text-red-500" />;
    default: return <Circle className="w-6 h-6 text-slate-300" />;
  }
}

function getMessageColor(type: ConsoleMessage['type']): string {
  switch (type) {
    case 'heading': return 'text-blue-300 font-semibold';
    case 'success': return 'text-emerald-400 font-medium';
    case 'error': return 'text-red-400';
    case 'dim': return 'text-slate-400';
    case 'info': return 'text-cyan-300';
    default: return 'text-slate-300';
  }
}

const STAGE_DEFS = [
  { key: 'T1', label: 'Tier 1', short: 'T1' },
  { key: 'T2', label: 'Tier 2', short: 'T2' },
  { key: 'F', label: 'Final', short: 'F' },
] as const;

function DeepeningStageBar({
  priorityStage,
  isRunning,
}: {
  priorityStage: string | undefined;
  isRunning: boolean;
}) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  // Map rca_priority_stage → how many rounds have COMPLETED
  const completedCount =
    priorityStage === 'all' ? 3
    : priorityStage === 'high_medium' ? 2
    : priorityStage === 'high' ? 1
    : 0;

  // While running the active stage is the next one to complete
  const activeIdx = isRunning ? completedCount : -1;

  return (
    <div className="flex items-center gap-3">
      <span className="text-xs font-medium text-slate-500 whitespace-nowrap">Deepening Tier</span>
      <div className="flex items-center">
        {STAGE_DEFS.map((stage, i) => {
          const isDone = i < completedCount;
          const isActive = i === activeIdx;

          return (
            <div key={stage.key} className="flex items-center">
              {/* Connector line before each node except the first */}
              {i > 0 && (
                <div
                  className={`w-10 h-0.5 transition-colors duration-300 ${
                    i <= completedCount ? 'bg-brand-red' : 'bg-slate-200'
                  }`}
                />
              )}
              <div
                className="relative"
                onMouseEnter={() => setHoveredIdx(i)}
                onMouseLeave={() => setHoveredIdx(null)}
              >
                <div
                  className={`
                    w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold
                    cursor-default select-none transition-all duration-300
                    ${isDone
                      ? 'bg-brand-red text-white shadow-sm'
                      : isActive
                      ? 'bg-brand-red/20 text-brand-red ring-2 ring-brand-red ring-offset-1 animate-pulse'
                      : 'bg-slate-100 text-slate-400'
                    }
                  `}
                >
                  {isActive ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    stage.short
                  )}
                </div>

                {/* Tooltip */}
                {hoveredIdx === i && (
                  <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-10 pointer-events-none">
                    <div className="bg-slate-800 text-white text-xs font-medium px-2.5 py-1 rounded-md whitespace-nowrap shadow-lg">
                      {stage.label}
                      <span className="ml-1.5 text-slate-400 font-normal">
                        {isDone ? '— done' : isActive ? '— in progress' : '— pending'}
                      </span>
                    </div>
                    <div className="w-2 h-2 bg-slate-800 rotate-45 mx-auto -mt-1" />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    rca_completed: { label: 'COMPLETED', cls: 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-300' },
    completed:     { label: 'COMPLETED', cls: 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-300' },
    in_progress:   { label: 'RUNNING',   cls: 'bg-amber-100 text-amber-700 ring-1 ring-amber-300' },
    running:       { label: 'RUNNING',   cls: 'bg-amber-100 text-amber-700 ring-1 ring-amber-300' },
    error:         { label: 'ERROR',     cls: 'bg-red-100 text-red-700 ring-1 ring-red-300' },
    cancelled:     { label: 'CANCELLED', cls: 'bg-slate-100 text-slate-600 ring-1 ring-slate-300' },
    queued:        { label: 'QUEUED',    cls: 'bg-blue-50 text-blue-600 ring-1 ring-blue-200' },
    created:       { label: 'QUEUED',    cls: 'bg-blue-50 text-blue-600 ring-1 ring-blue-200' },
  };
  const { label, cls } = map[status] ?? { label: status.toUpperCase(), cls: 'bg-slate-100 text-slate-500 ring-1 ring-slate-200' };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold tracking-wide ${cls}`}>
      {label}
    </span>
  );
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: '2-digit' });
  } catch {
    return iso;
  }
}


export default function LiveAnalysis() {
  const {
    isRunning, isUploading, uploadProgress,
    sessionId, progress, steps, messages, error,
    cancelAnalysis, resumeSession,
  } = useAnalysis();
  const { latestSession } = useAgentMemory(isRunning, sessionId);
  const [searchParams] = useSearchParams();
  const linkedSessionId = searchParams.get('session');
  const [linkedSessionInfo, setLinkedSessionInfo] = useState<JobSession | null>(null);
  const resumedRef = useRef(false);

  useEffect(() => {
    if (!linkedSessionId) { setLinkedSessionInfo(null); resumedRef.current = false; return; }
    getSessions().then(sessions => {
      const found = sessions.find(s => s.session_id === linkedSessionId) ?? null;
      setLinkedSessionInfo(found);
      if (found && !resumedRef.current) {
        resumedRef.current = true;
        resumeSession(found.session_id, found.status);
      }
    }).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkedSessionId]);

  const sessionMatches = latestSession && sessionId
    ? (latestSession.session_id === sessionId ||
       latestSession.api_session_id === sessionId)
    : false;
  const rawPriorityStage = sessionMatches
    ? latestSession?.results?.rca_priority_stage
    : undefined;
  const prevPriorityStageRef = useRef<string | undefined>(rawPriorityStage);
  if (sessionMatches && latestSession) {
    prevPriorityStageRef.current = rawPriorityStage;
  }
  // While running and memory is briefly null (e.g. useAgentMemory just cleared
  // it on an isRunning transition), fall back to the last known value so the
  // DeepeningStageBar doesn't regress from M-active back to H-active.
  const priorityStage = isRunning && !sessionMatches
    ? prevPriorityStageRef.current
    : rawPriorityStage;
  const [isCancelling, setIsCancelling] = useState(false);
  const consoleRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [messages]);

  const handleCancel = async () => {
    setIsCancelling(true);
    await cancelAnalysis();
    setIsCancelling(false);
  };

  return (
    <div className="space-y-6">
      {linkedSessionInfo && (
        <div className="bg-slate-800 text-white rounded-xl px-5 py-4 flex flex-wrap gap-x-8 gap-y-2 items-start">
          <div>
            <div className="text-[10px] text-slate-400 uppercase tracking-widest mb-0.5">Session</div>
            <div className="font-mono text-xs text-slate-300">{linkedSessionInfo.session_id.slice(0, 30)}…</div>
          </div>
          {linkedSessionInfo.problem_statement && (
            <div className="flex-1 min-w-0">
              <div className="text-[10px] text-slate-400 uppercase tracking-widest mb-0.5">Issue</div>
              <div className="text-slate-200 text-sm truncate max-w-sm">{linkedSessionInfo.problem_statement}</div>
            </div>
          )}
          {linkedSessionInfo.case_number && (
            <div>
              <div className="text-[10px] text-slate-400 uppercase tracking-widest mb-0.5">Case #</div>
              <div className="text-slate-200 text-sm">{linkedSessionInfo.case_number}</div>
            </div>
          )}
          {linkedSessionInfo.filename && (
            <div>
              <div className="text-[10px] text-slate-400 uppercase tracking-widest mb-0.5">
                {linkedSessionInfo.case_number ? 'Must-Gather' : 'File'}
              </div>
              <div className="font-mono text-xs text-slate-300 max-w-xs truncate">{linkedSessionInfo.filename}</div>
            </div>
          )}
        </div>
      )}

      <div>
        <h1 className="text-2xl font-bold text-slate-800">Live Analysis</h1>
        <p className="text-slate-500 text-sm mt-1">
          Monitor the progress of active analysis sessions.
        </p>
      </div>

      {/* Progress Bar + Deepening Stage */}
      <div className="space-y-2">
        <div className="h-1.5 w-full bg-slate-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-brand-red rounded-full transition-all duration-700 ease-out"
            style={{ width: `${progress}%` }}
          />
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-slate-400">{progress}%</span>
          <DeepeningStageBar priorityStage={priorityStage} isRunning={isRunning} />
        </div>
      </div>

      {/* Upload Progress */}
      {isUploading && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
          <div className="flex items-center gap-2 text-sm text-blue-700 mb-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Uploading must-gather archive... {uploadProgress}%</span>
          </div>
          <div className="h-1.5 w-full bg-blue-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-300"
              style={{ width: `${uploadProgress}%` }}
            />
          </div>
        </div>
      )}

      {/* Error Banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <div><span className="font-medium">Error:</span> {error}</div>
        </div>
      )}

      {/* Cancel button — only visible while analysis is running */}
      {isRunning && (
        <div className="flex justify-end">
          <button
            onClick={handleCancel}
            disabled={isCancelling}
            className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium
                       border border-red-300 text-red-600 bg-red-50 hover:bg-red-100
                       transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isCancelling ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <StopCircle className="w-4 h-4" />
            )}
            {isCancelling ? 'Cancelling...' : 'Cancel Analysis'}
          </button>
        </div>
      )}

      {/* Workflow + Console Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Workflow Steps */}
        <div className="lg:col-span-2">
          <h3 className="text-base font-semibold text-slate-800 mb-4">Analysis Workflow</h3>
          <div className="space-y-1">
            {steps.map((step, i) => (
              <div key={step.id} className="flex items-start gap-3">
                <div className="flex flex-col items-center">
                  <StepIcon status={step.status} />
                  {i < steps.length - 1 && (
                    <div
                      className={`w-0.5 h-8 mt-1 ${
                        step.status === 'completed' ? 'bg-brand-red' : 'bg-slate-200'
                      }`}
                    />
                  )}
                </div>
                <div className="pt-0.5">
                  <span
                    className={`text-sm font-medium ${
                      step.status === 'completed'
                        ? 'text-slate-700'
                        : step.status === 'active'
                        ? 'text-brand-red'
                        : step.status === 'error'
                        ? 'text-red-500'
                        : 'text-slate-400'
                    }`}
                  >
                    {step.label}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Terminal Console */}
        <div className="lg:col-span-3">
          <div className="bg-slate-900 rounded-xl overflow-hidden shadow-lg">
            {/* Terminal Header */}
            <div className="flex items-center justify-between px-4 py-2.5 bg-slate-800 border-b border-slate-700">
              <div className="flex items-center gap-2">
                <TerminalIcon className="w-4 h-4 text-slate-400" />
                <span className="text-xs font-mono text-slate-300 uppercase tracking-wide">
                  Session: {sessionId || 'Waiting...'}
                </span>
              </div>
              <div className="flex gap-1.5">
                <div className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-slate-600'}`} />
                <div className="w-3 h-3 rounded-full bg-slate-600" />
                <div className="w-3 h-3 rounded-full bg-slate-600" />
              </div>
            </div>

            {/* Terminal Body */}
            <div
              ref={consoleRef}
              className="p-4 h-80 overflow-y-auto font-mono text-xs leading-relaxed scroll-smooth"
            >
              {messages.length === 0 && !isRunning && (
                <div className="text-slate-500 italic">
                  Press "Start Analysis" to begin...
                </div>
              )}
              {messages.map((msg, idx) => (
                <div key={msg.id} className="flex">
                  <span className="w-8 text-right text-slate-600 mr-3 select-none flex-shrink-0">
                    {idx + 1}
                  </span>
                  <span className={getMessageColor(msg.type)}>{msg.text}</span>
                </div>
              ))}
              {isRunning && (
                <div className="flex items-center gap-2 mt-2 text-brand-red">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  <span className="text-xs">Processing...</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function TerminalIcon(props: React.SVGProps<SVGSVGElement> & { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      {...props}>
      <polyline points="4 17 10 11 4 5" />
      <line x1="12" y1="19" x2="20" y2="19" />
    </svg>
  );
}
