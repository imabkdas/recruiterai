import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../api/client';
import { useToast } from '../context/useToast';
import { ProgressStrip } from '../components/today/ProgressStrip';
import { QueueList } from '../components/today/QueueList';
import { JobDetailView } from '../components/today/JobDetailView';
import { MarkAppliedDialog } from '../components/today/MarkAppliedDialog';
import { ResumeEditorDialog } from '../components/today/ResumeEditorDialog';
import { ShortcutHelpDialog } from '../components/today/ShortcutHelpDialog';
import { JobDetailSkeleton } from '../components/common/Skeleton';
import { ErrorState } from '../components/common/ErrorState';
import { EmptyState } from '../components/common/EmptyState';
import { safeExternalUrl } from '../lib/safeUrl';
import type { ProgressSummary, QueueItem } from '../api/types';

function isTyping(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return (
    tag === 'input' ||
    tag === 'textarea' ||
    tag === 'select' ||
    target.isContentEditable
  );
}

function getNextSelectedJobId(currentQueue: QueueItem[], removedJobId: number): number | null {
  const currentIndex = currentQueue.findIndex((item) => item.job_id === removedJobId);
  const remaining = currentQueue.filter((item) => item.job_id !== removedJobId);
  if (remaining.length === 0) return null;
  if (currentIndex === -1) return remaining[0].job_id;
  const nextIndex = Math.min(currentIndex, remaining.length - 1);
  return remaining[nextIndex].job_id;
}

export function TodayPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [isMarkDialogOpen, setIsMarkDialogOpen] = useState(false);
  const [isResumeOpen, setIsResumeOpen] = useState(false);
  const [isShortcutHelpOpen, setIsShortcutHelpOpen] = useState(false);

  // 1. Fetch Progress Metrics
  const { data: progress, isLoading: isLoadingProgress } = useQuery({
    queryKey: ['progress'],
    queryFn: api.getProgress,
  });

  // 2. Fetch Follow-ups
  const { data: followUps } = useQuery({
    queryKey: ['follow-ups'],
    queryFn: () => api.getFollowUps(),
  });

  // 3. Fetch Ranked Queue
  const {
    data: queue = [],
    isLoading: isLoadingQueue,
    isError: isQueueError,
    error: queueError,
    refetch: refetchQueue,
  } = useQuery({
    queryKey: ['queue'],
    queryFn: () => api.getQueue(),
  });

  const jobParam = searchParams.get('job');
  const selectedJobId = jobParam ? parseInt(jobParam, 10) : null;

  // Auto-select first job if not specified or invalid
  useEffect(() => {
    if (queue.length > 0) {
      const exists = queue.some((item) => item.job_id === selectedJobId);
      if (!selectedJobId || !exists) {
        setSearchParams({ job: String(queue[0].job_id) }, { replace: true });
      }
    }
  }, [queue, selectedJobId, setSearchParams]);

  // 4. Fetch Selected Job Details
  const activeJobId =
    selectedJobId !== null && !isNaN(selectedJobId)
      ? selectedJobId
      : queue.length > 0
      ? queue[0].job_id
      : null;

  const {
    data: selectedJob,
    isLoading: isLoadingJob,
    isError: isJobError,
    error: jobError,
    refetch: refetchJob,
  } = useQuery({
    queryKey: ['job', activeJobId],
    queryFn: () => api.getJobDetail(activeJobId!),
    enabled: activeJobId !== null,
  });

  const handleSelectJob = useCallback(
    (jobId: number) => {
      setSearchParams({ job: String(jobId) });
    },
    [setSearchParams]
  );

  // ---------------------------------------------------------------------------
  // Mutations: Prepare, Mark Applied, Skip
  // ---------------------------------------------------------------------------

  // 1. Prepare
  const prepareMutation = useMutation({
    mutationFn: (jobId: number) => api.prepareJob(jobId),
    onSuccess: (_data, jobId) => {
      showToast('Application drafts prepared!', 'success');
      queryClient.invalidateQueries({ queryKey: ['job', jobId] });
      queryClient.setQueryData<QueueItem[]>(['queue'], (old = []) =>
        old.map((item) => (item.job_id === jobId ? { ...item, prepared: 'y' } : item))
      );
    },
    onError: (error) => {
      const msg = error instanceof ApiError ? error.detail : error.message;
      showToast(msg, 'error');
    },
  });

  // 2. Mark Applied (with optimistic updates)
  const markMutation = useMutation({
    mutationFn: (vars: {
      jobId: number;
      channel: string;
      referral_contact?: string | null;
      note?: string | null;
    }) =>
      api.markJob(vars.jobId, {
        status: 'applied',
        channel: vars.channel,
        referral_contact: vars.referral_contact,
        note: vars.note,
      }),
    onMutate: async ({ jobId }) => {
      await queryClient.cancelQueries({ queryKey: ['queue'] });
      await queryClient.cancelQueries({ queryKey: ['progress'] });

      const prevQueue = queryClient.getQueryData<QueueItem[]>(['queue']) || [];
      const prevProgress = queryClient.getQueryData<ProgressSummary>(['progress']);
      const prevJobId = activeJobId;

      const nextJobId = getNextSelectedJobId(prevQueue, jobId);
      const nextQueue = prevQueue.filter((j) => j.job_id !== jobId);

      queryClient.setQueryData<QueueItem[]>(['queue'], nextQueue);
      if (nextJobId !== null) {
        setSearchParams({ job: String(nextJobId) });
      } else {
        setSearchParams({});
      }

      if (prevProgress) {
        queryClient.setQueryData<ProgressSummary>(['progress'], {
          ...prevProgress,
          applied_today: prevProgress.applied_today + 1,
          applied_this_week: prevProgress.applied_this_week + 1,
        });
      }

      return { prevQueue, prevProgress, prevJobId };
    },
    onError: (error, _vars, context) => {
      if (context?.prevQueue) {
        queryClient.setQueryData<QueueItem[]>(['queue'], context.prevQueue);
      }
      if (context?.prevProgress) {
        queryClient.setQueryData<ProgressSummary>(['progress'], context.prevProgress);
      }
      if (context?.prevJobId !== undefined && context?.prevJobId !== null) {
        setSearchParams({ job: String(context.prevJobId) });
      }

      const msg = error instanceof ApiError ? error.detail : error.message;
      showToast(msg, 'error');
    },
    onSuccess: () => {
      showToast('Marked as applied!', 'success');
      setIsMarkDialogOpen(false);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      queryClient.invalidateQueries({ queryKey: ['progress'] });
      queryClient.invalidateQueries({ queryKey: ['follow-ups'] });
    },
  });

  // 3. Skip (with optimistic updates)
  const skipMutation = useMutation({
    mutationFn: (jobId: number) => api.skipJob(jobId),
    onMutate: async (jobId) => {
      await queryClient.cancelQueries({ queryKey: ['queue'] });

      const prevQueue = queryClient.getQueryData<QueueItem[]>(['queue']) || [];
      const prevProgress = queryClient.getQueryData<ProgressSummary>(['progress']);
      const prevJobId = activeJobId;

      const nextJobId = getNextSelectedJobId(prevQueue, jobId);
      const nextQueue = prevQueue.filter((j) => j.job_id !== jobId);

      queryClient.setQueryData<QueueItem[]>(['queue'], nextQueue);
      if (nextJobId !== null) {
        setSearchParams({ job: String(nextJobId) });
      } else {
        setSearchParams({});
      }

      return { prevQueue, prevProgress, prevJobId };
    },
    onError: (error, _vars, context) => {
      if (context?.prevQueue) {
        queryClient.setQueryData<QueueItem[]>(['queue'], context.prevQueue);
      }
      if (context?.prevProgress) {
        queryClient.setQueryData<ProgressSummary>(['progress'], context.prevProgress);
      }
      if (context?.prevJobId !== undefined && context?.prevJobId !== null) {
        setSearchParams({ job: String(context.prevJobId) });
      }

      const msg = error instanceof ApiError ? error.detail : error.message;
      showToast(msg, 'error');
    },
    onSuccess: () => {
      showToast('Job skipped', 'info');
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
    },
  });

  // ---------------------------------------------------------------------------
  // Action Handlers
  // ---------------------------------------------------------------------------

  const handlePrepare = useCallback(() => {
    if (activeJobId && !prepareMutation.isPending) {
      prepareMutation.mutate(activeJobId);
    }
  }, [activeJobId, prepareMutation]);

  const handleOpenMarkApplied = useCallback(() => {
    if (activeJobId) {
      setIsMarkDialogOpen(true);
    }
  }, [activeJobId]);

  const handleSubmitMarkApplied = (data: {
    channel: string;
    referral_contact?: string | null;
    note?: string | null;
  }) => {
    if (activeJobId) {
      markMutation.mutate({
        jobId: activeJobId,
        ...data,
      });
    }
  };

  const handleSkip = useCallback(() => {
    if (activeJobId && !skipMutation.isPending) {
      skipMutation.mutate(activeJobId);
    }
  }, [activeJobId, skipMutation]);

  const handleOpenJobLink = useCallback(() => {
    const rawUrl = selectedJob?.url || selectedJob?.apply_url;
    const safeUrl = safeExternalUrl(rawUrl);
    if (safeUrl) {
      window.open(safeUrl, '_blank', 'noopener,noreferrer');
    }
  }, [selectedJob]);

  // ---------------------------------------------------------------------------
  // Keyboard Shortcuts (Requirement 5)
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore if typing in input, textarea, select
      if (isTyping(e.target)) {
        return;
      }

      // Dialog is open: only Esc is processed (closing dialogs)
      const isAnyDialogOpen = isMarkDialogOpen || isShortcutHelpOpen;
      if (isAnyDialogOpen) {
        if (e.key === 'Escape') {
          setIsMarkDialogOpen(false);
          setIsShortcutHelpOpen(false);
        }
        return;
      }

      // Esc closes dialogs if any was open (already handled above)
      if (e.key === 'Escape') {
        setIsMarkDialogOpen(false);
        setIsShortcutHelpOpen(false);
        return;
      }

      // Ignore if modifier keys are pressed (e.g. Cmd+C, Ctrl+R)
      if (e.metaKey || e.ctrlKey || e.altKey) {
        return;
      }

      switch (e.key) {
        case '?':
          e.preventDefault();
          setIsShortcutHelpOpen(true);
          break;

        case 'j': {
          e.preventDefault();
          if (queue.length === 0) break;
          const idx = queue.findIndex((item) => item.job_id === activeJobId);
          if (idx !== -1 && idx < queue.length - 1) {
            handleSelectJob(queue[idx + 1].job_id);
          }
          break;
        }

        case 'k': {
          e.preventDefault();
          if (queue.length === 0) break;
          const idx = queue.findIndex((item) => item.job_id === activeJobId);
          if (idx > 0) {
            handleSelectJob(queue[idx - 1].job_id);
          }
          break;
        }

        case 'o':
          e.preventDefault();
          handleOpenJobLink();
          break;

        case 'p':
          e.preventDefault();
          handlePrepare();
          break;

        case 'a':
          e.preventDefault();
          handleOpenMarkApplied();
          break;

        case 's':
          e.preventDefault();
          handleSkip();
          break;

        default:
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    activeJobId,
    handleOpenJobLink,
    handleOpenMarkApplied,
    handlePrepare,
    handleSelectJob,
    handleSkip,
    isMarkDialogOpen,
    isShortcutHelpOpen,
    queue,
  ]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header Progress Strip & Follow-up Banner */}
      <ProgressStrip
        progress={progress}
        isLoading={isLoadingProgress}
        followUps={followUps}
      />

      {/* Split View Content */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left Column: Ranked Queue */}
        <div className="w-full md:w-96 border-r border-gray-200 dark:border-gray-800 flex flex-col flex-shrink-0 bg-white dark:bg-gray-900 overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between flex-shrink-0">
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Ranked Queue ({queue.length})
            </h3>
            <button
              type="button"
              onClick={() => setIsShortcutHelpOpen(true)}
              aria-label="View keyboard shortcuts"
              className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-750 font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              ? keys
            </button>
          </div>

          <div className="flex-1 overflow-y-auto">
            {isQueueError ? (
              <ErrorState
                title="Failed to load queue"
                message={queueError instanceof Error ? queueError.message : 'Unknown error'}
                onRetry={() => refetchQueue()}
              />
            ) : (
              <QueueList
                items={queue}
                selectedJobId={activeJobId}
                onSelectJob={handleSelectJob}
                isLoading={isLoadingQueue}
              />
            )}
          </div>
        </div>

        {/* Right Column: Selected Job Details */}
        <div className="flex-1 flex flex-col min-w-0 bg-white dark:bg-gray-950 overflow-hidden">
          {isQueueError ? (
            <div className="p-8 text-center text-sm text-gray-400">
              Select a job once the queue is loaded.
            </div>
          ) : queue.length === 0 && !isLoadingQueue ? (
            <div className="flex-1 flex items-center justify-center p-8">
              <EmptyState
                title="No job selected"
                message="Add jobs to the pipeline to see details."
              />
            </div>
          ) : isJobError ? (
            <div className="p-6">
              <ErrorState
                title="Failed to load job details"
                message={jobError instanceof Error ? jobError.message : 'Unknown error'}
                onRetry={() => refetchJob()}
              />
            </div>
          ) : isLoadingJob ? (
            <JobDetailSkeleton />
          ) : selectedJob ? (
            <JobDetailView
              key={selectedJob.id}
              job={selectedJob}
              onPrepare={handlePrepare}
              isPreparing={prepareMutation.isPending}
              onOpenMarkApplied={handleOpenMarkApplied}
              onOpenResume={() => setIsResumeOpen(true)}
              onSkip={handleSkip}
              isSkipping={skipMutation.isPending}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
              Select a job from the queue to view details.
            </div>
          )}
        </div>
      </div>

      {/* Mark Applied Modal Dialog */}
      {selectedJob && (
        <MarkAppliedDialog
          isOpen={isMarkDialogOpen}
          jobId={selectedJob.id}
          company={selectedJob.company}
          title={selectedJob.title}
          onClose={() => setIsMarkDialogOpen(false)}
          onSubmit={handleSubmitMarkApplied}
          isSubmitting={markMutation.isPending}
        />
      )}

      {selectedJob && (
        <ResumeEditorDialog
          isOpen={isResumeOpen}
          jobId={selectedJob.id}
          company={selectedJob.company}
          title={selectedJob.title}
          onClose={() => setIsResumeOpen(false)}
        />
      )}

      {/* Keyboard Shortcuts Help Overlay */}
      <ShortcutHelpDialog
        isOpen={isShortcutHelpOpen}
        onClose={() => setIsShortcutHelpOpen(false)}
      />
    </div>
  );
}
