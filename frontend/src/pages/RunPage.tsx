import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../api/client';
import { useToast } from '../context/useToast';
import { ErrorState } from '../components/common/ErrorState';
import type { RunStageItem, RunStatusResponse } from '../api/types';

function isGmailAuthError(stage: RunStageItem): boolean {
  const err = (stage.error || '').toLowerCase();
  const name = (stage.name || '').toLowerCase();
  return (
    name.includes('alert') ||
    name.includes('gmail') ||
    err.includes('auth') ||
    err.includes('token') ||
    err.includes('credentials') ||
    err.includes('gmail')
  );
}

type SourceLine = { name: string; line: string; ok: boolean };

function sourceLines(counts: unknown): SourceLine[] | null {
  if (!counts || typeof counts !== 'object') return null;
  const obj = counts as Record<string, unknown>;
  if (Array.isArray(obj.sources)) {
    return obj.sources.flatMap((item) => {
      if (!item || typeof item !== 'object') return [];
      const row = item as Record<string, unknown>;
      const name = String(row.name ?? 'source');
      const failed = row.status === 'failed';
      const found = Number(row.found ?? 0);
      const fresh = Number(row.new ?? 0);
      const line = failed
        ? String(row.error || 'source unavailable')
        : `${found} found / ${fresh} new`;
      return [{ name, line, ok: !failed }];
    });
  }
  if (obj.sender_counts && typeof obj.sender_counts === 'object') {
    return Object.entries(obj.sender_counts as Record<string, unknown>).map(([name, count]) => ({
      name,
      line: `${count} jobs`,
      ok: true,
    }));
  }
  return null;
}

function formatCounts(counts: unknown): string {
  if (!counts) return '';
  if (typeof counts === 'string') return counts;
  if (typeof counts === 'number') return String(counts);
  if (typeof counts === 'object') {
    const obj = counts as Record<string, unknown>;
    if (sourceLines(counts)) {
      const parts: string[] = [];
      if (typeof obj.total_fetched === 'number') parts.push(`${obj.total_fetched} found`);
      if (typeof obj.new_jobs === 'number') parts.push(`${obj.new_jobs} new`);
      if (typeof obj.total_listings === 'number') parts.push(`${obj.total_listings} listings`);
      return parts.join(' · ');
    }
    if (typeof obj.text === 'string') return obj.text;
    if (typeof obj.message === 'string') return obj.message;
    return Object.entries(obj)
      .map(([k, v]) => `${k}: ${v}`)
      .join(', ');
  }
  return String(counts);
}

export function RunPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  // Remember the last result while the page is open
  const [lastResult, setLastResult] = useState<RunStatusResponse | null>(null);

  // Track if finished invalidation has already fired for this run
  const lastInvalidatedRef = useRef<RunStatusResponse | null>(null);

  // Poll status every 1 second while state is 'running'
  const {
    data: runStatus,
    isError: isStatusError,
    error: statusError,
    refetch: refetchStatus,
  } = useQuery({
    queryKey: ['run-status'],
    queryFn: api.getRunStatus,
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state === 'running' ? 1000 : false;
    },
  });

  const isRunActiveOrHasData =
    runStatus &&
    (runStatus.state === 'running' ||
      runStatus.state === 'finished' ||
      runStatus.state === 'failed' ||
      (runStatus.stages && runStatus.stages.length > 0));

  const activeStatus = isRunActiveOrHasData ? runStatus : lastResult || runStatus;
  const isRunning = activeStatus?.state === 'running';

  // Keep last result updated when finished or failed
  useEffect(() => {
    if (runStatus) {
      if (runStatus.state === 'finished' || runStatus.state === 'failed' || runStatus.summary) {
        setLastResult(runStatus);
      }
    }
  }, [runStatus]);

  // Handle finished run invalidation
  useEffect(() => {
    if (runStatus && runStatus.state === 'finished' && lastInvalidatedRef.current !== runStatus) {
      lastInvalidatedRef.current = runStatus;
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      queryClient.invalidateQueries({ queryKey: ['needs-jd'] });
      queryClient.invalidateQueries({ queryKey: ['progress'] });
      queryClient.invalidateQueries({ queryKey: ['applications'] });
      queryClient.invalidateQueries({ queryKey: ['follow-ups'] });
    }
  }, [runStatus, queryClient]);

  // Trigger Run mutation
  const triggerMutation = useMutation({
    mutationFn: api.triggerRun,
    onSuccess: (data) => {
      showToast('Daily pipeline started', 'info');
      queryClient.setQueryData(['run-status'], data);
      refetchStatus();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        // Run already active: just attach to its status
        showToast('A pipeline run is already active. Attaching to live status.', 'info');
        refetchStatus();
        return;
      }
      const msg = err instanceof ApiError ? err.detail : err.message;
      showToast(msg, 'error');
    },
  });

  const handleStartRun = () => {
    if (!isRunning && !triggerMutation.isPending) {
      triggerMutation.mutate();
    }
  };

  const stages = activeStatus?.stages || [];
  const failedStages = stages.filter(
    (st) => st.status === 'failed' || st.status === 'error' || Boolean(st.error)
  );
  const hasGmailAuthFailure = failedStages.some(isGmailAuthError);

  return (
    <div className="p-8 max-w-5xl mx-auto space-y-8 overflow-y-auto h-full">
      {/* Header & Trigger Button */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-gray-200 dark:border-gray-800">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            Daily Pipeline Runner
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Ingests listings, extracts requirements with LLM, filters, scores against your profile, and updates the daily queue.
          </p>
        </div>

        <button
          type="button"
          onClick={handleStartRun}
          disabled={isRunning || triggerMutation.isPending}
          aria-label="Run daily pipeline"
          className="inline-flex items-center justify-center gap-2 px-5 py-2.5 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-400 dark:disabled:bg-gray-700 disabled:cursor-not-allowed rounded-xl shadow-xs transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-gray-900 flex-shrink-0"
        >
          {isRunning ? (
            <>
              <span className="animate-spin">⏳</span>
              <span>Running Pipeline...</span>
            </>
          ) : (
            <>
              <span>▶</span>
              <span>Run daily pipeline</span>
            </>
          )}
        </button>
      </div>

      {/* Query Error State */}
      {isStatusError && (
        <ErrorState
          title="Failed to fetch pipeline status"
          message={statusError instanceof Error ? statusError.message : 'Unknown error'}
          onRetry={() => refetchStatus()}
        />
      )}

      {/* Prominent Global Failure Alert */}
      {activeStatus?.state === 'failed' && (
        <div
          role="alert"
          data-testid="run-failed-alert"
          className="p-5 bg-red-50 dark:bg-red-950/40 border-l-4 border-red-600 rounded-xl space-y-1 text-red-900 dark:text-red-200"
        >
          <div className="flex items-center gap-2 font-bold text-sm">
            <span>⚠️</span>
            <span>Pipeline Execution Failed</span>
          </div>
          <p className="text-xs text-red-700 dark:text-red-300 font-mono">
            {activeStatus.error || 'An unexpected error caused the pipeline run to abort.'}
          </p>
        </div>
      )}

      {/* Prominent Failed Stage Banner */}
      {failedStages.length > 0 && (
        <div
          role="alert"
          data-testid="failed-stages-alert"
          className="p-5 bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-900 rounded-xl space-y-3"
        >
          <div className="flex items-center gap-2 text-rose-900 dark:text-rose-200 font-bold text-sm">
            <span>❌</span>
            <span>Failed Stage(s) Detected</span>
          </div>

          <div className="space-y-2">
            {failedStages.map((st) => (
              <div
                key={st.name}
                className="text-xs bg-white/60 dark:bg-black/30 p-3 rounded-lg border border-rose-200 dark:border-rose-900/60"
              >
                <div className="font-semibold text-rose-800 dark:text-rose-300">
                  Stage: {st.name}
                </div>
                {st.error && (
                  <div className="text-rose-600 dark:text-rose-400 mt-0.5 font-mono">
                    {st.error}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Special Gmail / Auth Re-authorization Guidance */}
          {hasGmailAuthFailure && (
            <div
              data-testid="gmail-auth-hint"
              className="p-3 bg-amber-50 dark:bg-amber-950/60 border border-amber-300 dark:border-amber-800 rounded-lg text-xs text-amber-900 dark:text-amber-200 font-medium"
            >
              Re-authorize by running <code className="font-mono bg-white dark:bg-black/40 px-1 py-0.5 rounded text-amber-950 dark:text-amber-100 font-bold">jobpilot ingest-alerts</code> in a terminal
            </div>
          )}
        </div>
      )}

      {/* Finished Summary Card */}
      {activeStatus?.state === 'finished' && activeStatus.summary && (
        <div
          data-testid="run-summary-card"
          className="p-6 bg-emerald-50/60 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 rounded-xl space-y-4"
        >
          <div className="flex items-center justify-between">
            <h3 className="text-base font-bold text-emerald-900 dark:text-emerald-200 flex items-center gap-2">
              <span>✓</span>
              <span>Pipeline Run Finished Successfully</span>
            </h3>
            {activeStatus.finished_at && (
              <span className="text-xs text-emerald-700 dark:text-emerald-400">
                Completed: {activeStatus.finished_at}
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="p-3 bg-white dark:bg-gray-900 rounded-lg border border-emerald-100 dark:border-emerald-900/60">
              <span className="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                New Jobs
              </span>
              <span className="text-2xl font-bold text-gray-900 dark:text-gray-100">
                {activeStatus.summary.new_jobs}
              </span>
            </div>
            <div className="p-3 bg-white dark:bg-gray-900 rounded-lg border border-emerald-100 dark:border-emerald-900/60">
              <span className="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                Queued Count
              </span>
              <span className="text-2xl font-bold text-gray-900 dark:text-gray-100">
                {activeStatus.summary.queued_count}
              </span>
            </div>
            <div className="p-3 bg-white dark:bg-gray-900 rounded-lg border border-emerald-100 dark:border-emerald-900/60">
              <span className="block text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
                Tier A Count
              </span>
              <span className="text-2xl font-bold text-indigo-600 dark:text-indigo-400">
                {activeStatus.summary.tier_a_count}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Live Stages Timeline / List */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            Pipeline Stages {isRunning && '(Live)'}
          </h2>
          {activeStatus?.state && (
            <span
              data-testid="run-state-badge"
              className={`px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider ${
                activeStatus.state === 'running'
                  ? 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300'
                  : activeStatus.state === 'finished'
                  ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
                  : activeStatus.state === 'failed'
                  ? 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300'
                  : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'
              }`}
            >
              State: {activeStatus.state}
            </span>
          )}
        </div>

        {stages.length === 0 ? (
          <div className="p-8 text-center text-xs text-gray-400 dark:text-gray-500 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl">
            No active or recent pipeline stages recorded. Click "Run daily pipeline" to begin.
          </div>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-800 border border-gray-200 dark:border-gray-800 rounded-xl bg-white dark:bg-gray-900 overflow-hidden shadow-xs">
            {stages.map((stage) => {
              const stageStatus = stage.status.toLowerCase();
              const formattedCountsText = formatCounts(stage.counts);
              const lines = sourceLines(stage.counts);

              return (
                <div
                  key={stage.name}
                  data-testid={`stage-item-${stage.name}`}
                  className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs"
                >
                  <div className="space-y-0.5">
                    <span className="font-bold text-gray-900 dark:text-gray-100">
                      {stage.name}
                    </span>
                    {formattedCountsText && (
                      <span className="text-gray-500 dark:text-gray-400 block font-mono text-[11px]">
                        {formattedCountsText}
                      </span>
                    )}
                    {lines && lines.length > 0 && (
                      <ul className="mt-2 space-y-1" data-testid={`source-lines-${stage.name}`}>
                        {lines.map((item) => (
                          <li key={item.name} className="font-mono text-[11px] text-gray-600 dark:text-gray-300">
                            <span className={item.ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}>
                              {item.ok ? '✓' : '✗'}
                            </span>{' '}
                            <span className="font-semibold">{item.name}</span>{' '}
                            <span>{item.line}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {stage.error && (
                      <span className="text-red-600 dark:text-red-400 block font-mono text-[11px]">
                        {stage.error}
                      </span>
                    )}
                  </div>

                  <div className="flex-shrink-0">
                    <span
                      data-testid={`stage-status-${stage.name}`}
                      className={`px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider ${
                        stageStatus === 'done' || stageStatus === 'ok' || stageStatus === 'finished'
                          ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
                          : stageStatus === 'running'
                          ? 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 animate-pulse'
                          : stageStatus === 'failed' || stageStatus === 'error'
                          ? 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300'
                          : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                      }`}
                    >
                      {stageStatus === 'running' && '⏳ '}
                      {stageStatus}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
