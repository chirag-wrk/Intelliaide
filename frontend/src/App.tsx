import { useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import Portal from './components/Portal';
import LiveAnalysis from './components/LiveAnalysis';
import Analytics from './components/Analytics';
import RCAReport from './components/RCAReport';
import RCAExecutiveSummary from './components/RCAExecutiveSummary';
import LoggingTracing from './components/LoggingTracing';
import Remediation from './components/Remediation';
import RawConsole from './components/RawConsole';
import HelpPage from './components/HelpPage';
import HelpModal from './components/HelpModal';
import AdminDashboard from './components/AdminDashboard';
import { useAgentMemory } from './hooks/useAgentMemory';
import { useEffectiveSession } from './hooks/useEffectiveSession';
import { AnalysisProvider, useAnalysis } from './contexts/AnalysisContext';
import { UserProvider, useUser } from './contexts/UserContext';

/** Sidebar-based shell for LiveAnalysis + secondary pages */
function AppShell() {
  const { isRunning } = useAnalysis();
  const { effectiveSession } = useEffectiveSession();
  const { latestSession } = useAgentMemory(isRunning, effectiveSession);
  const [helpModalOpen, setHelpModalOpen] = useState(false);

  const agentReady = !isRunning && !!latestSession && latestSession.status === 'rca_completed';

  return (
    <div className="min-h-screen bg-gray-50">
      <Sidebar agentReady={agentReady} />
      <HelpModal isOpen={helpModalOpen} onClose={() => setHelpModalOpen(false)} />
      <main className="ml-56 p-8 max-w-[1400px]">
        <Routes>
          <Route path="/analysis" element={<LiveAnalysis />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/rca-summary" element={<RCAExecutiveSummary />} />
          <Route path="/rca-report" element={<RCAReport />} />
          <Route path="/logging-tracing" element={<LoggingTracing />} />
          <Route path="/remediation" element={<Remediation />} />
          <Route path="/console" element={<RawConsole />} />
          <Route path="/help" element={<HelpPage />} />
        </Routes>
      </main>
    </div>
  );
}

/** Shows a loading spinner while the OAuth identity is being resolved */
function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading } = useUser();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-slate-500 text-sm">Authenticating...</div>
      </div>
    );
  }
  if (!user) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <p className="text-slate-600 mb-4">Authentication required.</p>
          <p className="text-sm text-slate-400">If you are not redirected, <a href="/oauth/sign_in" className="text-brand-red underline">click here</a> to sign in.</p>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}

/** Route guard: only admin users can access */
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useUser();
  if (!isAdmin) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Portal />} />
      <Route path="/admin" element={<RequireAdmin><AdminDashboard /></RequireAdmin>} />
      <Route path="/*" element={<AppShell />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <UserProvider>
        <AnalysisProvider>
          <AuthGate>
            <AppRoutes />
          </AuthGate>
        </AnalysisProvider>
      </UserProvider>
    </BrowserRouter>
  );
}
