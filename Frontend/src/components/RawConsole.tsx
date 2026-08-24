import { useState, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Copy, Check, Search } from 'lucide-react';
import { getWorkflowConsole } from '../services/api';
import { useAnalysis } from '../contexts/AnalysisContext';

function classifyLine(line: string): string {
  const trimmed = line.trim();
  if (/^={3,}/.test(trimmed)) return 'text-slate-500';
  if (/^---/.test(trimmed)) return 'text-slate-400';
  if (/^\[Agent thinks\]/.test(trimmed) || /\[Agent\]/.test(trimmed)) return 'text-cyan-400 italic';
  if (/\[Tool call\]/.test(trimmed)) return 'text-amber-400';
  if (/\[Tool result\]/.test(trimmed) || /\[Tool\]/.test(trimmed)) return 'text-emerald-400';
  if (/\[Success\]/.test(trimmed)) return 'text-emerald-400 font-medium';
  if (/\[Error\]/.test(trimmed) || /\[Warning\]/.test(trimmed)) return 'text-red-400';
  if (/=== Phase/.test(trimmed)) return 'text-blue-300 font-semibold';
  if (/Phase \d/.test(trimmed)) return 'text-blue-300';
  if (/^\s*(Problem|Max iterations|Token|Compression|Total)/.test(trimmed)) return 'text-slate-300';
  if (/^\s*\d+\.\s/.test(trimmed) || /^\[.+\]/.test(trimmed)) return 'text-purple-300';
  if (trimmed.startsWith('AGENTIC') || trimmed.startsWith('LLM API')) return 'text-white font-semibold';
  return 'text-slate-400';
}

export default function RawConsole() {
  const { isRunning, sessionId, setSessionId } = useAnalysis();
  const [searchParams] = useSearchParams();
  const effectiveSession = searchParams.get('session') || sessionId || null;
  const [content, setContent] = useState('');
  const [search, setSearch] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const urlSession = searchParams.get('session');
    if (urlSession && urlSession !== sessionId) {
      setSessionId(urlSession);
    }
  }, [searchParams, sessionId, setSessionId]);

  useEffect(() => {
    getWorkflowConsole(effectiveSession).then(setContent);
    if (!isRunning) return;
    const interval = setInterval(() => {
      getWorkflowConsole(effectiveSession).then(setContent);
    }, 3000);
    return () => clearInterval(interval);
  }, [isRunning, effectiveSession]);

  const lines = useMemo(() => content.split('\n'), [content]);

  const filteredLines = useMemo(() => {
    if (!search.trim()) return lines.map((line, i) => ({ line, num: i + 1 }));
    const lower = search.toLowerCase();
    return lines
      .map((line, i) => ({ line, num: i + 1 }))
      .filter(({ line }) => line.toLowerCase().includes(lower));
  }, [lines, search]);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Raw Agent Output</h1>
          <p className="text-slate-500 text-sm mt-1">
            {isRunning
              ? 'Live stream of the backend analysis workflow.'
              : 'Output from the last analysis workflow.'}
          </p>
        </div>
        <button
          onClick={handleCopy}
          className="flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-300
                     text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
        >
          {copied ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4" />}
          {copied ? 'Copied!' : 'Copy Output'}
        </button>
      </div>

      {/* Search Bar */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search console output..."
          className="w-full pl-10 pr-4 py-2.5 rounded-lg border border-slate-300 text-sm
                     focus:ring-2 focus:ring-brand-red/30 focus:border-brand-red
                     placeholder:text-slate-400 transition-colors"
        />
        {search && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400">
            {filteredLines.length} matches
          </span>
        )}
      </div>

      {/* Terminal */}
      <div className="bg-slate-900 rounded-xl overflow-hidden shadow-lg">
        {/* Terminal Header */}
        <div className="flex items-center justify-between px-4 py-2.5 bg-slate-800 border-b border-slate-700">
          <div className="flex items-center gap-2">
            <TerminalIcon className="w-4 h-4 text-slate-400" />
            <span className="text-xs font-mono text-slate-300 uppercase tracking-wide">
              System Log
            </span>
          </div>
          <div className="flex gap-1.5">
            <div className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-slate-600'}`} />
            <div className="w-3 h-3 rounded-full bg-slate-600" />
            <div className="w-3 h-3 rounded-full bg-slate-600" />
          </div>
        </div>

        {/* Terminal Body */}
        <div className="p-4 max-h-[600px] overflow-y-auto font-mono text-xs leading-relaxed">
          {filteredLines.map(({ line, num }) => {
            const highlighted =
              search.trim() && line.toLowerCase().includes(search.toLowerCase());
            return (
              <div
                key={num}
                className={`flex ${highlighted ? 'bg-amber-900/20' : 'hover:bg-slate-800/50'}`}
              >
                <span className="w-10 text-right text-slate-600 mr-4 select-none flex-shrink-0 py-px">
                  {num}
                </span>
                <span className={`py-px ${classifyLine(line)}`}>
                  {search.trim() ? highlightMatch(line, search) : line || '\u00A0'}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function highlightMatch(text: string, query: string): React.ReactNode {
  if (!query.trim()) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-amber-400/30 text-amber-200 rounded px-0.5">
        {text.slice(idx, idx + query.length)}
      </mark>
      {text.slice(idx + query.length)}
    </>
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
