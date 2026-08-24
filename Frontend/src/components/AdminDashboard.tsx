import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../contexts/UserContext';
import {
  getAdminStats,
  getAdminSessions,
  adminCancelJob,
  adminDeleteJob,
  type AdminStats,
  type JobSession,
} from '../services/api';
import {
  BarChart3,
  Activity,
  CheckCircle2,
  XCircle,
  HardDrive,
  Users,
  Trash2,
  StopCircle,
  RefreshCw,
  ArrowLeft,
  Server,
  ExternalLink,
} from 'lucide-react';

const POLL_INTERVAL = 8000;

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { bg: string; text: string; label: string }> = {
    running:   { bg: 'bg-blue-100', text: 'text-blue-700', label: 'Running' },
    in_progress: { bg: 'bg-blue-100', text: 'text-blue-700', label: 'Running' },
    queued:    { bg: 'bg-yellow-100', text: 'text-yellow-700', label: 'Queued' },
    completed: { bg: 'bg-green-100', text: 'text-green-700', label: 'Completed' },
    rca_completed: { bg: 'bg-green-100', text: 'text-green-700', label: 'Completed' },
    error:     { bg: 'bg-red-100', text: 'text-red-700', label: 'Error' },
    failed:    { bg: 'bg-red-100', text: 'text-red-700', label: 'Failed' },
    cancelled: { bg: 'bg-slate-100', text: 'text-slate-600', label: 'Cancelled' },
  };
  const s = map[status] || { bg: 'bg-slate-100', text: 'text-slate-600', label: status };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      {s.label}
    </span>
  );
}

function formatDate(iso?: string) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch { return iso; }
}

export default function AdminDashboard() {
  const { isAdmin } = useUser();
  const navigate = useNavigate();

  const [stats, setStats] = useState<AdminStats | null>(null);
  const [sessions, setSessions] = useState<JobSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchData = useCallback(async () => {
    try {
      const [s, sess] = await Promise.all([
        getAdminStats(),
        getAdminSessions(),
      ]);
      setStats(s);
      setSessions(sess);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load admin data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) { navigate('/', { replace: true }); return; }
    fetchData();
    const iv = setInterval(fetchData, POLL_INTERVAL);
    return () => clearInterval(iv);
  }, [isAdmin, navigate, fetchData]);

  const handleCancel = async (sid: string) => {
    if (!confirm(`Cancel job ${sid}?`)) return;
    try { await adminCancelJob(sid); fetchData(); }
    catch (err) { alert(err instanceof Error ? err.message : 'Cancel failed'); }
  };

  const handleDelete = async (sid: string) => {
    if (!confirm(`Permanently delete session ${sid} and all its data?`)) return;
    try { await adminDeleteJob(sid); fetchData(); }
    catch (err) { alert(err instanceof Error ? err.message : 'Delete failed'); }
  };

  if (!isAdmin) return null;

  const pvc = stats?.pvc_usage;

  return (
    <div className="min-h-screen bg-gray-50 p-6 max-w-[1500px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/')}
            className="p-2 rounded-lg hover:bg-slate-200 transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-slate-600" />
          </button>
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Admin Dashboard</h1>
            <p className="text-sm text-slate-500">System overview and job management</p>
          </div>
        </div>
        <button
          onClick={() => { setLoading(true); fetchData(); }}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white border border-slate-200 hover:bg-slate-50 text-sm text-slate-600 transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="mb-6 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4 mb-8">
          <StatCard icon={<BarChart3 className="w-5 h-5" />} label="Total Jobs" value={stats.total} color="slate" />
          <StatCard icon={<Activity className="w-5 h-5" />} label="Running" value={stats.running} color="blue" />
          <StatCard icon={<CheckCircle2 className="w-5 h-5" />} label="Completed" value={stats.completed} color="green" />
          <StatCard icon={<XCircle className="w-5 h-5" />} label="Failed" value={stats.failed} color="red" />
          <StatCard icon={<Server className="w-5 h-5" />} label="Active Pods" value={stats.active_pods} color="purple" />
        </div>
      )}

      {/* PVC + Owners row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* PVC usage */}
        {pvc && (
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <div className="flex items-center gap-2 mb-4">
              <HardDrive className="w-5 h-5 text-slate-500" />
              <h3 className="font-semibold text-slate-700">PVC Storage</h3>
            </div>
            <div className="mb-3">
              <div className="flex justify-between text-sm text-slate-600 mb-1">
                <span>{pvc.used_gb} GB used</span>
                <span>{pvc.free_gb} GB free of {pvc.total_gb} GB</span>
              </div>
              <div className="w-full h-3 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    pvc.used_pct > 85 ? 'bg-red-500' : pvc.used_pct > 60 ? 'bg-yellow-500' : 'bg-green-500'
                  }`}
                  style={{ width: `${Math.min(pvc.used_pct, 100)}%` }}
                />
              </div>
            </div>
            <p className="text-xs text-slate-400">{pvc.used_pct}% utilized</p>
          </div>
        )}

        {/* Owners */}
        {stats && Object.keys(stats.owners).length > 0 && (
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <div className="flex items-center gap-2 mb-4">
              <Users className="w-5 h-5 text-slate-500" />
              <h3 className="font-semibold text-slate-700">Jobs by User</h3>
            </div>
            <div className="space-y-2">
              {Object.entries(stats.owners)
                .sort(([, a], [, b]) => b - a)
                .map(([owner, count]) => (
                  <div key={owner} className="flex items-center justify-between text-sm">
                    <span className="text-slate-600">{owner || 'unknown'}</span>
                    <span className="font-medium text-slate-800 bg-slate-100 px-2 py-0.5 rounded">{count}</span>
                  </div>
                ))}
            </div>
          </div>
        )}
      </div>

      {/* All sessions table */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-100">
          <h3 className="font-semibold text-slate-700">All Sessions ({sessions.length})</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 text-left text-xs text-slate-500 uppercase tracking-wider">
                <th className="px-5 py-3">Owner</th>
                <th className="px-5 py-3">Session ID</th>
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3">Round</th>
                <th className="px-5 py-3">Phase</th>
                <th className="px-5 py-3">Problem</th>
                <th className="px-5 py-3">Case #</th>
                <th className="px-5 py-3">Created</th>
                <th className="px-5 py-3">Report</th>
                <th className="px-5 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sessions.length === 0 ? (
                <tr><td colSpan={10} className="px-5 py-8 text-center text-slate-400">No sessions found</td></tr>
              ) : (
                sessions.map((s) => (
                  <tr key={s.session_id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-5 py-3 font-medium text-slate-700">{s.owner || '—'}</td>
                    <td className="px-5 py-3 font-mono text-xs text-slate-500 max-w-[180px] truncate" title={s.session_id}>
                      {s.session_id.replace('session_', '').slice(0, 20)}
                    </td>
                    <td className="px-5 py-3"><StatusBadge status={s.status} /></td>
                    <td className="px-5 py-3">
                      {(() => {
                        const r = s.deepening_round ?? 1;
                        if (r === 1) return <span className="text-slate-500 text-xs">1st pass</span>;
                        return <span className="text-xs font-medium text-indigo-600">{r === 2 ? '2nd' : r === 3 ? '3rd' : `${r}th`} round</span>;
                      })()}
                    </td>
                    <td className="px-5 py-3 text-slate-600">{s.phase || '—'}</td>
                    <td className="px-5 py-3 max-w-[200px] truncate text-slate-600" title={s.problem_statement}>
                      {s.problem_statement || '—'}
                    </td>
                    <td className="px-5 py-3 text-slate-600">{s.case_number || '—'}</td>
                    <td className="px-5 py-3 text-slate-500 whitespace-nowrap">{formatDate(s.created_at)}</td>
                    <td className="px-5 py-3">
                      {['completed', 'rca_completed'].includes(s.status) ? (
                        <a
                          href={`/rca-report?session=${encodeURIComponent(s.session_id)}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-green-600 hover:text-green-800 font-medium"
                        >
                          View Report <ExternalLink className="w-3 h-3" />
                        </a>
                      ) : ['running', 'in_progress', 'queued'].includes(s.status) ? (
                        <a
                          href={`/analysis?session=${encodeURIComponent(s.session_id)}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-amber-600 hover:text-amber-800 font-medium"
                        >
                          View Progress <ExternalLink className="w-3 h-3" />
                        </a>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {['running', 'in_progress', 'queued'].includes(s.status) && (
                          <button
                            onClick={() => handleCancel(s.session_id)}
                            className="p-1.5 rounded hover:bg-yellow-100 text-yellow-600 transition-colors"
                            title="Cancel job"
                          >
                            <StopCircle className="w-4 h-4" />
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(s.session_id)}
                          className="p-1.5 rounded hover:bg-red-100 text-red-500 transition-colors"
                          title="Delete session"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, color }: {
  icon: React.ReactNode;
  label: string;
  value: number;
  color: string;
}) {
  const colorMap: Record<string, string> = {
    slate: 'bg-slate-100 text-slate-600',
    blue: 'bg-blue-100 text-blue-600',
    green: 'bg-green-100 text-green-600',
    red: 'bg-red-100 text-red-600',
    purple: 'bg-purple-100 text-purple-600',
  };
  const cls = colorMap[color] || colorMap.slate;
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <div className={`inline-flex items-center justify-center w-10 h-10 rounded-lg ${cls} mb-3`}>
        {icon}
      </div>
      <p className="text-2xl font-bold text-slate-800">{value}</p>
      <p className="text-xs text-slate-500 mt-0.5">{label}</p>
    </div>
  );
}
