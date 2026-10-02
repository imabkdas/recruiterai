import type { FollowUpItem, ProgressSummary } from '../../api/types';
import { Skeleton } from '../common/Skeleton';

interface ProgressStripProps {
  progress?: ProgressSummary;
  isLoading?: boolean;
  followUps?: FollowUpItem[];
}

export function ProgressStrip({ progress, isLoading, followUps = [] }: ProgressStripProps) {
  const todayPct = progress
    ? Math.min(100, Math.round((progress.applied_today / (progress.daily_size || 1)) * 100))
    : 0;

  const weekPct = progress
    ? Math.min(100, Math.round((progress.applied_this_week / (progress.weekly_target || 1)) * 100))
    : 0;

  return (
    <div className="flex-shrink-0 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
      {/* Follow-ups Alert Banner */}
      {followUps.length > 0 && (
        <div
          data-testid="follow-ups-banner"
          className="bg-amber-50 dark:bg-amber-950/40 border-b border-amber-200 dark:border-amber-900/60 px-6 py-2.5 flex items-center justify-between text-xs text-amber-900 dark:text-amber-200"
        >
          <div className="flex items-center gap-2 font-medium">
            <span className="text-amber-600 dark:text-amber-400 font-bold">⚠️</span>
            <span>
              Follow-ups due: <strong>{followUps.length}</strong> application(s) awaiting your follow-up (
              {followUps.map((f) => f.company).slice(0, 3).join(', ')}
              {followUps.length > 3 ? ` +${followUps.length - 3} more` : ''})
            </span>
          </div>
          <span className="text-[11px] text-amber-700 dark:text-amber-400">Action needed</span>
        </div>
      )}

      {/* Progress Bars Strip */}
      <div className="px-6 py-3 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-8">
          {/* Today's Goal */}
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
              Today
            </span>
            {isLoading || !progress ? (
              <Skeleton className="h-4 w-32" />
            ) : (
              <div className="flex items-center gap-2.5">
                <div className="w-28 h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-indigo-600 dark:bg-indigo-500 rounded-full transition-all duration-300"
                    style={{ width: `${todayPct}%` }}
                  />
                </div>
                <span className="text-xs font-semibold text-gray-800 dark:text-gray-200">
                  {progress.applied_today} / {progress.daily_size}
                </span>
              </div>
            )}
          </div>

          {/* Weekly Goal */}
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
              This Week
            </span>
            {isLoading || !progress ? (
              <Skeleton className="h-4 w-32" />
            ) : (
              <div className="flex items-center gap-2.5">
                <div className="w-28 h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-violet-600 dark:bg-violet-500 rounded-full transition-all duration-300"
                    style={{ width: `${weekPct}%` }}
                  />
                </div>
                <span className="text-xs font-semibold text-gray-800 dark:text-gray-200">
                  {progress.applied_this_week} / {progress.weekly_target}
                </span>
              </div>
            )}
          </div>
        </div>

        <div className="text-xs text-gray-400 dark:text-gray-500">
          Ranked queue resets daily
        </div>
      </div>
    </div>
  );
}
