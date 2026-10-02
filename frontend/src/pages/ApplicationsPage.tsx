import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../api/client';
import { useToast } from '../context/useToast';
import { ErrorState } from '../components/common/ErrorState';
import { EmptyState } from '../components/common/EmptyState';
import { Skeleton } from '../components/common/Skeleton';
import { safeExternalUrl } from '../lib/safeUrl';
import type { ApplicationItem, FollowUpItem } from '../api/types';

export const APPLICATION_STATUSES = [
  'new',
  'applied',
  'replied',
  'interview',
  'offer',
  'rejected',
] as const;

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number];

type ApplicationPatch = {
  status?: string;
  notes?: string;
  applied_at?: string;
  url?: string;
};

type EditField = {
  jobId: number;
  field: 'notes' | 'url';
  value: string;
};

type JdEdit = {
  jobId: number;
  company: string;
  text: string;
  loading: boolean;
};

export function formatDisplayDate(value: string | null | undefined): string {
  if (!value) return '';
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : value;
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    needs_jd: 'Needs JD',
    analysis_failed: 'Analysis failed',
    filtered_out: 'Filtered out',
  };
  return labels[status] ?? status.charAt(0).toUpperCase() + status.slice(1);
}

export function ApplicationsPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [selectedFilter, setSelectedFilter] = useState<string>('all');
  const [editing, setEditing] = useState<EditField | null>(null);
  const [jdEdit, setJdEdit] = useState<JdEdit | null>(null);

  const {
    data: applications = [],
    isLoading: isLoadingApps,
    isError: isAppsError,
    error: appsError,
    refetch: refetchApps,
  } = useQuery({
    queryKey: ['applications', selectedFilter],
    queryFn: () => api.getApplications(selectedFilter),
  });

  const { data: followUps = [] } = useQuery({
    queryKey: ['follow-ups'],
    queryFn: () => api.getFollowUps(),
  });

  const followUpsMap = new Map<number, FollowUpItem>(
    followUps.map((fu) => [fu.job_id, fu])
  );

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['applications'] });
    queryClient.invalidateQueries({ queryKey: ['jobs'] });
    queryClient.invalidateQueries({ queryKey: ['needs-jd'] });
    queryClient.invalidateQueries({ queryKey: ['progress'] });
    queryClient.invalidateQueries({ queryKey: ['follow-ups'] });
  };

  const openJdEditor = async (app: ApplicationItem) => {
    setJdEdit({
      jobId: app.job_id,
      company: app.company,
      text: '',
      loading: Boolean(app.has_description),
    });
    if (!app.has_description) return;
    try {
      const detail = await api.getJobDetail(app.job_id);
      setJdEdit({
        jobId: app.job_id,
        company: app.company,
        text: detail.description || '',
        loading: false,
      });
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : err instanceof Error ? err.message : 'Could not load the description';
      showToast(msg, 'error');
      setJdEdit(null);
    }
  };

  const saveJdMutation = useMutation({
    mutationFn: ({ jobId, text }: { jobId: number; text: string }) => api.addJd(jobId, text),
    onSuccess: () => {
      setJdEdit(null);
      showToast('Job description saved', 'success');
    },
    onError: (err) => {
      const msg = err instanceof ApiError ? err.detail : err.message;
      showToast(msg, 'error');
    },
    onSettled: invalidate,
  });

  const markMutation = useMutation({
    mutationFn: ({ jobId, status }: { jobId: number; status: string }) =>
      api.updateApplication(jobId, { status }),
    onMutate: async ({ jobId, status }) => {
      await queryClient.cancelQueries({ queryKey: ['applications', selectedFilter] });
      const previousApps =
        queryClient.getQueryData<ApplicationItem[]>(['applications', selectedFilter]) || [];

      queryClient.setQueryData<ApplicationItem[]>(
        ['applications', selectedFilter],
        previousApps.map((app) =>
          app.job_id === jobId ? { ...app, status } : app
        )
      );

      return { previousApps };
    },
    onError: (err, _vars, context) => {
      if (context?.previousApps) {
        queryClient.setQueryData(
          ['applications', selectedFilter],
          context.previousApps
        );
      }
      const msg = err instanceof ApiError ? err.detail : err.message;
      showToast(msg, 'error');
    },
    onSuccess: (_data, { status }) => {
      showToast(`Status updated to "${statusLabel(status)}"`, 'success');
    },
    onSettled: invalidate,
  });

  const saveMutation = useMutation({
    mutationFn: ({ jobId, patch }: { jobId: number; patch: ApplicationPatch; toast: string }) =>
      api.updateApplication(jobId, patch),
    onError: (err) => {
      const msg = err instanceof ApiError ? err.detail : err.message;
      showToast(msg, 'error');
    },
    onSuccess: (_data, { toast }) => {
      setEditing(null);
      showToast(toast, 'success');
    },
    onSettled: invalidate,
  });

  const commitEdit = () => {
    if (!editing) return;
    if (editing.field === 'notes') {
      saveMutation.mutate({
        jobId: editing.jobId,
        patch: { notes: editing.value },
        toast: 'Note saved',
      });
      return;
    }
    saveMutation.mutate({
      jobId: editing.jobId,
      patch: { url: editing.value },
      toast: 'Job link updated',
    });
  };

  if (isLoadingApps) {
    return (
      <div className="flex h-full w-full flex-col gap-4 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-72" />
        <Skeleton className="min-h-0 flex-1 w-full rounded-xl" />
      </div>
    );
  }

  if (isAppsError) {
    return (
      <div className="h-full w-full p-6">
        <ErrorState
          title="Failed to load applications"
          message={appsError instanceof Error ? appsError.message : 'Unknown error'}
          onRetry={() => refetchApps()}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col gap-4 p-6">
      <div className="flex shrink-0 flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            Applications
          </h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Every job from a search. Add a description when the posting arrived without one.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <label
            htmlFor="status-filter-select"
            className="text-xs font-semibold text-gray-600 dark:text-gray-400"
          >
            Filter by status:
          </label>
          <select
            id="status-filter-select"
            aria-label="Filter applications by status"
            value={selectedFilter}
            onChange={(e) => setSelectedFilter(e.target.value)}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="all">All Statuses ({applications.length})</option>
            {APPLICATION_STATUSES.map((st) => (
              <option key={st} value={st}>
                {statusLabel(st)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {applications.length === 0 ? (
        <EmptyState
          title="No jobs found"
          message={
            selectedFilter === 'all'
              ? 'Run the pipeline to search for jobs.'
              : `No jobs currently have status "${statusLabel(selectedFilter)}".`
          }
        />
      ) : (
        <div className="min-h-0 w-full flex-1 overflow-auto rounded-xl border border-gray-200 bg-white shadow-xs dark:border-gray-800 dark:bg-gray-900">
          <table className="w-full table-fixed text-left text-xs text-gray-700 dark:text-gray-300">
            <colgroup>
              <col className="w-[20%]" />
              <col className="w-[10%]" />
              <col className="w-[12%]" />
              <col className="w-[12%]" />
              <col className="w-[10%]" />
              <col className="w-[12%]" />
              <col className="w-[12%]" />
              <col className="w-[12%]" />
            </colgroup>
            <thead className="sticky top-0 z-10 border-b border-gray-200 bg-gray-50 text-[11px] font-bold uppercase tracking-wider text-gray-500 dark:border-gray-800 dark:bg-gray-800 dark:text-gray-400">
              <tr>
                <th className="px-4 py-3">Company & Title</th>
                <th className="px-4 py-3">Posted</th>
                <th className="px-4 py-3">Location</th>
                <th className="px-4 py-3">JD</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Job link</th>
                <th className="px-4 py-3">Applied Date</th>
                <th className="px-4 py-3">Note</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {applications.map((app) => {
                const followUp = followUpsMap.get(app.job_id);
                const isFollowUpDue = Boolean(followUp);
                const jobUrl = safeExternalUrl(app.url);
                const editingNote = editing?.jobId === app.job_id && editing.field === 'notes';
                const editingUrl = editing?.jobId === app.job_id && editing.field === 'url';
                const appliedDate = formatDisplayDate(app.applied_at);
                const posted = formatDisplayDate(app.posted_at);
                const knownStatus = (APPLICATION_STATUSES as readonly string[]).includes(app.status);
                const statusOptions = knownStatus ? APPLICATION_STATUSES : [app.status, ...APPLICATION_STATUSES];

                return (
                  <tr
                    key={app.job_id}
                    data-testid={`app-row-${app.job_id}`}
                    className={`transition-colors ${
                      isFollowUpDue
                        ? 'border-l-4 border-amber-500 bg-amber-50/60 dark:bg-amber-950/30'
                        : 'hover:bg-gray-50/50 dark:hover:bg-gray-800/40'
                    }`}
                  >
                    <td className="px-4 py-3.5 align-top">
                      <div className="break-words font-bold text-gray-900 dark:text-gray-100">
                        {app.company}
                      </div>
                      <div className="break-words text-gray-500 dark:text-gray-400">
                        {app.title}
                      </div>
                      {isFollowUpDue && followUp && (
                        <div className="mt-1">
                          <span
                            data-testid={`followup-badge-${app.job_id}`}
                            className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
                          >
                            ⚠️ No reply after {followUp.days_since_applied} days
                          </span>
                        </div>
                      )}
                    </td>

                    <td className="px-4 py-3.5 align-top text-gray-600 dark:text-gray-400">
                      {posted || '—'}
                    </td>

                    <td className="px-4 py-3.5 align-top break-words text-gray-600 dark:text-gray-400">
                      {app.location || '—'}
                    </td>

                    <td className="px-4 py-3.5 align-top">
                      <div className="flex items-center gap-2">
                        <span className={app.has_description ? 'text-gray-700 dark:text-gray-200' : 'text-gray-400'}>
                          {app.has_description ? 'JD' : 'No JD'}
                        </span>
                        <button
                          type="button"
                          aria-label={`Edit job description for ${app.company}`}
                          onClick={() => openJdEditor(app)}
                          className="rounded-md px-1.5 py-0.5 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
                        >
                          ✎
                        </button>
                      </div>
                    </td>

                    <td className="px-4 py-3.5 align-top">
                      <select
                        aria-label={`Change status for ${app.company}`}
                        value={app.status}
                        disabled={markMutation.isPending}
                        onChange={(e) =>
                          markMutation.mutate({ jobId: app.job_id, status: e.target.value })
                        }
                        className="w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-xs font-medium text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
                      >
                        {statusOptions.map((st) => (
                          <option key={st} value={st}>
                            {statusLabel(st)}
                          </option>
                        ))}
                      </select>
                    </td>

                    <td className="px-4 py-3.5 align-top">
                      {editingUrl ? (
                        <div className="flex items-center gap-1">
                          <input
                            aria-label={`Job link for ${app.company}`}
                            value={editing.value}
                            onChange={(e) =>
                              setEditing({ ...editing, value: e.target.value })
                            }
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') commitEdit();
                              if (e.key === 'Escape') setEditing(null);
                            }}
                            className="min-w-0 flex-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-800"
                            autoFocus
                          />
                          <button
                            type="button"
                            aria-label={`Save job link for ${app.company}`}
                            onClick={commitEdit}
                            className="rounded-md px-2 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 dark:text-indigo-300 dark:hover:bg-indigo-950/40"
                          >
                            Save
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2">
                          {jobUrl ? (
                            <a
                              href={jobUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="truncate font-semibold text-indigo-700 hover:underline dark:text-indigo-300"
                            >
                              Open
                            </a>
                          ) : (
                            <span className="text-gray-400">—</span>
                          )}
                          <button
                            type="button"
                            aria-label={`Edit job link for ${app.company}`}
                            onClick={() =>
                              setEditing({ jobId: app.job_id, field: 'url', value: app.url || '' })
                            }
                            className="rounded-md px-1.5 py-0.5 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
                          >
                            ✎
                          </button>
                        </div>
                      )}
                    </td>

                    <td className="px-4 py-3.5 align-top">
                      <input
                        type="date"
                        aria-label={`Applied date for ${app.company}`}
                        value={appliedDate}
                        onChange={(e) => {
                          if (!e.target.value) return;
                          saveMutation.mutate({
                            jobId: app.job_id,
                            patch: { applied_at: e.target.value },
                            toast: 'Applied date saved',
                          });
                        }}
                        className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
                      />
                    </td>

                    <td className="px-4 py-3.5 align-top">
                      {editingNote ? (
                        <div className="flex items-center gap-1">
                          <input
                            aria-label={`Note for ${app.company}`}
                            value={editing.value}
                            onChange={(e) =>
                              setEditing({ ...editing, value: e.target.value })
                            }
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') commitEdit();
                              if (e.key === 'Escape') setEditing(null);
                            }}
                            className="min-w-0 flex-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-800"
                            autoFocus
                          />
                          <button
                            type="button"
                            aria-label={`Save note for ${app.company}`}
                            onClick={commitEdit}
                            className="rounded-md px-2 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 dark:text-indigo-300 dark:hover:bg-indigo-950/40"
                          >
                            Save
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-start gap-2">
                          <span className="min-w-0 flex-1 break-words text-gray-600 dark:text-gray-400">
                            {app.notes || '—'}
                          </span>
                          <button
                            type="button"
                            aria-label={`Edit note for ${app.company}`}
                            onClick={() =>
                              setEditing({
                                jobId: app.job_id,
                                field: 'notes',
                                value: app.notes || '',
                              })
                            }
                            className="shrink-0 rounded-md px-1.5 py-0.5 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
                          >
                            ✎
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {jdEdit && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
          <div
            role="dialog"
            aria-label={`Job description for ${jdEdit.company}`}
            className="flex max-h-[80vh] w-full max-w-2xl flex-col gap-3 rounded-xl bg-white p-5 shadow-xl dark:bg-gray-900"
          >
            <h2 className="text-sm font-bold text-gray-900 dark:text-gray-100">
              Job description — {jdEdit.company}
            </h2>
            {jdEdit.loading ? (
              <Skeleton className="h-40 w-full" />
            ) : (
              <textarea
                aria-label={`Job description text for ${jdEdit.company}`}
                value={jdEdit.text}
                onChange={(e) => setJdEdit({ ...jdEdit, text: e.target.value })}
                rows={12}
                className="min-h-40 w-full flex-1 rounded-lg border border-gray-300 bg-white p-3 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
              />
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setJdEdit(null)}
                className="rounded-lg px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={jdEdit.loading || saveJdMutation.isPending || jdEdit.text.trim().length === 0}
                onClick={() =>
                  saveJdMutation.mutate({ jobId: jdEdit.jobId, text: jdEdit.text.trim() })
                }
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:bg-gray-400"
              >
                Save description
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
