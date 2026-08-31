import { useState, useEffect, useRef } from 'react';
import { Shield, Loader2, Calendar, FileText, AlertCircle, BookOpen, Download } from 'lucide-react';
import { useAnalysis } from '../contexts/AnalysisContext';
import { useAgentMemory } from '../hooks/useAgentMemory';
import { useEffectiveSession } from '../hooks/useEffectiveSession';
import { getRCAReportSummary, getRCAStages } from '../services/api';
import { getRcaCache, setRcaStagesCache, setRcaSummaryCache } from '../services/rcaCache';
import { exportAsDoc } from '../utils/exportUtils';
import RCADetailTables from './RCADetailTables';

interface ParsedSummary {
  issue: string;
  summary: string;
}

function parseSummary(raw: string): ParsedSummary {
  const sections: Record<string, string> = {};
  const lines = raw.split('\n');
  let current = '';
  let buffer: string[] = [];

  for (const line of lines) {
    const heading = line.match(/^##\s+(.+)/);
    if (heading) {
      if (current) sections[current.toLowerCase()] = buffer.join('\n').trim();
      current = heading[1].trim();
      buffer = [];
    } else {
      buffer.push(line);
    }
  }
  if (current) sections[current.toLowerCase()] = buffer.join('\n').trim();

  return {
    issue: sections['issue'] || '',
    summary: sections['summary'] || '',
  };
}

export default function RCAExecutiveSummary() {
  const { isRunning } = useAnalysis();
  const { effectiveSession: reportSession } = useEffectiveSession();
  const { latestSession } = useAgentMemory(isRunning, reportSession);
  const cached = getRcaCache(reportSession);
  const [rawText, setRawText] = useState(cached?.summaryText ?? '');
  const [latestRcaText, setLatestRcaText] = useState(
    cached?.stages.length ? cached.stages[cached.stages.length - 1].text : '',
  );
  const [loading, setLoading] = useState(!cached?.summaryText && !cached?.stages.length);
  const prevRunningRef = useRef(isRunning);

  const loadData = async () => {
    const existing = getRcaCache(reportSession);
    if (existing?.summaryText || existing?.stages.length) {
      setRawText(existing.summaryText);
      if (existing.stages.length > 0) {
        setLatestRcaText(existing.stages[existing.stages.length - 1].text);
      }
      setLoading(false);
    } else {
      setLoading(true);
    }

    try {
      const [summaryText, stages] = await Promise.all([
        getRCAReportSummary(reportSession),
        getRCAStages(reportSession),
      ]);
      setRawText(summaryText);
      setRcaSummaryCache(reportSession, summaryText);
      if (stages.length > 0) {
        setLatestRcaText(stages[stages.length - 1].text);
        setRcaStagesCache(reportSession, stages);
      }
    } catch {
      const fallback = getRcaCache(reportSession);
      if (fallback) {
        setRawText(fallback.summaryText);
        if (fallback.stages.length > 0) {
          setLatestRcaText(fallback.stages[fallback.stages.length - 1].text);
        }
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const wasRunning = prevRunningRef.current;
    prevRunningRef.current = isRunning;

    if (isRunning) {
      setRawText('');
      setLatestRcaText('');
      setLoading(false);
      return;
    }

    if (wasRunning || !isRunning) {
      loadData();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRunning, reportSession]);

  if (isRunning) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <Loader2 className="w-16 h-16 mx-auto mb-4 opacity-30 animate-spin text-brand-red" />
          <p className="text-lg font-medium">Analysis in progress</p>
          <p className="text-sm">The RCA report will be available once the analysis is complete.</p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <Loader2 className="w-12 h-12 mx-auto mb-4 animate-spin text-slate-300" />
          <p className="text-sm">Loading report...</p>
        </div>
      </div>
    );
  }

  if (!rawText && !latestRcaText) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <Shield className="w-16 h-16 mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium">No RCA report available</p>
          <p className="text-sm">Run an analysis first to generate a Root Cause Analysis report.</p>
        </div>
      </div>
    );
  }

  const createdDate = latestSession
    ? new Date(latestSession.created_at).toLocaleDateString('en-US', {
        month: 'numeric', day: 'numeric', year: 'numeric',
      })
    : new Date().toLocaleDateString('en-US', {
        month: 'numeric', day: 'numeric', year: 'numeric',
      });

  const parsed = parseSummary(rawText);
  const hasSections = parsed.issue || parsed.summary;

  const handleExportDoc = () => exportAsDoc('rca-summary-export', 'RCA_Summary.doc', 'RCA Summary');

  return (
    <div className="space-y-6" id="rca-summary-export">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">RCA Summary</h1>
          <div className="flex items-center gap-2 text-sm text-slate-500 mt-1">
            <Calendar className="w-4 h-4" />
            Generated {createdDate}
          </div>
        </div>
        <button
          onClick={handleExportDoc}
          className="no-export flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-300
                     text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
        >
          <Download className="w-4 h-4" />
          Export DOC
        </button>
      </div>

      {/* Progress accent */}
      <div className="h-1.5 w-full bg-brand-red rounded-full" />

      {hasSections ? (
        <div className="space-y-5">
          {/* Issue */}
          {parsed.issue && (
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
              <div className="flex items-start gap-3 mb-4">
                <div className="w-9 h-9 rounded-lg bg-red-50 flex items-center justify-center flex-shrink-0">
                  <AlertCircle className="w-4.5 h-4.5 text-brand-red" />
                </div>
                <div>
                  <h2 className="text-base font-semibold text-slate-800">Issue</h2>
                  <p className="text-xs text-slate-400 mt-0.5">User reported problem</p>
                </div>
              </div>
              <div className="text-slate-600 leading-relaxed text-[15px] whitespace-pre-line pl-12">
                {parsed.issue}
              </div>
            </div>
          )}

          {/* Resolution — from the actual detailed RCA, same as Resolution tab */}
          {latestRcaText && (
            <RCADetailTables
              rcaText={latestRcaText}
              showErrorPatterns={false}
              showResolutions
              showCoverage={false}
              showCost={false}
            />
          )}

          {/* Summary */}
          {parsed.summary && (
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
              <div className="flex items-start gap-3 mb-4">
                <div className="w-9 h-9 rounded-lg bg-brand-red/10 flex items-center justify-center flex-shrink-0">
                  <BookOpen className="w-4.5 h-4.5 text-brand-red" />
                </div>
                <div>
                  <h2 className="text-base font-semibold text-slate-800">Summary</h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Root cause analysis overview · <a href={`/rca-report${reportSession ? `?session=${encodeURIComponent(reportSession)}` : ''}`} className="text-brand-red hover:underline">View detailed report</a>
                  </p>
                </div>
              </div>
              <div className="text-slate-600 leading-relaxed text-[15px] whitespace-pre-line pl-12">
                {parsed.summary}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-8">
          <div className="flex items-start gap-3 mb-6">
            <div className="w-10 h-10 rounded-lg bg-brand-red/10 flex items-center justify-center flex-shrink-0">
              <FileText className="w-5 h-5 text-brand-red" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-800">RCA Summary</h2>
              <p className="text-xs text-slate-400 mt-0.5">
                AI-generated summary · <a href={`/rca-report${reportSession ? `?session=${encodeURIComponent(reportSession)}` : ''}`} className="text-brand-red hover:underline">View detailed report</a>
              </p>
            </div>
          </div>
          <div className="text-slate-600 leading-relaxed text-[15px] whitespace-pre-line">
            {rawText}
          </div>
        </div>
      )}

      {/* Footer hint */}
      <div className="text-center text-xs text-slate-400 py-2">
        For the full analysis with detailed sections, chronology, and error patterns, visit{' '}
        <a href={`/rca-report${reportSession ? `?session=${encodeURIComponent(reportSession)}` : ''}`} className="text-brand-red hover:underline font-medium">RCA Detailed</a>.
      </div>
    </div>
  );
}
