import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { ErrorState } from '../components/common/ErrorState';
import { EmptyState } from '../components/common/EmptyState';
import { Skeleton } from '../components/common/Skeleton';
import { safeExternalUrl } from '../lib/safeUrl';

export const SEARCH_STATUSES = [
  'new',
  'needs_jd',
  'analyzed',
  'scored',
  'queued',
  'prepared',
  'analysis_failed',
  'filtered_out',
  'applied',
  'replied',
  'interview',
  'offer',
  'rejected',
  'skipped',
] as const;

const STATUS_LABELS: Record<string, string> = {
  needs_jd: 'Needs JD',
  analysis_failed: 'Analysis failed',
  filtered_out: 'Filtered out',
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status.charAt(0).toUpperCase() + status.slice(1);
}

function displayDate(value: string | null | undefined): string {
  if (!value) return '';
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : value;
}

export function JobsPage() {
  const [location, setLocation] = useState('');
  const [status, setStatus] = useState('');
  const [postedFrom, setPostedFrom] = useState('');
  const [postedTo, setPostedTo] = useState('');

  const filters = {
    location: location.trim() || undefined,
    status: status || undefined,
    postedFrom: postedFrom || undefined,
    postedTo: postedTo || undefined,
  };

  const { data: jobs = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['jobs', filters],
    queryFn: () => api.getJobs(filters),
  });

  return (
    <div className="flex h-full min-h-0 w-full flex-col gap-4 p-6">
      <div className="shrink-0">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">All jobs</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Every posting JobPilot has found, including older searches.
        </p>
      </div>

      <div className="flex shrink-0 flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-[11px] font-semibold text-gray-500 dark:text-gray-400">
          Location
          <input
            aria-label="Filter by location"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="City or remote"
            className="w-40 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] font-semibold text-gray-500 dark:text-gray-400">
          Posted from
          <input
            type="date"
            aria-label="Posted from"
            value={postedFrom}
            onChange={(e) => setPostedFrom(e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] font-semibold text-gray-500 dark:text-gray-400">
          Posted to
          <input
            type="date"
            aria-label="Posted to"
            value={postedTo}
            onChange={(e) => setPostedTo(e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] font-semibold text-gray-500 dark:text-gray-400">
          Status
          <select
            aria-label="Filter by status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="">All statuses</option>
            {SEARCH_STATUSES.map((item) => (
              <option key={item} value={item}>
                {statusLabel(item)}
              </option>
            ))}
          </select>
        </label>
        <span className="pb-1.5 text-xs text-gray-500 dark:text-gray-400">{jobs.length} jobs</span>
      </div>

      {isLoading ? (
        <Skeleton className="min-h-0 flex-1 w-full rounded-xl" />
      ) : isError ? (
        <ErrorState
          title="Failed to load jobs"
          message={error instanceof Error ? error.message : 'Unknown error'}
          onRetry={() => refetch()}
        />
      ) : jobs.length === 0 ? (
        <EmptyState
          title="No jobs found"
          message="Nothing matches these filters. Clear them to see every searched job."
        />
      ) : (
        <div className="min-h-0 w-full flex-1 overflow-auto rounded-xl border border-gray-200 bg-white shadow-xs dark:border-gray-800 dark:bg-gray-900">
          <table className="w-full table-fixed text-left text-xs text-gray-700 dark:text-gray-300">
            <colgroup>
              <col className="w-[24%]" />
              <col className="w-[20%]" />
              <col className="w-[12%]" />
              <col className="w-[16%]" />
              <col className="w-[14%]" />
              <col className="w-[14%]" />
            </colgroup>
            <thead className="sticky top-0 z-10 border-b border-gray-200 bg-gray-50 text-[11px] font-bold uppercase tracking-wider text-gray-500 dark:border-gray-800 dark:bg-gray-800 dark:text-gray-400">
              <tr>
                <th className="px-4 py-3">Job title</th>
                <th className="px-4 py-3">Company</th>
                <th className="px-4 py-3">Posted</th>
                <th className="px-4 py-3">Location</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Career link</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {jobs.map((job) => {
                const href = safeExternalUrl(job.url);
                return (
                  <tr key={job.job_id} data-testid={`job-row-${job.job_id}`} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/40">
                    <td className="px-4 py-3 align-top break-words font-semibold text-gray-900 dark:text-gray-100">
                      {job.title}
                    </td>
                    <td className="px-4 py-3 align-top break-words">{job.company}</td>
                    <td className="px-4 py-3 align-top">{displayDate(job.posted_at) || '—'}</td>
                    <td className="px-4 py-3 align-top break-words">{job.location || '—'}</td>
                    <td className="px-4 py-3 align-top">{statusLabel(job.status)}</td>
                    <td className="px-4 py-3 align-top">
                      {href ? (
                        <a
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-label={`Open career page for ${job.company}`}
                          className="font-semibold text-indigo-700 hover:underline dark:text-indigo-300"
                        >
                          Open
                        </a>
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
