import React from 'react';
import { NavLink } from 'react-router-dom';

interface AppShellProps {
  children: React.ReactNode;
}

const NAV_ITEMS = [
  { path: '/', label: 'Today', icon: '☀️' },
  { path: '/jobs', label: 'All jobs', icon: '🗂️' },
  { path: '/resumes', label: 'Edit resume', icon: '📄' },
  { path: '/needs-jd', label: 'Needs JD', icon: '📝' },
  { path: '/applications', label: 'Applications', icon: '📬' },
  { path: '/answers', label: 'Answers', icon: '💡' },
  { path: '/stats', label: 'Stats', icon: '📊' },
  { path: '/run', label: 'Run', icon: '⚡' },
  { path: '/profile-check', label: 'Profile check', icon: '👤' },
];

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex h-screen bg-gray-50 text-gray-900 dark:bg-gray-950 dark:text-gray-100 overflow-hidden">
      {/* Sidebar */}
      <aside className="w-60 flex-shrink-0 border-r border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 flex flex-col justify-between">
        <div>
          {/* Logo / Brand Header */}
          <div className="h-16 flex items-center px-6 border-b border-gray-100 dark:border-gray-800/80">
            <span className="text-xl font-bold bg-gradient-to-r from-indigo-600 to-violet-600 bg-clip-text text-transparent">
              JobPilot
            </span>
            <span className="ml-2 px-2 py-0.5 text-[10px] font-semibold tracking-wider uppercase rounded-full bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
              Local
            </span>
          </div>

          {/* Navigation items */}
          <nav className="p-3 space-y-1">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.path === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300 font-semibold shadow-xs'
                      : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800/60 hover:text-gray-900 dark:hover:text-gray-200'
                  }`
                }
              >
                <span className="text-base">{item.icon}</span>
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </div>

        {/* Footer info */}
        <div className="p-4 border-t border-gray-100 dark:border-gray-800 text-[11px] text-gray-400 dark:text-gray-500">
          Single-user local CLI & UI
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {children}
      </main>
    </div>
  );
}
