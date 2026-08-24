import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Shield, Loader2, Layers, FileSearch } from 'lucide-react';
import { useAnalysis } from '../contexts/AnalysisContext';
import { useAgentMemory } from '../hooks/useAgentMemory';
import { getRCAStages } from '../services/api';
import RCADetailTables from './RCADetailTables';
import RCADiagrams from './FishboneDiagram';

type StageEntry = { stage: 'Tier_1' | 'Tier_2' | 'Final'; text: string };

const STAGE_DEFS: Record<string, { short: string; label: string }> = {
  Tier_1: { short: 'T1', label: 'Tier 1' },
  Tier_2: { short: 'T2', label: 'Tier 2' },
  Final:  { short: 'F',  label: 'Final' },
};

export default function LoggingTracing() {
  const { isRunning, sessionId, setSessionId } = useAnalysis();
  const [searchParams] = useSearchParams();
  const effectiveSession = searchParams.get('session') || sessionId || null;
  const { latestSession } = useAgentMemory(isRunning, effectiveSession);
  const [stageReports, setStageReports] = useState<StageEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => {
    const urlSession = searchParams.get('session');
    if (urlSession && urlSession !== sessionId) {
      setSessionId(urlSession);
    }
  }, [searchParams, sessionId, setSessionId]);

  useEffect(() => {
    if (isRunning) { setStageReports([]); setLoading(false); return; }
    setLoading(true);
    getRCAStages(effectiveSession)
      .then(stages => { setStageReports(stages); setActiveIdx(stages.length - 1); })
      .catch(() => setStageReports([]))
      .finally(() => setLoading(false));
  }, [isRunning, effectiveSession]);

  if (isRunning) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <Loader2 className="w-16 h-16 mx-auto mb-4 opacity-30 animate-spin text-brand-red" />
          <p className="text-lg font-medium">Analysis in progress</p>
          <p className="text-sm">Logging and tracing data will appear once the analysis completes.</p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <Loader2 className="w-12 h-12 mx-auto mb-4 animate-spin text-slate-300" />
          <p className="text-sm">Loading logging data...</p>
        </div>
      </div>
    );
  }

  const suggestedFiles = latestSession?.results?.file_suggestions?.suggested_files ?? [];
  const fileAvailability = latestSession?.results?.file_suggestions?.file_availability;
  const hasRcaContent = stageReports.length > 0;
  const hasSourceRef = suggestedFiles.length > 0 || fileAvailability;

  if (!hasRcaContent && !hasSourceRef) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <Shield className="w-16 h-16 mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium">No logging data yet</p>
          <p className="text-sm">Run an analysis to generate aggregated error patterns and source references.</p>
        </div>
      </div>
    );
  }

  const safeIdx = Math.min(activeIdx, stageReports.length - 1);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-800">Logging / Tracing</h1>
        <p className="text-slate-500 text-sm mt-1">
          Aggregated error patterns, analysis coverage, and source references for log and trace investigation.
        </p>
      </div>

      <div className="h-1.5 w-full bg-slate-600 rounded-full" />

      {/* Stage selector when we have RCA content */}
      {stageReports.length > 1 && (
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex items-center gap-1.5 text-xs text-slate-500 mr-1">
            <Layers className="w-3.5 h-3.5" />
            <span className="font-medium">Analysis tiers</span>
          </div>
          {stageReports.map((r, i) => {
            const def = STAGE_DEFS[r.stage] ?? { short: r.stage[0], label: r.stage };
            return (
              <button
                key={r.stage}
                onClick={() => setActiveIdx(i)}
                className={`flex items-center gap-2 px-3.5 py-1.5 rounded-full text-sm font-medium
                            border transition-all duration-200
                            ${safeIdx === i
                              ? 'bg-slate-600 text-white border-slate-600 shadow-sm'
                              : 'bg-white text-slate-600 border-slate-200 hover:border-slate-400 hover:text-slate-800'
                            }`}
              >
                <span
                  className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold
                              ${safeIdx === i ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-500'}`}
                >
                  {def.short}
                </span>
                {def.label}
              </button>
            );
          })}
        </div>
      )}

      {/* Source Reference / Indexing — long list of file paths from session */}
      {hasSourceRef && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <div className="flex items-center gap-2 px-6 py-4 border-b border-slate-100 bg-slate-50/60">
            <FileSearch className="w-4.5 h-4.5 text-brand-red" />
            <h2 className="text-[15px] font-semibold text-slate-800">Source Reference / Indexing</h2>
          </div>
          <div className="p-5 space-y-6">
            {suggestedFiles.length > 0 && (
              <div>
                <h3 className="text-[13px] font-semibold text-slate-700 mb-2">Shortlisted Files</h3>
                <div className="max-h-64 overflow-y-auto rounded-lg border border-slate-200">
                  <table className="w-full text-left text-[12.5px]">
                    <thead className="bg-slate-50 border-b border-slate-200 sticky top-0">
                      <tr>
                        <th className="px-3 py-2 font-semibold text-slate-600 text-[10.5px] w-24">Priority</th>
                        <th className="px-3 py-2 font-semibold text-slate-600 text-[10.5px]">Path</th>
                        <th className="px-3 py-2 font-semibold text-slate-600 text-[10.5px]">Reason</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {suggestedFiles.map((f, i) => (
                        <tr key={i} className="hover:bg-slate-50/60">
                          <td className="px-3 py-2">
                            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full
                              ${f.priority === 'high' ? 'bg-red-50 text-red-700' : ''}
                              ${f.priority === 'medium' ? 'bg-amber-50 text-amber-700' : ''}
                              ${f.priority === 'low' ? 'bg-slate-100 text-slate-600' : ''}`}>
                              {f.priority}
                            </span>
                          </td>
                          <td className="px-3 py-2 font-mono text-[11px] text-slate-700 break-all">{f.path}</td>
                          <td className="px-3 py-2 text-slate-600 text-[12px]">{f.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            {fileAvailability && (
              <div>
                <h3 className="text-[13px] font-semibold text-slate-700 mb-2">File Availability</h3>
                <p className="text-[12px] text-slate-600 mb-2">{fileAvailability.summary_text}</p>
                {fileAvailability.not_found.length > 0 && (
                  <div>
                    <span className="text-[11px] font-medium text-slate-500">Not found:</span>
                    <ul className="mt-1 max-h-32 overflow-y-auto text-[11px] font-mono text-slate-600 space-y-0.5">
                      {fileAvailability.not_found.map((p, i) => (
                        <li key={i} className="break-all">• {p}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Cause-Effect Diagram & Timeline — causes from High stage, timeline merged across stages */}
      {hasRcaContent && (
        <RCADiagrams
          rcaText={stageReports[safeIdx]?.text ?? ''}
          allTexts={stageReports.map(r => r.text)}
          activeStageIdx={safeIdx}
        />
      )}

      {/* Aggregated Error Patterns + Analysis Coverage from RCA text */}
      {hasRcaContent && (
        <RCADetailTables
          rcaText={stageReports[safeIdx]?.text ?? ''}
          showErrorPatterns
          showResolutions={false}
          showCoverage
          showCost={false}
        />
      )}
    </div>
  );
}
