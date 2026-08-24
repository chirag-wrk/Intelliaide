import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts';
import { FileText, HardDrive, ScrollText, RefreshCw, Zap } from 'lucide-react';
import { useAgentMemory } from '../hooks/useAgentMemory';
import { useAnalysis } from '../contexts/AnalysisContext';
import type { SessionResults, RCAResult } from '../types';

const COLORS = {
  teal: '#EE0000',
  tealDark: '#C00000',
  navy: '#1e3a5f',
  orange: '#ea580c',
  purple: '#7c3aed',
  green: '#16a34a',
  blue: '#2563eb',
  slate: '#64748b',
  red: '#dc2626',
  amber: '#d97706',
};

const PIE_COLORS = [COLORS.teal, COLORS.navy, COLORS.orange, COLORS.purple, COLORS.green, COLORS.blue];

function StatCard({ icon: Icon, label, value, sub, color }: {
  icon: React.ElementType; label: string; value: string | number; sub?: string; color: string;
}) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5 flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${color}`}>
        <Icon className="w-6 h-6" />
      </div>
      <div>
        <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">{label}</div>
        <div className="text-2xl font-bold text-slate-800 mt-0.5">{value}</div>
        {sub && <div className="text-xs text-slate-400">{sub}</div>}
      </div>
    </div>
  );
}

export default function Analytics() {
  const { isRunning, sessionId, setSessionId } = useAnalysis();
  const [searchParams] = useSearchParams();
  const effectiveSession = searchParams.get('session') || sessionId || null;
  const { latestSession: session, loading: memLoading, version } = useAgentMemory(isRunning, effectiveSession);

  useEffect(() => {
    const urlSession = searchParams.get('session');
    if (urlSession && urlSession !== sessionId) {
      setSessionId(urlSession);
    }
  }, [searchParams, sessionId, setSessionId]);

  const data = useMemo(() => {
    if (!session?.results) return null;
    const r = session.results;
    const fs = r.file_suggestions;
    const da = r.data_analysis;
    if (!fs || !da) return null;

    const suggestedFiles = fs.suggested_files || [];
    const classTable = da.classification_table || [];
    const yamlSizes = da.yaml_file_sizes || {};
    const logEntries: { file?: string; original_size?: number }[] = (r as any).log_error_entries || (da as any).log_payload || [];
    const fileAvail = fs.file_availability || { found_in_supplied_dir: [], found_elsewhere: [], not_found: [] };

    // Files analyzed = resolved paths (files actually found in the bundle)
    const resolvedCount =
      (fileAvail.found_in_supplied_dir?.length ?? 0) +
      (fileAvail.found_elsewhere?.length ?? 0);

    // Priority distribution
    const priorityCounts: Record<string, number> = { high: 0, medium: 0, low: 0 };
    suggestedFiles.forEach(f => { if (f.priority in priorityCounts) priorityCounts[f.priority]++; });
    const priorityData = [
      { name: 'Tier 1', value: priorityCounts.high, color: COLORS.red },
      { name: 'Tier 2', value: priorityCounts.medium, color: COLORS.amber },
      { name: 'Final', value: priorityCounts.low, color: COLORS.green },
    ];

    // Classification distribution
    const classMap: Record<string, number> = {};
    classTable.forEach(row => {
      classMap[row.Classification] = (classMap[row.Classification] || 0) + 1;
    });
    const classData = Object.entries(classMap).map(([name, value]) => ({ name, value }));

    // File sizes for bar chart
    const fileSizeData = Object.entries(yamlSizes)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 8)
      .map(([name, size]) => ({ name: name.length > 14 ? name.slice(0, 14) + '…' : name, size: Math.round(size / 1024 * 10) / 10 }));

    // Components from file paths
    const componentMap: Record<string, { total: number; found: number; notFound: number }> = {};
    suggestedFiles.forEach(f => {
      const parts = f.path.split('/');
      const component = parts[0] === 'namespaces' ? (parts[1] || parts[0]) : parts[0];
      if (!componentMap[component]) componentMap[component] = { total: 0, found: 0, notFound: 0 };
      componentMap[component].total++;
    });
    // Count both found_in_supplied_dir AND found_elsewhere as "Found"
    [...(fileAvail.found_in_supplied_dir || []), ...(fileAvail.found_elsewhere || [])].forEach(f => {
      const parts = f.original.split('/');
      const component = parts[0] === 'namespaces' ? (parts[1] || parts[0]) : parts[0];
      if (componentMap[component]) componentMap[component].found++;
    });
    (fileAvail.not_found || []).forEach(f => {
      const parts = f.split('/');
      const component = parts[0] === 'namespaces' ? (parts[1] || parts[0]) : parts[0];
      if (componentMap[component]) componentMap[component].notFound++;
    });
    const componentData = Object.entries(componentMap).map(([name, v]) => ({
      name: name.length > 16 ? name.slice(0, 16) + '…' : name,
      Total: v.total,
      Found: v.found,
      'Not Found': v.notFound,
    }));

    // Compression — prefer cumulative totals stored by orchestrator, fall back to data_analysis
    const totalLogBytes = r.total_log_bytes
      ?? (Array.isArray(logEntries)
        ? logEntries.reduce((s, l) => s + (l.original_size || 0), 0)
        : 0);
    const totalInput = (r.total_yaml_bytes ?? da.total_yaml_bytes ?? 0) + totalLogBytes;
    const payloadBytes = r.rca_result?.payload_bytes || 0;
    const compressionRatio = totalInput > 0 && payloadBytes > 0 ? (totalInput / payloadBytes).toFixed(1) : '0';

    // Log bytes — prefer cumulative top-level value stored by orchestrator
    const logProcessedKB = Math.round((r.total_log_bytes ?? totalLogBytes) / 1024 * 10) / 10;

    return {
      priorityData,
      classData,
      fileSizeData,
      componentData,
      totalFiles: resolvedCount,
      yamlProcessedKB: Math.round((da.total_yaml_bytes || 0) / 1024 * 10) / 10,
      logProcessedKB,
      agentCycles: r.agent_iteration_count || 0,
      compressionRatio,
      totalInput,
      payloadBytes,
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, version]);

  if (memLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <BarChartPlaceholderIcon className="w-16 h-16 mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium">Loading analytics data...</p>
        </div>
      </div>
    );
  }

  if (!session || !data) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center text-slate-400">
          <BarChartPlaceholderIcon className="w-16 h-16 mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium">No analysis data yet</p>
          <p className="text-sm">Run an analysis from the Live Analysis page to see insights.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-800">Analytics Dashboard</h1>
        <p className="text-slate-500 text-sm mt-1">
          Insights derived from diagnostic bundles and logs.
        </p>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={FileText} label="Files Analyzed" value={data.totalFiles} color="bg-red-50 text-red-600" />
        <StatCard icon={HardDrive} label="YAML Processed" value={`${data.yamlProcessedKB}`} sub="KB" color="bg-blue-50 text-blue-600" />
        <StatCard icon={ScrollText} label="Logs Processed" value={`${data.logProcessedKB}`} sub="KB" color="bg-teal-50 text-teal-600" />
        <StatCard icon={RefreshCw} label="Agent Cycles" value={data.agentCycles} color="bg-purple-50 text-purple-600" />
      </div>

      {/* Charts Row 1 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* YAML ML Classification Distribution */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h3 className="text-base font-semibold text-slate-800 mb-1">YAML Classification Distribution</h3>
          <p className="text-xs text-slate-500 mb-4">ML classification of YAML objects in analyzed files (Error / Majority Error / CONFIG / Majority)</p>
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie
                data={data.classData}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={100}
                paddingAngle={3}
                dataKey="value"
                stroke="none"
              >
                {data.classData.map((_, i) => (
                  <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ backgroundColor: '#1e293b', border: 'none', borderRadius: '8px', color: '#fff', fontSize: '12px' }}
              />
              <Legend
                verticalAlign="bottom"
                formatter={(value: string) => <span className="text-xs text-slate-600">{value}</span>}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* File Sizes */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h3 className="text-base font-semibold text-slate-800 mb-1">File Sizes (Top 8)</h3>
          <p className="text-xs text-slate-500 mb-4">Size distribution of processed configuration files (KB)</p>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={data.fileSizeData} margin={{ left: 0, right: 10, top: 5, bottom: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="name" tick={{ fontSize: 9, fill: '#64748b' }} interval={0} angle={-25} textAnchor="end" height={55} />
              <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
              <Tooltip
                contentStyle={{ backgroundColor: '#1e293b', border: 'none', borderRadius: '8px', color: '#fff', fontSize: '12px' }}
              />
              <Bar dataKey="size" fill={COLORS.teal} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Charts Row 2 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Priority Distribution */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h3 className="text-base font-semibold text-slate-800 mb-1">File Priority Distribution (Suggested)</h3>
          <p className="text-xs text-slate-500 mb-4">All suggested files by tier — first pass analyzes Tier 1 only; Tier 2/Final require feedback deepening</p>
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie
                data={data.priorityData}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={100}
                paddingAngle={3}
                dataKey="value"
                stroke="none"
                label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
              >
                {data.priorityData.map((entry, i) => (
                  <Cell key={i} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ backgroundColor: '#1e293b', border: 'none', borderRadius: '8px', color: '#fff', fontSize: '12px' }}
              />
              <Legend
                verticalAlign="bottom"
                formatter={(value: string) => <span className="text-xs text-slate-600">{value}</span>}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Components Bar Chart */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <h3 className="text-base font-semibold text-slate-800 mb-1">Files by Component</h3>
          <p className="text-xs text-slate-500 mb-4">File availability grouped by OpenShift component</p>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={data.componentData} margin={{ left: 0, right: 10, top: 5, bottom: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="name" tick={{ fontSize: 8, fill: '#64748b' }} interval={0} angle={-30} textAnchor="end" height={70} />
              <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
              <Tooltip
                contentStyle={{ backgroundColor: '#1e293b', border: 'none', borderRadius: '8px', color: '#fff', fontSize: '12px' }}
              />
              <Legend formatter={(v: string) => <span className="text-xs text-slate-600">{v}</span>} />
              <Bar dataKey="Total" fill={COLORS.blue} radius={[3, 3, 0, 0]} />
              <Bar dataKey="Found" fill={COLORS.teal} radius={[3, 3, 0, 0]} />
              <Bar dataKey="Not Found" fill={COLORS.orange} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Cost Table with stage tabs */}
      {session?.results && <CostSection results={session.results} />}
    </div>
  );
}

// ── Cost Table Section with stage tabs ────────────────────────────────

type CostStage = 'total' | 'Tier1' | 'Tier2' | 'Final';

const STAGE_TAB_DEFS: { key: CostStage; short: string; label: string }[] = [
  { key: 'total', short: 'Σ',  label: 'Total (cumulative)' },
  { key: 'Tier1', short: 'T1', label: 'Tier 1' },
  { key: 'Tier2', short: 'T2', label: 'Tier 2' },
  { key: 'Final', short: 'F',  label: 'Final' },
];

function tok(n: number) { return n > 0 ? n.toLocaleString() : '—'; }
function usd(n: number) { return n > 0 ? `$${n.toFixed(6)}` : '—'; }

interface CostRow { phase: string; inTok: number; outTok: number; cost: number; dim?: boolean }

function buildTotalRows(r: SessionResults): CostRow[] {
  const fsIn = r.file_suggestions?.input_tokens ?? 0;
  const fsOut = r.file_suggestions?.output_tokens ?? 0;
  const orchIn = r.agent_input_tokens ?? r.agent_total_tokens ?? 0;
  const orchOut = r.agent_output_tokens ?? 0;
  const rcaIn = r.rca_result?.input_tokens ?? 0;
  const rcaOut = r.rca_result?.output_tokens ?? 0;
  const rcaCost = r.rca_result?.cost_usd ?? 0;
  // Use backend pre-computed costs when available (no frontend price multiplication needed)
  const fsCost = r.file_selection_cost_usd ?? 0;
  const orchCost = r.orchestrator_cost_usd ?? 0;
  const iterLabel = r.agent_iteration_count
    ? `0. Orchestrator ReAct loop (${r.agent_iteration_count} iter.)`
    : '0. Orchestrator ReAct loop';
  return [
    { phase: iterLabel, inTok: orchIn, outTok: orchOut, cost: orchCost },
    { phase: '1. File selection', inTok: fsIn, outTok: fsOut, cost: fsCost },
    { phase: '2. YAML processing (local ML)', inTok: 0, outTok: 0, cost: 0, dim: true },
    { phase: '3. Log processing (local Drain3)', inTok: 0, outTok: 0, cost: 0, dim: true },
    { phase: '4. Data aggregation (no LLM)', inTok: 0, outTok: 0, cost: 0, dim: true },
    { phase: '5. RCA (extracted payload)', inTok: rcaIn, outTok: rcaOut, cost: rcaCost },
  ];
}

function buildStageRows(stageCost: RCAResult, stageName: string): CostRow[] {
  const orchIn = stageCost.orchestrator_input_tokens ?? 0;
  const orchOut = stageCost.orchestrator_output_tokens ?? 0;
  const orchCost = stageCost.orchestrator_cost_usd ?? 0;
  const fsIn = stageCost.file_selection_input_tokens ?? 0;
  const fsOut = stageCost.file_selection_output_tokens ?? 0;
  const fsCost = stageCost.file_selection_cost_usd ?? 0;
  return [
    { phase: `0. Orchestrator ReAct loop (${stageName} stage delta)`, inTok: orchIn, outTok: orchOut, cost: orchCost },
    { phase: `1. File selection (${stageName} stage delta)`, inTok: fsIn, outTok: fsOut, cost: fsCost },
    { phase: '2. YAML processing (local ML)', inTok: 0, outTok: 0, cost: 0, dim: true },
    { phase: '3. Log processing (local Drain3)', inTok: 0, outTok: 0, cost: 0, dim: true },
    { phase: '4. Data aggregation (no LLM)', inTok: 0, outTok: 0, cost: 0, dim: true },
    { phase: `5. RCA (${stageName} pass only)`, inTok: stageCost.input_tokens, outTok: stageCost.output_tokens, cost: stageCost.cost_usd },
  ];
}

function CostTableUI({ rows, totalCost }: { rows: CostRow[]; totalCost: number }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200">
            {['Phase', 'Input Tokens', 'Output Tokens', 'Cost (USD)'].map(h => (
              <th key={h} className="px-4 py-2.5 text-left font-semibold text-slate-500 uppercase tracking-wide text-[10.5px] whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-slate-100">
              <td className={`px-4 py-2 ${r.dim ? 'text-slate-400' : 'text-slate-700'}`}>{r.phase}</td>
              <td className={`px-4 py-2 text-right font-mono ${r.dim ? 'text-slate-300' : 'text-slate-600'}`}>{tok(r.inTok)}</td>
              <td className={`px-4 py-2 text-right font-mono ${r.dim ? 'text-slate-300' : 'text-slate-600'}`}>{tok(r.outTok)}</td>
              <td className={`px-4 py-2 text-right font-mono ${r.dim ? 'text-slate-300' : 'text-brand-red font-semibold'}`}>{r.dim ? '—' : usd(r.cost)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="bg-slate-50">
            <td className="px-4 py-2.5 font-semibold text-slate-700">Total</td>
            <td colSpan={2} />
            <td className="px-4 py-2.5 text-right font-bold text-brand-red font-mono">{usd(totalCost)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function CostSection({ results }: { results: SessionResults }) {
  const perStage = results.rca_costs_per_stage ?? {};
  const availableStages = (['Tier1', 'Tier2', 'Final'] as const).filter(s => s in perStage);

  const tabs = useMemo(() => {
    const t: { key: CostStage; short: string; label: string }[] = [STAGE_TAB_DEFS[0]];
    for (const s of availableStages) {
      const def = STAGE_TAB_DEFS.find(d => d.key === s);
      if (def) t.push(def);
    }
    return t;
  }, [availableStages.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const [active, setActive] = useState<CostStage>('total');

  const hasCost = (results.rca_result?.cost_usd ?? 0) > 0
    || (results.file_suggestions?.input_tokens ?? 0) > 0
    || (results.agent_input_tokens ?? results.agent_total_tokens ?? 0) > 0;

  if (!hasCost) return null;

  const totalRows = buildTotalRows(results);
  // Use backend pre-computed total when available, fall back to summing rows
  const totalCost = results.total_cost_usd ?? totalRows.reduce((s, r) => s + r.cost, 0);

  let displayRows: CostRow[];
  let displayTotal: number;
  let displayPayloadBytes = 0;
  let displayOutputTokens = 0;
  let displayShortlistedBytes = 0;
  if (active === 'total') {
    displayRows = totalRows;
    displayTotal = totalCost;
    displayPayloadBytes = results.rca_result?.payload_bytes ?? 0;
    displayOutputTokens = results.rca_result?.output_tokens ?? 0;
    displayShortlistedBytes =
      (results.total_yaml_bytes ?? results.data_analysis?.total_yaml_bytes ?? 0)
      + (results.total_log_bytes ?? 0);
  } else {
    const sc = perStage[active];
    if (sc) {
      displayRows = buildStageRows(sc, active);
      displayTotal = displayRows.reduce((s, r) => s + r.cost, 0);
      displayPayloadBytes = sc.payload_bytes ?? 0;
      displayOutputTokens = sc.output_tokens ?? 0;
      displayShortlistedBytes = sc.shortlisted_total_bytes
        ?? ((sc.shortlisted_yaml_bytes ?? 0) + (sc.shortlisted_log_bytes ?? 0));
    } else {
      displayRows = totalRows;
      displayTotal = totalCost;
      displayPayloadBytes = results.rca_result?.payload_bytes ?? 0;
      displayOutputTokens = results.rca_result?.output_tokens ?? 0;
      displayShortlistedBytes =
        (results.total_yaml_bytes ?? results.data_analysis?.total_yaml_bytes ?? 0)
        + (results.total_log_bytes ?? 0);
    }
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-4">
      <div className="flex items-center gap-3 mb-1">
        <Zap className="w-5 h-5 text-brand-red" />
        <h3 className="text-base font-semibold text-slate-800">Token Usage & Cost Summary</h3>
      </div>
      <p className="text-xs text-slate-500">LLM API consumption across all workflow phases</p>

      {/* Tab buttons */}
      {tabs.length > 1 && (
        <div className="flex items-center gap-2 flex-wrap">
          {tabs.map(tab => (
            <button
              key={tab.key}
              onClick={() => setActive(tab.key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium
                          border transition-all duration-200
                          ${active === tab.key
                            ? 'bg-brand-red text-white border-brand-red shadow-sm'
                            : 'bg-white text-slate-600 border-slate-200 hover:border-brand-red/50 hover:text-brand-red'
                          }`}
            >
              <span
                className={`w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold
                            ${active === tab.key ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-500'}`}
              >
                {tab.short}
              </span>
              {tab.label}
            </button>
          ))}
        </div>
      )}

      <CostTableUI rows={displayRows} totalCost={displayTotal} />

      {/* Compression summary line (switches with selected stage) */}
      {displayPayloadBytes > 0 && (
        <div className="p-3 bg-slate-50 rounded-lg text-xs text-slate-600">
          <span className="font-semibold">Compression:</span>{' '}
          {(() => {
            const pb = displayPayloadBytes;
            const ratio = displayShortlistedBytes > 0 && pb > 0 ? (displayShortlistedBytes / pb).toFixed(1) : '—';
            return `Shortlisted files: ${(displayShortlistedBytes / 1e6).toFixed(2)} MB -> Payload to LLM: ${(pb / 1024).toFixed(0)} KB (${ratio}x) -> Final output: ${displayOutputTokens.toLocaleString()} tokens`;
          })()}
        </div>
      )}
    </div>
  );
}

function BarChartPlaceholderIcon(props: React.SVGProps<SVGSVGElement> & { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      {...props}>
      <rect x="3" y="12" width="4" height="8" rx="1" />
      <rect x="10" y="4" width="4" height="16" rx="1" />
      <rect x="17" y="8" width="4" height="12" rx="1" />
    </svg>
  );
}
