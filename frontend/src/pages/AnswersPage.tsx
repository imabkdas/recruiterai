import { useState, type FormEvent } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../api/client';
import { useToast } from '../context/useToast';
import { ErrorState } from '../components/common/ErrorState';
import { EmptyState } from '../components/common/EmptyState';
import { Skeleton } from '../components/common/Skeleton';
import type { AnswerBankItem, AnswerResult, QueueItem } from '../api/types';

const NEEDS_INPUT = 'NEEDS YOUR INPUT';

function categoryLabel(category: string): string {
  if (category === 'setting') return 'Personal setting';
  if (category === 'profile_fact') return 'From your profile';
  if (category === 'free_text') return 'Draft';
  return category;
}

export function AnswersPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [questionInput, setQuestionInput] = useState('');
  const [selectedJobId, setSelectedJobId] = useState<string>('');
  const [activeResult, setActiveResult] = useState<AnswerResult | null>(null);
  const [suppliedAnswerInput, setSuppliedAnswerInput] = useState('');
  const [editingItem, setEditingItem] = useState<AnswerBankItem | null>(null);
  const [editingAnswerText, setEditingAnswerText] = useState('');

  const { data: queue = [] } = useQuery<QueueItem[]>({
    queryKey: ['queue'],
    queryFn: () => api.getQueue(),
  });

  const {
    data: bankItems = [],
    isLoading: isLoadingBank,
    isError: isBankError,
    error: bankError,
    refetch: refetchBank,
  } = useQuery<AnswerBankItem[]>({
    queryKey: ['answers'],
    queryFn: api.getAnswers,
  });

  const askMutation = useMutation({
    mutationFn: ({ question, jobId }: { question: string; jobId?: number | null }) =>
      api.askQuestion(question, jobId),
    onSuccess: (data) => {
      setActiveResult(data);
      setSuppliedAnswerInput('');
      queryClient.invalidateQueries({ queryKey: ['answers'] });
      showToast('Answer retrieved', 'info');
    },
    onError: (err) => {
      const msg = err instanceof ApiError ? err.detail : err.message;
      showToast(msg, 'error');
    },
  });

  const setAnswerMutation = useMutation({
    mutationFn: ({ answerId, answerText }: { answerId: number; answerText: string }) =>
      api.setAnswer(answerId, { answer_text: answerText }),
    onSuccess: () => {
      showToast('Answer saved and approved', 'success');
      queryClient.invalidateQueries({ queryKey: ['answers'] });
      const saved = suppliedAnswerInput || editingAnswerText;
      if (activeResult && activeResult.answer_bank_id) {
        setActiveResult((prev) =>
          prev
            ? {
                ...prev,
                answer: saved,
                is_draft: false,
              }
            : null
        );
      }
      setEditingItem(null);
      setEditingAnswerText('');
      setSuppliedAnswerInput('');
    },
    onError: (err) => {
      const msg = err instanceof ApiError ? err.detail : err.message;
      showToast(msg, 'error');
    },
  });

  const approveMutation = useMutation({
    mutationFn: (answerId: number) => api.approveAnswer(answerId),
    onSuccess: () => {
      showToast('Answer approved', 'success');
      queryClient.invalidateQueries({ queryKey: ['answers'] });
      setActiveResult((prev) => (prev ? { ...prev, is_draft: false } : null));
    },
    onError: (err) => {
      const msg = err instanceof ApiError ? err.detail : err.message;
      showToast(msg, 'error');
    },
  });

  const handleAsk = (event: FormEvent) => {
    event.preventDefault();
    const question = questionInput.trim();
    if (!question) return;
    const jobId = selectedJobId ? Number(selectedJobId) : null;
    askMutation.mutate({ question, jobId });
  };

  const needsInput = activeResult?.answer === NEEDS_INPUT;

  if (isLoadingBank) {
    return (
      <div className="h-full space-y-6 overflow-y-auto p-8">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-96" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    );
  }

  if (isBankError) {
    return (
      <div className="h-full p-8">
        <ErrorState
          title="Failed to load answers"
          message={bankError instanceof Error ? bankError.message : 'Unknown error'}
          onRetry={() => refetchBank()}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col gap-6 overflow-y-auto p-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Answers</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Save replies for personal questions on an application form, such as notice period,
          salary, visa, or relocation. JobPilot uses your profile when it can. Blank settings
          stay as {NEEDS_INPUT} until you fill them in here. Nothing is submitted for you.
        </p>
      </div>

      <form
        onSubmit={handleAsk}
        className="space-y-3 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
      >
        <label htmlFor="answer-question" className="text-xs font-semibold text-gray-600 dark:text-gray-400">
          Application question
        </label>
        <textarea
          id="answer-question"
          value={questionInput}
          onChange={(e) => setQuestionInput(e.target.value)}
          rows={3}
          placeholder="What is your notice period?"
          className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
        />
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex-1">
            <label htmlFor="answer-job" className="text-xs font-semibold text-gray-600 dark:text-gray-400">
              Optional job
            </label>
            <select
              id="answer-job"
              aria-label="Optional job for this question"
              value={selectedJobId}
              onChange={(e) => setSelectedJobId(e.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800"
            >
              <option value="">No specific job</option>
              {queue.map((job) => (
                <option key={job.job_id} value={job.job_id}>
                  {job.company} — {job.title}
                </option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            disabled={askMutation.isPending || questionInput.trim().length === 0}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            Get answer
          </button>
        </div>
      </form>

      {activeResult && (
        <section className="space-y-3 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            {categoryLabel(activeResult.category)}
          </div>
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{activeResult.question}</p>
          {needsInput ? (
            <div
              role="status"
              className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
            >
              {NEEDS_INPUT}
            </div>
          ) : (
            <p className="whitespace-pre-wrap text-sm text-gray-700 dark:text-gray-300">{activeResult.answer}</p>
          )}
          {needsInput && activeResult.answer_bank_id != null && (
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                aria-label="Your answer"
                value={suppliedAnswerInput}
                onChange={(e) => setSuppliedAnswerInput(e.target.value)}
                placeholder="Type the answer you want to reuse"
                className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800"
              />
              <button
                type="button"
                disabled={suppliedAnswerInput.trim().length === 0 || setAnswerMutation.isPending}
                onClick={() =>
                  setAnswerMutation.mutate({
                    answerId: activeResult.answer_bank_id as number,
                    answerText: suppliedAnswerInput.trim(),
                  })
                }
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                Save answer
              </button>
            </div>
          )}
          {activeResult.is_draft && activeResult.answer_bank_id != null && !needsInput && (
            <button
              type="button"
              onClick={() => approveMutation.mutate(activeResult.answer_bank_id as number)}
              className="rounded-lg border border-indigo-300 px-3 py-1.5 text-sm font-semibold text-indigo-700 dark:text-indigo-300"
            >
              Approve answer
            </button>
          )}
        </section>
      )}

      <section className="min-h-0 flex-1">
        <h2 className="mb-3 text-sm font-bold text-gray-900 dark:text-gray-100">Saved answers</h2>
        {bankItems.length === 0 ? (
          <EmptyState
            title="No saved answers yet"
            message="Ask a personal question above. Answers you save are reused the next time the same question comes up."
          />
        ) : (
          <ul className="space-y-3">
            {bankItems.map((item) => {
              const isEditing = editingItem?.id === item.id;
              return (
                <li
                  key={item.id}
                  className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                        {item.question_norm}
                      </div>
                      <div className="mt-1 text-xs text-gray-500">{categoryLabel(item.category)}</div>
                    </div>
                    <span className="text-[11px] font-semibold uppercase text-gray-500">
                      {item.approved ? 'Approved' : 'Draft'}
                    </span>
                  </div>
                  {isEditing ? (
                    <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                      <input
                        aria-label={`Edit answer for ${item.question_norm}`}
                        value={editingAnswerText}
                        onChange={(e) => setEditingAnswerText(e.target.value)}
                        className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800"
                      />
                      <button
                        type="button"
                        onClick={() =>
                          setAnswerMutation.mutate({
                            answerId: item.id,
                            answerText: editingAnswerText.trim(),
                          })
                        }
                        className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white"
                      >
                        Save
                      </button>
                    </div>
                  ) : (
                    <p className="mt-2 whitespace-pre-wrap text-sm text-gray-700 dark:text-gray-300">
                      {item.answer || '—'}
                    </p>
                  )}
                  {!isEditing && (
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        aria-label={`Edit ${item.question_norm}`}
                        onClick={() => {
                          setEditingItem(item);
                          setEditingAnswerText(item.answer || '');
                        }}
                        className="rounded-md px-2 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 dark:text-indigo-300"
                      >
                        Edit
                      </button>
                      {!item.approved && item.answer && item.answer !== NEEDS_INPUT && (
                        <button
                          type="button"
                          aria-label={`Approve ${item.question_norm}`}
                          onClick={() => approveMutation.mutate(item.id)}
                          className="rounded-md px-2 py-1 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 dark:text-indigo-300"
                        >
                          Approve
                        </button>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
