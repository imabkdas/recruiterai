import { useState } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts';
import { api } from '../api/client';
import { ErrorState } from '../components/common/ErrorState';
import { EmptyState } from '../components/common/EmptyState';
import { Skeleton } from '../components/common/Skeleton';
import type { FullStatsReport } from '../api/types';

const WEEK_OPTIONS = [4, 8, 12] as const;

export function StatsPage() {
  const [selectedWeeks, setSelectedWeeks] = useState<number>(4);

  const {
    data: stats,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<FullStatsReport>({
    queryKey: ['stats', selectedWeeks],
    queryFn: () => api.getStats(selectedWeeks),
    placeholderData: keepPreviousData,
  });

  if (isLoading) {
    return (
      <div className="p-8 max-w-6xl mx-auto space-y-6">
        <Skeleton className="h-8 w-48 mb-2" />
        <Skeleton className="h-4 w-72 mb-8" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Skeleton className="h-24 w-full rounded-xl" />
          <Skeleton className="h-24 w-full rounded-xl" />
          <Skeleton className="h-24 w-full rounded-xl" />
          <Skeleton className="h-24 w-full rounded-xl" />
        </div>
        <Skeleton className="h-72 w-full rounded-xl" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-8 max-w-6xl mx-auto">
        <ErrorState
          title="Failed to load statistics report"
          message={error instanceof Error ? error.message : 'Unknown error'}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="p-8 max-w-6xl mx-auto">
        <EmptyState
          title="No statistics available"
          message="No data has been recorded for the selected time window."
        />
      </div>
    );
  }

  const { funnel, applications, sources = [], tiers = [] } = stats;

  const chartData = (applications.weeks || []).map((w) => ({
    weekLabel: w.iso_week || w.week_start,
    weekStart: w.week_start,
    weekEnd: w.week_end,
    applications: w.count,
    target: w.target,
    pct: w.pct_of_target,
  }));

  const funnelItems = [
    { label: 'Total Discovered', value: funnel.total_discovered, color: 'text-gray-900 dark:text-gray-100' },
    { label: 'Pre-filtered', value: funnel.filtered, color: 'text-gray-600 dark:text-gray-400' },
    { label: 'Needs JD', value: funnel.needs_jd, color: 'text-amber-600 dark:text-amber-400' },
    { label: 'Analyzed', value: funnel.analyzed, color: 'text-blue-600 dark:text-blue-400' },
    { label: 'Scored', value: funnel.scored, color: 'text-indigo-600 dark:text-indigo-400' },
    { label: 'Queued', value: funnel.queued, color: 'text-purple-600 dark:text-purple-400' },
    { label: 'Prepared', value: funnel.prepared, color: 'text-cyan-600 dark:text-cyan-400' },
    { label: 'Applied', value: funnel.applied, color: 'text-emerald-600 dark:text-emerald-400' },
    { label: 'Replied', value: funnel.replied, color: 'text-teal-600 dark:text-teal-400' },
    { label: 'Interview', value: funnel.interview, color: 'text-green-600 dark:text-green-400' },
    { label: 'Offer', value: funnel.offer, color: 'text-emerald-700 dark:text-emerald-300 font-black' },
    { label: 'Rejected', value: funnel.rejected, color: 'text-rose-600 dark:text-rose-400' },
    { label: 'Skipped', value: funnel.skipped, color: 'text-gray-500 dark:text-gray-500' },
    { label: 'Expired', value: funnel.expired, color: 'text-gray-400 dark:text-gray-600' },
  ];

  return (
    <div className="p-8 max-w-6xl mx-auto space-y-8 overflow-y-auto h-full">
      {/* Header & Weeks Selector */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-gray-200 dark:border-gray-800">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            Pipeline & Performance Stats
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Application volume against targets, pipeline funnel conversions, and response breakdown.
          </p>
        </div>

        {/* Weeks Selector (4, 8, 12) */}
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-gray-600 dark:text-gray-400">
            Window:
          </span>
          <div className="inline-flex rounded-lg shadow-2xs border border-gray-200 dark:border-gray-700 p-0.5 bg-gray-100 dark:bg-gray-800">
            {WEEK_OPTIONS.map((weeks) => (
              <button
                key={weeks}
                type="button"
                onClick={() => setSelectedWeeks(weeks)}
                aria-pressed={selectedWeeks === weeks}
                className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors ${
                  selectedWeeks === weeks
                    ? 'bg-white dark:bg-gray-900 text-indigo-600 dark:text-indigo-400 shadow-2xs'
                    : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                }`}
              >
                {weeks} Weeks
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 1. Funnel Counts Grid */}
      <div className="space-y-3">
        <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">
          Pipeline Funnel
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-3">
          {funnelItems.map((item) => (
            <div
              key={item.label}
              data-testid={`funnel-metric-${item.label.toLowerCase().replace(/[\s-]+/g, '-')}`}
              className="p-3 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-2xs text-center space-y-0.5"
            >
              <span className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 truncate">
                {item.label}
              </span>
              <span className={`text-xl font-bold ${item.color}`}>
                {item.value}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* 2. Weekly Applications vs Target Chart & Text Alternative */}
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-6 shadow-xs space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">
              Weekly Applications vs Target ({selectedWeeks} Weeks)
            </h2>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Total applied: <strong className="text-gray-900 dark:text-gray-100">{applications.total_applied}</strong> • Average: <strong className="text-gray-900 dark:text-gray-100">{applications.avg_per_week.toFixed(1)}/wk</strong> • Target: <strong className="text-gray-900 dark:text-gray-100">{applications.weekly_target}/wk</strong>
            </p>
          </div>
        </div>

        {/* Visual Bar Chart */}
        <div className="h-72 w-full pt-2" data-testid="weekly-bar-chart">
          <ResponsiveContainer width="100%" height="100%" minWidth={300} minHeight={200}>
            <BarChart data={chartData} margin={{ top: 10, right: 20, left: -10, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
              <XAxis
                dataKey="weekLabel"
                tick={{ fontSize: 11 }}
                stroke="#888888"
              />
              <YAxis
                allowDecimals={false}
                tick={{ fontSize: 11 }}
                stroke="#888888"
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#1f2937',
                  borderColor: '#374151',
                  borderRadius: '0.5rem',
                  fontSize: '12px',
                  color: '#f9fafb',
                }}
              />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }} />
              <Bar dataKey="applications" name="Applications" fill="#6366f1" radius={[4, 4, 0, 0]} />
              <Bar dataKey="target" name="Target" fill="#cbd5e1" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Text Table Alternative */}
        <div className="space-y-2 pt-4 border-t border-gray-100 dark:border-gray-800">
          <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            Chart Data Table (Accessible Alternative)
          </h3>
          <div className="overflow-x-auto">
            <table
              data-testid="weekly-stats-table"
              className="w-full text-left text-xs text-gray-700 dark:text-gray-300"
            >
              <thead className="bg-gray-50 dark:bg-gray-800/80 text-[11px] font-bold uppercase text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-800">
                <tr>
                  <th className="py-2.5 px-3">Week</th>
                  <th className="py-2.5 px-3">Start Date</th>
                  <th className="py-2.5 px-3">End Date</th>
                  <th className="py-2.5 px-3 text-right">Applications</th>
                  <th className="py-2.5 px-3 text-right">Target</th>
                  <th className="py-2.5 px-3 text-right">% of Target</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800 font-mono text-[11px]">
                {chartData.map((row) => (
                  <tr key={row.weekLabel} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/40">
                    <td className="py-2 px-3 font-semibold text-gray-900 dark:text-gray-100">
                      {row.weekLabel}
                    </td>
                    <td className="py-2 px-3 text-gray-500">{row.weekStart}</td>
                    <td className="py-2 px-3 text-gray-500">{row.weekEnd}</td>
                    <td className="py-2 px-3 text-right font-bold text-indigo-600 dark:text-indigo-400">
                      {row.applications}
                    </td>
                    <td className="py-2 px-3 text-right text-gray-600 dark:text-gray-400">
                      {row.target}
                    </td>
                    <td className="py-2 px-3 text-right font-semibold">
                      {(row.pct * 100).toFixed(0)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* 3. Conversion Rates by Source & Tier Tables */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Sources Table */}
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-xs space-y-3">
          <h2 className="text-sm font-bold text-gray-900 dark:text-gray-100">
            Conversion by Source
          </h2>
          {sources.length === 0 ? (
            <p className="text-xs text-gray-400">No source conversion data recorded yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table
                data-testid="sources-conversion-table"
                className="w-full text-left text-xs text-gray-700 dark:text-gray-300"
              >
                <thead className="bg-gray-50 dark:bg-gray-800/80 text-[10px] font-bold uppercase text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-800">
                  <tr>
                    <th className="py-2 px-3">Source</th>
                    <th className="py-2 px-3 text-right">Applied</th>
                    <th className="py-2 px-3 text-right">Replies</th>
                    <th className="py-2 px-3 text-right">Interviews</th>
                    <th className="py-2 px-3 text-right">Rate</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800 font-mono text-[11px]">
                  {sources.map((s) => (
                    <tr key={s.name}>
                      <td className="py-2 px-3 font-semibold text-gray-900 dark:text-gray-100 capitalize">
                        {s.name}
                      </td>
                      <td className="py-2 px-3 text-right">{s.applied}</td>
                      <td className="py-2 px-3 text-right">{s.responses}</td>
                      <td className="py-2 px-3 text-right">{s.interviews}</td>
                      <td className="py-2 px-3 text-right font-bold text-indigo-600 dark:text-indigo-400">
                        {(s.response_rate * 100).toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Tiers Table */}
        <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-xs space-y-3">
          <h2 className="text-sm font-bold text-gray-900 dark:text-gray-100">
            Conversion by Tier
          </h2>
          {tiers.length === 0 ? (
            <p className="text-xs text-gray-400">No tier conversion data recorded yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table
                data-testid="tiers-conversion-table"
                className="w-full text-left text-xs text-gray-700 dark:text-gray-300"
              >
                <thead className="bg-gray-50 dark:bg-gray-800/80 text-[10px] font-bold uppercase text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-800">
                  <tr>
                    <th className="py-2 px-3">Tier</th>
                    <th className="py-2 px-3 text-right">Applied</th>
                    <th className="py-2 px-3 text-right">Replies</th>
                    <th className="py-2 px-3 text-right">Interviews</th>
                    <th className="py-2 px-3 text-right">Rate</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800 font-mono text-[11px]">
                  {tiers.map((t) => (
                    <tr key={t.name}>
                      <td className="py-2 px-3 font-bold text-gray-900 dark:text-gray-100">
                        Tier {t.name}
                      </td>
                      <td className="py-2 px-3 text-right">{t.applied}</td>
                      <td className="py-2 px-3 text-right">{t.responses}</td>
                      <td className="py-2 px-3 text-right">{t.interviews}</td>
                      <td className="py-2 px-3 text-right font-bold text-indigo-600 dark:text-indigo-400">
                        {(t.response_rate * 100).toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
