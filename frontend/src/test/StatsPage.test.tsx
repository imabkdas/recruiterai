import { screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { StatsPage } from '../pages/StatsPage';
import { renderWithProviders } from './test-utils';
import { api } from '../api/client';
import type { FullStatsReport } from '../api/types';

vi.mock('recharts', async () => {
  const original = await vi.importActual<typeof import('recharts')>('recharts');
  return {
    ...original,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div className="recharts-responsive-container" style={{ width: 500, height: 300 }}>
        {children}
      </div>
    ),
  };
});

const mockStats4Weeks: FullStatsReport = {
  funnel: {
    total_discovered: 120,
    filtered: 40,
    needs_jd: 5,
    analyzed: 75,
    scored: 75,
    queued: 60,
    prepared: 25,
    applied: 20,
    replied: 8,
    interview: 4,
    offer: 1,
    rejected: 5,
    skipped: 15,
    expired: 10,
    status_counts: {},
    total_applied_all_time: 20,
  },
  applications: {
    weeks: [
      {
        iso_week: '2026-W37',
        week_start: '2026-09-08',
        week_end: '2026-09-14',
        count: 5,
        target: 5,
        pct_of_target: 1.0,
      },
      {
        iso_week: '2026-W38',
        week_start: '2026-09-15',
        week_end: '2026-09-21',
        count: 7,
        target: 5,
        pct_of_target: 1.4,
      },
      {
        iso_week: '2026-W39',
        week_start: '2026-09-22',
        week_end: '2026-09-28',
        count: 4,
        target: 5,
        pct_of_target: 0.8,
      },
      {
        iso_week: '2026-W40',
        week_start: '2026-09-29',
        week_end: '2026-10-05',
        count: 4,
        target: 5,
        pct_of_target: 0.8,
      },
    ],
    total_applied: 20,
    weekly_target: 5,
    avg_per_week: 5.0,
  },
  sources: [
    {
      name: 'linkedin_alert',
      applied: 12,
      responses: 5,
      interviews: 3,
      offers: 1,
      response_rate: 0.417,
      rejected: 2,
    },
    {
      name: 'adzuna',
      applied: 8,
      responses: 3,
      interviews: 1,
      offers: 0,
      response_rate: 0.375,
      rejected: 3,
    },
  ],
  tiers: [
    {
      name: 'A',
      applied: 14,
      responses: 6,
      interviews: 3,
      offers: 1,
      response_rate: 0.429,
      rejected: 2,
    },
    {
      name: 'B',
      applied: 6,
      responses: 2,
      interviews: 1,
      offers: 0,
      response_rate: 0.333,
      rejected: 3,
    },
  ],
};

describe('StatsPage (Phase 5c-2b)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders weeks selector, bar chart container, text table alternative, and funnel counts', async () => {
    vi.spyOn(api, 'getStats').mockResolvedValue(mockStats4Weeks);

    renderWithProviders(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText('Pipeline & Performance Stats')).toBeInTheDocument();
    });

    // Funnel count metrics
    expect(screen.getByTestId('funnel-metric-total-discovered')).toHaveTextContent('120');
    expect(screen.getByTestId('funnel-metric-applied')).toHaveTextContent('20');
    expect(screen.getByTestId('funnel-metric-interview')).toHaveTextContent('4');
    expect(screen.getByTestId('funnel-metric-offer')).toHaveTextContent('1');

    // Chart container is rendered
    expect(screen.getByTestId('weekly-bar-chart')).toBeInTheDocument();

    // Accessible text table alternative is rendered with week rows
    const table = screen.getByTestId('weekly-stats-table');
    expect(table).toBeInTheDocument();
    expect(screen.getByText('2026-W37')).toBeInTheDocument();
    expect(screen.getByText('2026-W38')).toBeInTheDocument();
    expect(screen.getByText('140%')).toBeInTheDocument(); // 1.4 * 100

    // Source and Tier conversion tables
    expect(screen.getByTestId('sources-conversion-table')).toBeInTheDocument();
    expect(screen.getByText('linkedin_alert')).toBeInTheDocument();
    expect(screen.getByText('41.7%')).toBeInTheDocument();

    expect(screen.getByTestId('tiers-conversion-table')).toBeInTheDocument();
    expect(screen.getByText('Tier A')).toBeInTheDocument();
    expect(screen.getByText('42.9%')).toBeInTheDocument();
  });

  it('changes time window when selecting 8 or 12 weeks', async () => {
    const statsSpy = vi.spyOn(api, 'getStats').mockResolvedValue(mockStats4Weeks);

    renderWithProviders(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText('Pipeline & Performance Stats')).toBeInTheDocument();
    });

    // Initial fetch was for 4 weeks
    expect(statsSpy).toHaveBeenCalledWith(4);

    // Click 8 Weeks selector
    const eightWeeksBtn = screen.getByRole('button', { name: '8 Weeks' });
    fireEvent.click(eightWeeksBtn);

    await waitFor(() => {
      expect(statsSpy).toHaveBeenCalledWith(8);
    });

    // Click 12 Weeks selector
    const twelveWeeksBtn = screen.getByRole('button', { name: '12 Weeks' });
    fireEvent.click(twelveWeeksBtn);

    await waitFor(() => {
      expect(statsSpy).toHaveBeenCalledWith(12);
    });
  });

  it('renders error state and retries on failure', async () => {
    const statsSpy = vi
      .spyOn(api, 'getStats')
      .mockRejectedValueOnce(new Error('Network timeout'))
      .mockResolvedValueOnce(mockStats4Weeks);

    renderWithProviders(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load statistics report')).toBeInTheDocument();
      expect(screen.getByText('Network timeout')).toBeInTheDocument();
    });

    const retryBtn = screen.getByRole('button', { name: /Retry/i });
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(statsSpy).toHaveBeenCalledTimes(2);
      expect(screen.getByText('Pipeline & Performance Stats')).toBeInTheDocument();
    });
  });
});
