import type { QueueItem } from '../../api/types';
import { EmptyState } from '../common/EmptyState';
import { Skeleton } from '../common/Skeleton';

interface QueueListProps {
  items: QueueItem[];
  selectedJobId: number | null;
  onSelectJob: (jobId: number) => void;
  isLoading?: boolean;
}

export function QueueList({
  items,
  selectedJobId,
  onSelectJob,
  isLoading,
}: QueueListProps) {
  if (isLoading) {
    return (
      <div className="p-4 space-y-3" data-testid="queue-skeleton">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="p-4 border border-gray-100 dark:border-gray-800/80 rounded-xl space-y-2">
            <div className="flex justify-between items-center">
              <Skeleton className="h-5 w-16" />
              <Skeleton className="h-4 w-12" />
            </div>
            <Skeleton className="h-5 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <EmptyState
        title="Today's queue is empty"
        message="No jobs are currently queued for today. Run the daily pipeline or add full JDs to populate the queue."
      />
    );
  }

  return (
    <div
      role="listbox"
      aria-label="Ranked jobs"
      className="divide-y divide-gray-100 dark:divide-gray-850 overflow-y-auto h-full"
    >
      {items.map((item) => {
        const isSelected = item.job_id === selectedJobId;
        const flagsList = item.flags
          ? item.flags.split(',').map((f) => f.trim()).filter(Boolean)
          : [];

        const isTierA = item.tier.toUpperCase() === 'A';
        const isTierB = item.tier.toUpperCase() === 'B';
        const sourceLabel = (item as { source?: string }).source || 'job';

        return (
          <div
            key={item.job_id}
            role="option"
            aria-selected={isSelected}
            tabIndex={0}
            data-testid={`queue-item-${item.job_id}`}
            onClick={() => onSelectJob(item.job_id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onSelectJob(item.job_id);
              }
            }}
            className={`p-4 cursor-pointer transition-all border-l-4 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-indigo-500 ${
              isSelected
                ? 'bg-indigo-50/70 dark:bg-indigo-950/30 border-indigo-600 dark:border-indigo-500 shadow-xs'
                : 'border-transparent hover:bg-gray-50/80 dark:hover:bg-gray-800/40'
            }`}
          >
            {/* Header: Rank, Tier Badge & Score */}
            <div className="flex items-center justify-between gap-2 mb-1.5">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono font-bold text-gray-400 dark:text-gray-500">
                  #{item.rank}
                </span>
                <span
                  className={`px-2 py-0.5 rounded text-[11px] font-bold tracking-wide uppercase ${
                    isTierA
                      ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300'
                      : isTierB
                      ? 'bg-cyan-100 text-cyan-800 dark:bg-cyan-950/80 dark:text-cyan-300'
                      : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'
                  }`}
                >
                  Tier {item.tier}
                </span>
              </div>
              <span className="text-xs font-mono font-semibold px-2 py-0.5 bg-gray-100 dark:bg-gray-800 rounded text-gray-700 dark:text-gray-300">
                ★ {item.score.toFixed(1)}
              </span>
            </div>

            {/* Title & Company */}
            <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 leading-snug line-clamp-1 mb-1">
              {item.title}
            </h4>
            <div className="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400 mb-2">
              <span className="font-medium text-gray-800 dark:text-gray-200">{item.company}</span>
              {item.location && (
                <>
                  <span>•</span>
                  <span className="truncate">{item.location}</span>
                </>
              )}
            </div>

            {/* Metadata & Flags */}
            <div className="flex flex-wrap items-center gap-1.5 mt-2">
              <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
                {sourceLabel}
              </span>
              {flagsList.map((flag, idx) => (
                <span
                  key={idx}
                  className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300 border border-amber-200 dark:border-amber-900/50"
                >
                  {flag}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
