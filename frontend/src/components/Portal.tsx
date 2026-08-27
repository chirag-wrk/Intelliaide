import { useState, useRef, useCallback, useEffect } from 'react';
import {
  Upload, Play, AlertCircle, Loader2, Hash, X, RefreshCw,
  FileArchive, Download, Search, ExternalLink, Activity,
  BarChart3, FileText, Terminal, Wrench, FileSearch, Trash2,
  Shield, LogOut, HelpCircle,
} from 'lucide-react';
import { useAnalysis } from '../contexts/AnalysisContext';
import { useUser } from '../contexts/UserContext';
import HelpModal from './HelpModal';
import {
  fetchCaseAttachment, downloadSpecificAttachment, getDownloadStatus,
  getSessions, clearSessionsHistory,
  uploadMustGather,
} from '../services/api';
import type { AttachmentOption, JobSession } from '../services/api';
import redHatLogo from '../assets/red_hat_logo.svg';

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('en-US', {
      year: 'numeric', month: 'short', day: '2-digit',
    });
  } catch { return iso; }
}

function shortId(id: string) {
  // session_20260331_062313_1068123 → S-062313
  const parts = id.split('_');
  return parts.length >= 3 ? `S-${parts[2]}` : id.slice(0, 8);
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    rca_completed: { label: 'COMPLETED', cls: 'bg-emerald-500 text-white' },
    completed:     { label: 'COMPLETED', cls: 'bg-emerald-500 text-white' },
    in_progress:   { label: 'RUNNING',   cls: 'bg-amber-400 text-white' },
    running:       { label: 'RUNNING',   cls: 'bg-amber-400 text-white' },
    error:         { label: 'ERROR',     cls: 'bg-red-500 text-white' },
    cancelled:     { label: 'CANCELLED', cls: 'bg-slate-400 text-white' },
    queued:        { label: 'QUEUED',    cls: 'bg-slate-500 text-white' },
    created:       { label: 'QUEUED',    cls: 'bg-slate-500 text-white' },
  };
  const { label, cls } = map[status] ?? {
    label: status.toUpperCase(),
    cls: 'bg-slate-300 text-slate-700',
  };
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded text-[11px] font-bold tracking-wide ${cls}`}>
      {label}
    </span>
  );
}

// ── Top Navbar ────────────────────────────────────────────────────────────────

const NAV_ITEMS = [
  { href: '/',         label: 'Dashboard', icon: Activity },
  { href: '/analysis', label: 'Live Analysis', icon: Activity },
  { href: '/analytics',label: 'Analytics', icon: BarChart3 },
  { href: '/rca-report',label: 'Report', icon: FileText },
  { href: '/logging-tracing', label: 'Logs', icon: FileSearch },
  { href: '/remediation',label: 'Remediation', icon: Wrench },
  { href: '/console',  label: 'Console', icon: Terminal },
];

function TopNav({ onHelpClick }: { onHelpClick: () => void }) {
  const { user, isAdmin, logout } = useUser();
  const initials = user?.name
    ? user.name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2)
    : 'RH';

  return (
    <header className="bg-sidebar-bg border-b border-zinc-800 h-14 flex items-center px-6 gap-4 sticky top-0 z-50">
      {/* Brand */}
      <div className="flex items-center gap-2.5 mr-6">
        <img src={redHatLogo} alt="Red Hat" className="w-7 h-7 object-contain flex-shrink-0" />
        <div>
          <div className="text-white font-bold text-sm leading-tight tracking-wide">RCA Portal</div>
          <div className="text-zinc-500 text-[9px] tracking-widest uppercase">IntelliAide</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex items-center gap-1">
        <a
          href="/"
          className="px-3 py-1.5 rounded text-sm font-medium bg-brand-red text-white transition-colors"
        >
          Dashboard
        </a>
        {isAdmin && (
          <a
            href="/admin"
            className="px-3 py-1.5 rounded text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors flex items-center gap-1"
          >
            <Shield className="w-3.5 h-3.5" />
            Admin
          </a>
        )}
      </nav>

      <div className="ml-auto flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-brand-red flex items-center justify-center text-white text-xs font-bold">
            {initials}
          </div>
          <span className="text-zinc-300 text-sm hidden sm:block">{user?.name || 'User'}</span>
          {isAdmin && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-yellow-500/20 text-yellow-400 uppercase">
              Admin
            </span>
          )}
        </div>
        <button
          onClick={onHelpClick}
          className="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-white transition-colors"
          title="Help"
        >
          <HelpCircle className="w-5 h-5" />
        </button>
        <button
          onClick={logout}
          className="p-1.5 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
          title="Logout"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}

// ── Main Portal ───────────────────────────────────────────────────────────────

type CaseStep = 'idle' | 'downloading' | 'selecting' | 'ready' | 'error';

export default function Portal() {
  const { isUploading, startNewAnalysis, error: analysisError } = useAnalysis();
  const { user } = useUser();
  const currentUser = user?.name || '';
  const [helpOpen, setHelpOpen] = useState(() => !localStorage.getItem('intelliaide-has-seen-welcome'));

  const closeHelp = () => {
    localStorage.setItem('intelliaide-has-seen-welcome', 'true');
    setHelpOpen(false);
  };

  // ── Submit method toggle ─────────────────────────────────────────────────
  type SubmitMethod = 'upload' | 'case';
  const [submitMethod, setSubmitMethod] = useState<SubmitMethod>('upload');

  // ── Upload form state ──────────────────────────────────────────────────────
  const [issue, setIssue]           = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [caseNum, setCaseNum]       = useState('');
  const fileInputRef                = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  // ── Hydra case step state ─────────────────────────────────────────────────
  const [caseStep, setCaseStep]           = useState<CaseStep>('idle');
  const [downloadedFile, setDownloadedFile]   = useState<string | null>(null);
  const [downloadedFileId, setDownloadedFileId] = useState<string | null>(null);
  const [caseError, setCaseError]         = useState<string | null>(null);
  const [attachmentList, setAttachmentList]   = useState<AttachmentOption[]>([]);

  // ── Jobs board state ──────────────────────────────────────────────────────
  const [liveSessions, setLiveSessions] = useState<JobSession[]>([]);
  const [jobSearch, setJobSearch]       = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [clearing, setClearing]         = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // ── Clear all finished sessions ───────────────────────────────────────────
  const handleClearSessions = async () => {
    if (!window.confirm('Remove all completed, error, and cancelled sessions from the board?')) return;
    setClearing(true);
    await clearSessionsHistory();
    const data = await getSessions(currentUser).catch(() => [] as JobSession[]);
    setLiveSessions(data);
    setClearing(false);
  };

  // ── Poll sessions every 3 s (scoped to current user) ─────────────────────
  useEffect(() => {
    let active = true;
    const poll = async () => {
      const data = await getSessions(currentUser).catch(() => [] as JobSession[]);
      if (active) setLiveSessions(data);
    };
    poll();
    const id = setInterval(poll, 1500);
    return () => { active = false; clearInterval(id); };
  }, [currentUser]);

  // ── Drag-and-drop ─────────────────────────────────────────────────────────
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) setSelectedFile(file);
  }, []);

  const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); setIsDragOver(true); };
  const handleDragLeave = () => setIsDragOver(false);

  // ── Case reset ────────────────────────────────────────────────────────────
  const handleCaseReset = useCallback(() => {
    setCaseStep('idle');
    setDownloadedFile(null);
    setDownloadedFileId(null);
    setCaseError(null);
    setAttachmentList([]);
  }, []);

  // ── Toggle between Upload / Case # ──────────────────────────────────────
  const handleMethodSwitch = (method: SubmitMethod) => {
    if (method === submitMethod) return;
    setSubmitMethod(method);
    if (method === 'upload') {
      setCaseNum('');
      handleCaseReset();
    } else {
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  // ── Poll a background Hydra download until it finishes ──────────────────
  const pollDownload = useCallback(async (downloadId: string) => {
    setCaseStep('downloading');
    const poll = async () => {
      try {
        const s = await getDownloadStatus(downloadId);
        if (s.status === 'downloaded') {
          setDownloadedFile(s.filename);
          setDownloadedFileId(s.local_file_id);
          setAttachmentList([]);
          setCaseStep('ready');
          return;
        }
        if (s.status === 'error') {
          setCaseError(s.detail || 'Download failed.');
          setCaseStep('error');
          return;
        }
        setTimeout(poll, 2000);
      } catch {
        setCaseError('Lost connection while downloading.');
        setCaseStep('error');
      }
    };
    poll();
  }, []);

  // ── Hydra: fetch attachment list ──────────────────────────────────────────
  const handleCaseFetch = async () => {
    if (!caseNum.trim()) return;
    setCaseStep('downloading');
    setDownloadedFile(null);
    setDownloadedFileId(null);
    setCaseError(null);
    setAttachmentList([]);
    try {
      const result = await fetchCaseAttachment(caseNum.trim());
      if (result.status === 'downloading') {
        pollDownload(result.download_id);
      } else if (result.status === 'multiple') {
        setAttachmentList(result.attachments);
        setCaseStep('selecting');
      } else {
        setDownloadedFile(result.filename);
        setDownloadedFileId(result.local_file_id);
        setCaseStep('ready');
      }
    } catch (e) {
      setCaseError(e instanceof Error ? e.message : 'Download failed.');
      setCaseStep('error');
    }
  };

  // ── Hydra: user chose a specific attachment ───────────────────────────────
  const handleSelectAttachment = async (uuid: string, filename: string) => {
    setCaseStep('downloading');
    setCaseError(null);
    try {
      const { download_id } = await downloadSpecificAttachment(caseNum.trim(), uuid, filename);
      pollDownload(download_id);
    } catch (e) {
      setCaseError(e instanceof Error ? e.message : 'Download failed.');
      setCaseStep('error');
    }
  };

  // ── Submit job ─────────────────────────────────────────────────────────────
  const handleSubmit = async () => {
    if (!issue.trim()) return;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      if (selectedFile) {
        const uploadResult = await uploadMustGather(selectedFile, selectedFile.name);
        await startNewAnalysis(issue.trim(), caseNum.trim() || undefined, uploadResult.local_file_id, currentUser);
        getSessions(currentUser).then(d => setLiveSessions(d)).catch(() => {});
        setIssue('');
        setCaseNum('');
        setSelectedFile(null);
        if (fileInputRef.current) fileInputRef.current.value = '';
        handleCaseReset();
      } else if (caseStep === 'ready' && downloadedFileId) {
        await startNewAnalysis(issue.trim(), caseNum.trim(), downloadedFileId, currentUser);
        getSessions(currentUser).then(d => setLiveSessions(d)).catch(() => {});
        setIssue('');
        setCaseNum('');
        handleCaseReset();
      } else if (caseNum.trim()) {
        await handleCaseFetch();
      }
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : 'Job submission failed.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const canSubmit = issue.trim() && (selectedFile || caseNum.trim() || caseStep === 'ready');
  const isBusy = isUploading || isSubmitting;

  // ── Jobs table ─────────────────────────────────────────────────────────────
  const filteredSessions = liveSessions.filter(s => {
    const q = jobSearch.toLowerCase();
    const matchSearch = !q
      || s.session_id.toLowerCase().includes(q)
      || (s.case_number ?? '').toLowerCase().includes(q)
      || (s.problem_statement ?? '').toLowerCase().includes(q);
    const matchStatus = !statusFilter
      || s.status === statusFilter
      || (statusFilter === 'completed' && s.status === 'rca_completed');
    return matchSearch && matchStatus;
  });

  return (
    <div className="min-h-screen bg-gray-100 flex flex-col">
      <TopNav onHelpClick={() => setHelpOpen(true)} />
      <HelpModal isOpen={helpOpen} onClose={closeHelp} />

      <main className="flex-1 p-6">
        <div className="max-w-[1400px] mx-auto grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">

          {/* ── LEFT: Submit New Job ─────────────────────────────────────── */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
            {/* Panel header */}
            <div className="bg-slate-800 px-5 py-3 flex items-center justify-between">
              <div>
                <div className="text-xs text-slate-400 uppercase tracking-widest">New Submittal</div>
                <h2 className="text-white font-semibold text-base mt-0.5">Submit New Job</h2>
              </div>
              <span className="text-xs text-brand-red font-mono bg-brand-red/10 px-2 py-0.5 rounded">
                #RCA-{new Date().getFullYear()}
              </span>
            </div>

            <div className="p-5 space-y-4">
              {/* Issue Description */}
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Issue Description
                  <span className="ml-2 text-xs text-slate-400 font-normal">(Required)</span>
                </label>
                <textarea
                  value={issue}
                  onChange={e => setIssue(e.target.value)}
                  placeholder="Enter detailed issue description for Root Cause Analysis (RCA)..."
                  rows={4}
                  disabled={isBusy}
                  className="w-full px-4 py-2.5 rounded-lg border border-slate-300 text-sm resize-none
                             focus:ring-2 focus:ring-brand-red/30 focus:border-brand-red
                             placeholder:text-slate-400 transition-colors disabled:opacity-50"
                />
              </div>

              {/* Method toggle */}
              <div className="flex rounded-lg border border-slate-300 overflow-hidden">
                <button
                  type="button"
                  onClick={() => handleMethodSwitch('upload')}
                  disabled={isBusy}
                  className={`flex-1 flex items-center justify-center gap-1.5 px-4 py-2 text-sm font-medium transition-colors ${
                    submitMethod === 'upload'
                      ? 'bg-brand-red text-white'
                      : 'bg-white text-slate-600 hover:bg-slate-50'
                  }`}
                >
                  <Upload className="w-4 h-4" />
                  Upload File
                </button>
                <button
                  type="button"
                  onClick={() => handleMethodSwitch('case')}
                  disabled={isBusy}
                  className={`flex-1 flex items-center justify-center gap-1.5 px-4 py-2 text-sm font-medium transition-colors ${
                    submitMethod === 'case'
                      ? 'bg-brand-red text-white'
                      : 'bg-white text-slate-600 hover:bg-slate-50'
                  }`}
                >
                  <Hash className="w-4 h-4" />
                  Fetch by Case #
                </button>
              </div>

              {/* Upload Logs/Dump */}
              {submitMethod === 'upload' && (
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Upload Logs/Dump
                  <span className="ml-2 text-xs text-slate-400 font-normal">(Drop Zone)</span>
                </label>
                {selectedFile ? (
                  <div className="flex items-center gap-2 px-4 py-3 rounded-lg border border-emerald-300 bg-emerald-50">
                    <FileArchive className="w-4 h-4 text-emerald-600 flex-shrink-0" />
                    <span className="text-sm text-slate-700 truncate flex-1">
                      {selectedFile.name} ({(selectedFile.size / 1024 / 1024).toFixed(1)} MB)
                    </span>
                    <button
                      onClick={() => { setSelectedFile(null); if (fileInputRef.current) fileInputRef.current.value = ''; }}
                      disabled={isBusy}
                      className="text-slate-400 hover:text-red-500 transition-colors"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <div
                    onDrop={handleDrop}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    className={`flex flex-col items-center justify-center gap-2 px-4 py-6 rounded-lg border-2 border-dashed cursor-pointer transition-colors ${
                      isDragOver ? 'border-brand-red bg-brand-red/5' : 'border-slate-300 hover:border-slate-400'
                    }`}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <Upload className="w-8 h-8 text-slate-400" />
                    <p className="text-sm text-slate-500">Drop files or Click to Upload</p>
                    <p className="text-xs text-slate-400">Supported: .zip, .tar, .log, .dmp (Max 2GB)</p>
                    <button
                      type="button"
                      className="mt-1 px-4 py-1.5 rounded-lg bg-brand-red text-white text-xs font-medium hover:bg-brand-red/90 transition-colors"
                    >
                      Browse Files
                    </button>
                  </div>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip,.tar,.tar.gz,.tgz,.log,.dmp"
                  className="hidden"
                  onChange={e => setSelectedFile(e.target.files?.[0] ?? null)}
                  disabled={isBusy}
                />
              </div>
              )}

              {/* Case # + Hydra flow */}
              {submitMethod === 'case' && (
              <>
              <div>
                <label className="flex items-center gap-1.5 text-sm font-medium text-slate-700 mb-1.5">
                  <Hash className="w-4 h-4" />
                  Case #
                  <span className="ml-1 text-xs text-slate-400 font-normal">(Required)</span>
                </label>
                <input
                  type="text"
                  value={caseNum}
                  onChange={e => { setCaseNum(e.target.value); if (caseStep !== 'idle') handleCaseReset(); }}
                  placeholder="CS-12345"
                  disabled={isBusy || caseStep === 'downloading'}
                  className="w-full px-4 py-2.5 rounded-lg border border-slate-300 text-sm
                             focus:ring-2 focus:ring-brand-red/30 focus:border-brand-red
                             placeholder:text-slate-400 transition-colors disabled:opacity-50"
                />
              </div>

              {caseStep === 'downloading' && (
                <div className="flex items-center gap-3 bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-sm text-blue-700">
                  <Loader2 className="w-4 h-4 animate-spin flex-shrink-0" />
                  <span>Fetching must-gather archive for case <strong>{caseNum}</strong>…</span>
                </div>
              )}

              {caseStep === 'selecting' && attachmentList.length > 0 && (
                <div className="border border-amber-200 rounded-lg overflow-hidden">
                  <div className="bg-amber-50 border-b border-amber-200 px-4 py-2.5 flex items-center gap-2">
                    <FileArchive className="w-4 h-4 text-amber-600 flex-shrink-0" />
                    <span className="text-sm font-medium text-amber-800">
                      {attachmentList.length} archives found — choose one:
                    </span>
                    <button onClick={handleCaseReset} className="ml-auto text-amber-500 hover:text-amber-700">
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                  <ul className="divide-y divide-slate-100 bg-white max-h-48 overflow-y-auto">
                    {attachmentList.map(att => (
                      <li key={att.uuid} className="flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50">
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-mono text-slate-700 truncate">{att.filename}</p>
                          <p className="text-xs text-slate-400 mt-0.5">
                            {att.size_bytes > 0 ? `${(att.size_bytes / 1024 / 1024).toFixed(0)} MB` : ''}
                            {att.created ? ` · ${att.created}` : ''}
                          </p>
                        </div>
                        <button
                          onClick={() => handleSelectAttachment(att.uuid, att.filename)}
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                                     bg-brand-red text-white hover:bg-brand-red/90 whitespace-nowrap"
                        >
                          <Download className="w-3 h-3" /> Use This
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {caseStep === 'ready' && downloadedFile && (
                <div className="flex items-center gap-3 bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-3 text-sm text-emerald-700">
                  <FileArchive className="w-4 h-4 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <span className="font-medium">Ready: </span>
                    <span className="font-mono truncate">{downloadedFile}</span>
                  </div>
                  <button onClick={handleCaseReset} className="text-emerald-500 hover:text-emerald-700">
                    <X className="w-4 h-4" />
                  </button>
                </div>
              )}

              {caseStep === 'error' && (
                <div className="flex items-center gap-3 bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" />
                  <span className="flex-1">{caseError || 'Download failed.'}</span>
                  <button onClick={handleCaseReset} className="text-red-400 hover:text-red-600">
                    <RefreshCw className="w-4 h-4" />
                  </button>
                </div>
              )}
              </>
              )}

              {/* Submit / analysis error banner */}
              {(submitError || analysisError) && (
                <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" />
                  <span className="flex-1">{submitError || analysisError}</span>
                  <button onClick={() => setSubmitError(null)} className="text-red-400 hover:text-red-600">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}

              {/* Submit button */}
              <button
                onClick={handleSubmit}
                disabled={isBusy || !canSubmit || caseStep === 'downloading' || caseStep === 'selecting'}
                className="w-full flex items-center justify-center gap-2 px-6 py-3 rounded-lg text-sm font-semibold
                           bg-brand-red text-white hover:bg-brand-red/90
                           active:scale-95 active:brightness-90
                           transition-all duration-100
                           disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {isBusy || caseStep === 'downloading' ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : caseStep === 'ready' || selectedFile ? (
                  <Play className="w-4 h-4" />
                ) : (
                  <Search className="w-4 h-4" />
                )}
                {isSubmitting ? 'Creating job…'
                  : isBusy ? 'Submitting…'
                  : caseStep === 'downloading' ? 'Fetching…'
                  : caseStep === 'ready' || selectedFile ? 'Submit Job'
                  : caseNum.trim() ? 'Fetch Must-Gather'
                  : 'Submit Job'}
              </button>
            </div>
          </div>

          {/* ── RIGHT: My Jobs & Tracking ────────────────────────────────── */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
            {/* Panel header */}
            <div className="bg-slate-800 px-5 py-3 flex items-center justify-between">
              <div>
                <div className="text-xs text-slate-400 uppercase tracking-widest">My Jobs &amp; Tracking</div>
                <h2 className="text-white font-semibold text-base mt-0.5">Job Status Overview</h2>
              </div>
              <button
                onClick={handleClearSessions}
                disabled={clearing || liveSessions.filter(s => ['completed','error','cancelled'].includes(s.status)).length === 0}
                title="Clear completed, error and cancelled sessions"
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                           border border-slate-600 text-slate-300 hover:bg-red-700 hover:border-red-600
                           hover:text-white transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              >
                {clearing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                Clear
              </button>
            </div>

            <div className="p-5">
              {/* Search Jobs */}
              <div className="mb-4">
                <label className="block text-xs text-slate-500 font-medium uppercase tracking-wide mb-1.5">Search Jobs</label>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                    <input
                      type="text"
                      value={jobSearch}
                      onChange={e => setJobSearch(e.target.value)}
                      placeholder="Session ID, Case #"
                      className="w-full pl-9 pr-4 py-2 rounded-lg border border-slate-300 text-sm
                                 focus:ring-2 focus:ring-brand-red/30 focus:border-brand-red
                                 placeholder:text-slate-400 transition-colors"
                    />
                  </div>
                  <select
                    value={statusFilter}
                    onChange={e => setStatusFilter(e.target.value)}
                    className="px-3 py-2 rounded-lg border border-slate-300 text-sm text-slate-600
                               focus:ring-2 focus:ring-brand-red/30 bg-white"
                  >
                    <option value="">Filter by Status</option>
                    <option value="completed">Completed</option>
                    <option value="running">Running</option>
                    <option value="queued">Queued</option>
                    <option value="error">Error</option>
                    <option value="cancelled">Cancelled</option>
                  </select>
                </div>
              </div>

              {/* Table */}
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-slate-700 text-white">
                      <th className="text-left px-3 py-2.5 text-xs font-semibold uppercase tracking-wide">Session ID</th>
                      <th className="text-left px-3 py-2.5 text-xs font-semibold uppercase tracking-wide">Case #</th>
                      <th className="text-left px-3 py-2.5 text-xs font-semibold uppercase tracking-wide">Submit Date</th>
                      <th className="text-left px-3 py-2.5 text-xs font-semibold uppercase tracking-wide">
                        Status
                        <span className="ml-1 text-slate-400 font-normal normal-case tracking-normal">(Color Coded)</span>
                      </th>
                      <th className="text-left px-3 py-2.5 text-xs font-semibold uppercase tracking-wide">Round</th>
                      <th className="text-left px-3 py-2.5 text-xs font-semibold uppercase tracking-wide">Report URL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredSessions.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-3 py-10 text-center text-slate-400 text-sm italic">
                          {liveSessions.length === 0 ? 'No jobs yet. Submit an analysis to get started.' : 'No jobs match your filter.'}
                        </td>
                      </tr>
                    ) : (
                      filteredSessions.map((session, idx) => {
                        const isCompleted = session.status === 'rca_completed' || session.status === 'completed';
                        const isActive = session.status === 'running' || session.status === 'in_progress';
                        const isQueued = session.status === 'queued' || session.status === 'created';

                        return (
                          <tr
                            key={session.session_id}
                            className={`border-b border-slate-100 hover:bg-slate-50/70 transition-colors ${idx % 2 !== 0 ? 'bg-slate-50/30' : ''}`}
                          >
                            <td className="px-3 py-3 font-mono text-xs text-slate-600" title={session.session_id}>
                              {shortId(session.session_id)}
                            </td>
                            <td className="px-3 py-3 text-xs text-slate-600">
                              {session.case_number || <span className="text-slate-400">—</span>}
                            </td>
                            <td className="px-3 py-3 text-xs text-slate-500 whitespace-nowrap">
                              {session.created_at ? formatDate(session.created_at) : '—'}
                            </td>
                            <td className="px-3 py-3">
                              <StatusBadge status={session.status} />
                            </td>
                            <td className="px-3 py-3 text-xs">
                              {(() => {
                                const r = session.deepening_round ?? 1;
                                if (r === 1) return <span className="text-slate-500">1st pass</span>;
                                return <span className="font-medium text-indigo-600">{r === 2 ? '2nd' : r === 3 ? '3rd' : `${r}th`} round</span>;
                              })()}
                            </td>
                            <td className="px-3 py-3">
                              {isCompleted ? (
                                <a
                                  href={`/rca-report?session=${encodeURIComponent(session.session_id)}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-flex items-center gap-1 text-brand-red hover:underline text-xs font-medium"
                                >
                                  View Report <ExternalLink className="w-3 h-3" />
                                </a>
                              ) : isActive ? (
                                <a
                                  href={`/analysis?session=${encodeURIComponent(session.session_id)}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-flex items-center gap-1 text-amber-600 hover:underline text-xs font-medium"
                                >
                                  View Progress <ExternalLink className="w-3 h-3" />
                                </a>
                              ) : isQueued ? (
                                <span className="text-slate-400 text-xs">(N/A)</span>
                              ) : session.status === 'error' ? (
                                <span className="text-red-400 text-xs" title={session.error}>
                                  {session.error ? session.error.slice(0, 40) + '…' : 'Error'}
                                </span>
                              ) : (
                                <span className="text-slate-400 text-xs">—</span>
                              )}
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

        </div>
      </main>
    </div>
  );
}
