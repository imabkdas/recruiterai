import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from './context/ToastContext';
import { AppShell } from './components/layout/AppShell';
import { TodayPage } from './pages/TodayPage';
import { JobsPage } from './pages/JobsPage';
import { ResumesPage } from './pages/ResumesPage';
import { NeedsJdPage } from './pages/NeedsJdPage';
import { ApplicationsPage } from './pages/ApplicationsPage';
import { RunPage } from './pages/RunPage';
import { AnswersPage } from './pages/AnswersPage';
import { StatsPage } from './pages/StatsPage';
import { ProfileCheckPage } from './pages/ProfileCheckPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <AppShell>
            <Routes>
              <Route path="/" element={<TodayPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route path="/today" element={<Navigate to="/" replace />} />
              <Route path="/resumes" element={<ResumesPage />} />
              <Route path="/needs-jd" element={<NeedsJdPage />} />
              <Route path="/applications" element={<ApplicationsPage />} />
              <Route path="/answers" element={<AnswersPage />} />
              <Route path="/stats" element={<StatsPage />} />
              <Route path="/run" element={<RunPage />} />
              <Route path="/profile-check" element={<ProfileCheckPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AppShell>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

export default App;
