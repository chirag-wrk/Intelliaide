import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Shield, Loader2, Layers } from 'lucide-react';
import { useAnalysis } from '../contexts/AnalysisContext';
import { getRCAStages } from '../services/api';
import RCADetailTables from './RCADetailTables';

type StageEntry = { stage: 'Tier_1' | 'Tier_2' | 'Final'; text: string };

const STAGE_DEFS: Record<string, { short: string; label: string }> = {
  Tier_1: { short: 'T1', label: 'Tier 1' },
  Tier_2: { short: 'T2', label: 'Tier 2' },
  Final:  { short: 'F',  label: 'Final' },
};

export default function Remediation() {
  const { isRunning, sessionId, setSessionId } = useAnalysis();
  const [searchParams] = useSearchParams();
  const effectiveSession = searchParams.get('session') || sessionId || null;
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
          <p className="text-sm">Resolution steps will appear once the analysis completes.</p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <Loader2 className="w-12 h-12 mx-auto mb-4 animate-spin text-slate-300" />
          <p className="text-sm">Loading resolution...</p>
        </div>
      </div>
    );
  }

  if (stageReports.length === 0) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <Shield className="w-16 h-16 mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium">No resolution yet</p>
          <p className="text-sm">Run an analysis to generate resolution steps.</p>
        </div>
      </div>
    );
  }

  const safeIdx = Math.min(activeIdx, stageReports.length - 1);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-800">Resolution</h1>
        <p className="text-slate-500 text-sm mt-1">
          Resolution steps extracted from each RCA stage, sorted by priority.
        </p>
      </div>

      <div className="h-1.5 w-full bg-brand-red rounded-full" />

      {/* Stage selector */}
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
                              ? 'bg-brand-red text-white border-brand-red shadow-sm'
                              : 'bg-white text-slate-600 border-slate-200 hover:border-brand-red/50 hover:text-brand-red'
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

      {/* Remediation content */}
      <RCADetailTables
        rcaText={stageReports[safeIdx]?.text ?? ''}
        showErrorPatterns={false}
        showResolutions
        showCoverage={false}
        showCost={false}
      />
    </div>
  );
}
