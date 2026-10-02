import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../api/client';
import { useToast } from '../context/useToast';
import { safeExternalUrl } from '../lib/safeUrl';
import { ErrorState } from '../components/common/ErrorState';
import { EmptyState } from '../components/common/EmptyState';
import { Skeleton } from '../components/common/Skeleton';
import type { NeedsJdItem } from '../api/types';

const JD_CHAR_LIMIT = 200000;

export function NeedsJdPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [pastedTexts, setPastedTexts] = useState<Record<number, string>>({});

  const {
    data: items = [],
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ['needs-jd'],
    queryFn: api.getNeedsJd,
  });

  const addJdMutation = useMutation({
    mutationFn: ({ jobId, text }: { jobId: number; text: string }) =>
      api.addJd(jobId, text),
    onSuccess: (_data, { jobId }) => {
      // Optimistically remove from list
      queryClient.setQueryData<NeedsJdItem[]>(['needs-jd'], (old = []) =>
        old.filter((item) => item.job_id !== jobId)
      );

      // Clear pasted text for this job
      setPastedTexts((prev) => {
        const copy = { ...prev };
        delete copy[jobId];
        return copy;
      });

      showToast(
        'Added. Run the pipeline to analyze and score it.',
        'success',
        { label: 'Run screen', href: '/run' }
      );
    },
    onError: (err) => {
      const msg = err instanceof ApiError ? err.detail : err.message;
      showToast(msg, 'error');
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['needs-jd'] });
      queryClient.invalidateQueries({ queryKey: ['queue'] });
    },
  });

  const handleTextChange = (jobId: number, text: string) => {
    setPastedTexts((prev) => ({ ...prev, [jobId]: text }));
  };

  const handleSubmit = (e: React.FormEvent, jobId: number) => {
    e.preventDefault();
    const text = (pastedTexts[jobId] || '').trim();
    if (text.length === 0 || text.length > JD_CHAR_LIMIT) {
      return;
    }
    addJdMutation.mutate({ jobId, text });
  };

  if (isLoading) {
    return (
      <div className="p-8 max-w-5xl mx-auto space-y-6">
        <Skeleton className="h-8 w-48 mb-2" />
        <Skeleton className="h-4 w-72 mb-8" />
        <div className="space-y-4">
          <Skeleton className="h-48 w-full rounded-xl" />
          <Skeleton className="h-48 w-full rounded-xl" />
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-8 max-w-5xl mx-auto">
        <ErrorState
          title="Failed to load jobs needing descriptions"
          message={error instanceof Error ? error.message : 'Unknown error'}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  return (
    <div className="p-8 max-w-5xl mx-auto space-y-6 overflow-y-auto h-full">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          Needs Job Description
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          These listings were discovered via alerts or feeds without a full JD. Paste the full description to enable LLM extraction and scoring.
        </p>
      </div>

      {/* Empty State */}
      {items.length === 0 ? (
        <EmptyState
          title="No jobs waiting for descriptions"
          message="All queued jobs currently have their full descriptions attached. Check back after running new ingest sources."
        />
      ) : (
        <div className="space-y-6">
          {items.map((item) => {
            const safeUrl = safeExternalUrl(item.url);
            const currentText = pastedTexts[item.job_id] || '';
            const charCount = currentText.length;
            const isOverLimit = charCount > JD_CHAR_LIMIT;
            const isSubmitDisabled =
              charCount === 0 || isOverLimit || addJdMutation.isPending;

            const extraItem = item as { snippet?: string; source?: string };

            return (
              <div
                key={item.job_id}
                data-testid={`needs-jd-card-${item.job_id}`}
                className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-6 shadow-xs space-y-4"
              >
                {/* Job Metadata Header */}
                <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-indigo-600 dark:text-indigo-400">
                        {item.company}
                      </span>
                      {extraItem.source && (
                        <>
                          <span className="text-gray-300 dark:text-gray-700">•</span>
                          <span className="text-xs text-gray-500 dark:text-gray-400">
                            Source: {extraItem.source}
                          </span>
                        </>
                      )}
                    </div>
                    <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                      {item.title}
                    </h2>
                    {item.location && (
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        📍 {item.location}
                      </p>
                    )}
                  </div>

                  {/* Safe Open Job Link */}
                  <div className="flex-shrink-0">
                    {safeUrl ? (
                      <a
                        href={safeUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label="Open job link"
                        className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-750 text-gray-800 dark:text-gray-200 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      >
                        <span>Open job</span>
                        <span>↗</span>
                      </a>
                    ) : (
                      <span
                        aria-disabled="true"
                        aria-label="Invalid link"
                        className="inline-flex items-center px-3 py-1.5 text-xs font-medium bg-gray-100 dark:bg-gray-800 text-gray-400 dark:text-gray-500 rounded-lg cursor-not-allowed border border-gray-200 dark:border-gray-700"
                      >
                        Invalid link
                      </span>
                    )}
                  </div>
                </div>

                {/* Optional Snippet */}
                {extraItem.snippet && (
                  <div className="p-3 bg-gray-50 dark:bg-gray-850 rounded-lg border border-gray-100 dark:border-gray-800 text-xs text-gray-600 dark:text-gray-400 whitespace-pre-wrap">
                    {extraItem.snippet}
                  </div>
                )}

                {/* JD Input Form */}
                <form
                  onSubmit={(e) => handleSubmit(e, item.job_id)}
                  className="space-y-3"
                >
                  <div>
                    <label
                      htmlFor={`jd-input-${item.job_id}`}
                      className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1"
                    >
                      Paste Full Job Description:
                    </label>
                    <textarea
                      id={`jd-input-${item.job_id}`}
                      rows={5}
                      value={currentText}
                      onChange={(e) => handleTextChange(item.job_id, e.target.value)}
                      placeholder="Paste the full job description text here..."
                      className="w-full text-xs font-mono p-3 bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 resize-y"
                    />
                  </div>

                  {/* Character Counter & Submit Button */}
                  <div className="flex items-center justify-between">
                    <span
                      data-testid={`char-count-${item.job_id}`}
                      className={`text-xs ${
                        isOverLimit
                          ? 'text-red-600 dark:text-red-400 font-bold'
                          : 'text-gray-500 dark:text-gray-400'
                      }`}
                    >
                      {charCount.toLocaleString()} / {JD_CHAR_LIMIT.toLocaleString()} characters
                      {isOverLimit && ' (exceeds limit)'}
                    </span>

                    <button
                      type="submit"
                      disabled={isSubmitDisabled}
                      aria-label={`Submit job description for ${item.company}`}
                      className="inline-flex items-center justify-center px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg shadow-xs transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-gray-900"
                    >
                      {addJdMutation.isPending ? 'Saving...' : 'Submit Job Description'}
                    </button>
                  </div>
                </form>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
