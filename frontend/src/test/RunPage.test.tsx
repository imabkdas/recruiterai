import { screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { RunPage } from '../pages/RunPage';
import { renderWithProviders, createTestQueryClient } from './test-utils';
import { api, ApiError } from '../api/client';
import type { RunStatusResponse } from '../api/types';

const mockIdleStatus: RunStatusResponse = {
  state: 'idle',
  stages: [],
  started_at: null,
  finished_at: null,
  summary: null,
  error: null,
};

const mockRunningStatus: RunStatusResponse = {
  state: 'running',
  started_at: '2026-10-02T10:00:00Z',
  finished_at: null,
  error: null,
  stages: [
    { name: 'fetch', status: 'done', counts: 'Fetched 20' },
    { name: 'ingest-alerts', status: 'running', counts: 'Ingested 14, parsed 12' },
    { name: 'analyze', status: 'pending', counts: null },
    { name: 'score', status: 'pending', counts: null },
    { name: 'queue', status: 'pending', counts: null },
    { name: 'prepare', status: 'pending', counts: null },
  ],
};

const mockFinishedStatus: RunStatusResponse = {
  state: 'finished',
  started_at: '2026-10-02T10:00:00Z',
  finished_at: '2026-10-02T10:02:30Z',
  error: null,
  stages: [
    { name: 'fetch', status: 'done', counts: 'Fetched 20' },
    { name: 'ingest-alerts', status: 'done', counts: 'Ingested 14, parsed 12' },
    { name: 'analyze', status: 'done', counts: 'Analyzed 12' },
    { name: 'score', status: 'done', counts: 'Scored 12' },
    { name: 'queue', status: 'done', counts: 'Queued 10' },
    { name: 'prepare', status: 'done', counts: 'Prepared 5' },
  ],
  summary: {
    stages: [],
    new_jobs: 14,
    queued_count: 10,
    tier_a_count: 4,
    needs_jd_count: 2,
    prepared_count: 5,
    follow_ups_due_count: 1,
    has_failures: false,
  },
};

const mockGmailAuthFailedStatus: RunStatusResponse = {
  state: 'finished',
  started_at: '2026-10-02T10:00:00Z',
  finished_at: '2026-10-02T10:01:10Z',
  stages: [
    {
      name: 'ingest-alerts',
      status: 'error',
      counts: null,
      error: 'Gmail token expired or credentials not found',
    },
    { name: 'queue', status: 'done', counts: 'Queued 8' },
  ],
  summary: {
    stages: [],
    new_jobs: 0,
    queued_count: 8,
    tier_a_count: 2,
    needs_jd_count: 0,
    prepared_count: 0,
    follow_ups_due_count: 0,
    has_failures: true,
  },
};

describe('RunPage (Phase 5c-2a)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders initial idle state with "Run daily pipeline" button enabled', async () => {
    vi.spyOn(api, 'getRunStatus').mockResolvedValue(mockIdleStatus);

    renderWithProviders(<RunPage />);

    await waitFor(() => {
      expect(screen.getByTestId('run-state-badge')).toHaveTextContent('State: idle');
      expect(screen.getByRole('button', { name: /Run daily pipeline/i })).toBeEnabled();
    });
    expect(
      screen.getByText(/No active or recent pipeline stages recorded/i)
    ).toBeInTheDocument();
  });

  it('triggers run on button click and displays running stages and counts', async () => {
    vi.spyOn(api, 'getRunStatus').mockResolvedValue(mockIdleStatus);
    const triggerSpy = vi.spyOn(api, 'triggerRun').mockResolvedValue(mockRunningStatus);

    renderWithProviders(<RunPage />);

    const runBtn = await screen.findByRole('button', { name: /Run daily pipeline/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(triggerSpy).toHaveBeenCalledTimes(1);
    });

    await waitFor(() => {
      expect(screen.getByText('Daily pipeline started')).toBeInTheDocument();
    });
  });

  it('attaches to live status on 409 conflict when clicking Run', async () => {
    vi.spyOn(api, 'getRunStatus').mockResolvedValue(mockIdleStatus);
    vi.spyOn(api, 'triggerRun').mockRejectedValue(
      new ApiError(409, 'A pipeline run is already in progress.', 'conflict')
    );

    renderWithProviders(<RunPage />);

    const runBtn = await screen.findByRole('button', { name: /Run daily pipeline/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(
        screen.getByText('A pipeline run is already active. Attaching to live status.')
      ).toBeInTheDocument();
    });

    // Does NOT show an error toast
    expect(screen.queryByText('A pipeline run is already in progress.')).toBeNull();
  });

  it('polls status every 1s while running and renders live stage status and counts', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    let callCount = 0;
    const getStatusSpy = vi.spyOn(api, 'getRunStatus').mockImplementation(async () => {
      callCount++;
      if (callCount === 1) return mockRunningStatus;
      if (callCount === 2) return mockRunningStatus;
      return mockFinishedStatus;
    });

    renderWithProviders(<RunPage />);

    // First call happens immediately
    await waitFor(() => {
      expect(getStatusSpy).toHaveBeenCalledTimes(1);
    });

    expect(screen.getByText('Ingested 14, parsed 12')).toBeInTheDocument();
    expect(screen.getByTestId('stage-status-ingest-alerts')).toHaveTextContent('running');

    // Advance timer by 1s (1000ms) for second poll
    await vi.advanceTimersByTimeAsync(1000);
    expect(getStatusSpy).toHaveBeenCalledTimes(2);

    // Advance timer by another 1s for third poll -> finished
    await vi.advanceTimersByTimeAsync(1000);
    expect(getStatusSpy).toHaveBeenCalledTimes(3);

    // Finished summary renders
    await waitFor(() => {
      expect(screen.getByText('Pipeline Run Finished Successfully')).toBeInTheDocument();
    });

    // Advance timer again: polling has stopped because state is 'finished'
    await vi.advanceTimersByTimeAsync(2000);
    expect(getStatusSpy).toHaveBeenCalledTimes(3);
  });

  it('displays summary and prominently reminds user to re-authorize Gmail on auth failure', async () => {
    vi.spyOn(api, 'getRunStatus').mockResolvedValue(mockGmailAuthFailedStatus);

    renderWithProviders(<RunPage />);

    await waitFor(() => {
      expect(screen.getByTestId('failed-stages-alert')).toBeInTheDocument();
    });

    expect(screen.getByText('Failed Stage(s) Detected')).toBeInTheDocument();
    expect(screen.getByText('Stage: ingest-alerts')).toBeInTheDocument();
    expect(
      screen.getAllByText('Gmail token expired or credentials not found').length
    ).toBeGreaterThanOrEqual(1);

    // Gmail auth hint appears with exact required text
    const gmailHint = screen.getByTestId('gmail-auth-hint');
    expect(gmailHint).toHaveTextContent(
      'Re-authorize by running jobpilot ingest-alerts in a terminal'
    );
  });

  it('invalidates queries for queue, needs-jd, progress, applications, and follow-ups on finished', async () => {
    vi.spyOn(api, 'getRunStatus').mockResolvedValue(mockFinishedStatus);

    const testClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(testClient, 'invalidateQueries');
    renderWithProviders(<RunPage />, { queryClient: testClient });

    await waitFor(() => {
      expect(screen.getByText('Pipeline Run Finished Successfully')).toBeInTheDocument();
    });

    // Check invalidation of all 5 queries
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['queue'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['needs-jd'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['progress'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['applications'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['follow-ups'] });
  });

  it('displays error text when pipeline run has failed state', async () => {
    const mockFailedStatus: RunStatusResponse = {
      state: 'failed',
      started_at: '2026-10-02T10:00:00Z',
      finished_at: '2026-10-02T10:00:05Z',
      error: 'Database connection failed: disk I/O error',
      stages: [],
      summary: null,
    };
    vi.spyOn(api, 'getRunStatus').mockResolvedValue(mockFailedStatus);

    renderWithProviders(<RunPage />);

    await waitFor(() => {
      expect(screen.getByTestId('run-failed-alert')).toBeInTheDocument();
    });

    expect(screen.getByText('Pipeline Execution Failed')).toBeInTheDocument();
    expect(
      screen.getByText('Database connection failed: disk I/O error')
    ).toBeInTheDocument();
  });

  it('shows per-source fetch results and gmail sender counts', async () => {
    const status: RunStatusResponse = {
      state: 'finished',
      started_at: '2026-10-02T10:00:00Z',
      finished_at: '2026-10-02T10:02:30Z',
      error: null,
      summary: null,
      stages: [
        {
          name: 'fetch',
          status: 'ok',
          counts: {
            total_fetched: 42,
            new_jobs: 7,
            sources: [
              { name: 'adzuna', status: 'ok', found: 42, new: 7, updated: 0 },
              { name: 'lever:acme', status: 'failed', error: 'source unavailable' },
            ],
          },
        },
        {
          name: 'ingest-alerts',
          status: 'ok',
          counts: {
            total_listings: 4,
            sender_counts: { linkedin: 2, instahyre: 2 },
          },
        },
      ],
    };
    vi.spyOn(api, 'getRunStatus').mockResolvedValue(status);
    renderWithProviders(<RunPage />);
    await waitFor(() => {
      expect(screen.getByTestId('source-lines-fetch')).toBeInTheDocument();
    });
    expect(screen.getByText('42 found / 7 new')).toBeInTheDocument();
    expect(screen.getByText('source unavailable')).toBeInTheDocument();
    expect(screen.getAllByText('2 jobs')).toHaveLength(2);
    expect(screen.getByText('instahyre')).toBeInTheDocument();
  });

  it('ceases polling when component unmounts', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const getStatusSpy = vi.spyOn(api, 'getRunStatus').mockResolvedValue(mockRunningStatus);

    const { unmount } = renderWithProviders(<RunPage />);

    await waitFor(() => {
      expect(getStatusSpy).toHaveBeenCalledTimes(1);
    });

    // Unmount component
    unmount();

    // Advance timer: should not poll anymore
    await vi.advanceTimersByTimeAsync(3000);
    expect(getStatusSpy).toHaveBeenCalledTimes(1);
  });
});
