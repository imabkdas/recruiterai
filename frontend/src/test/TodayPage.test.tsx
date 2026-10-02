import { screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TodayPage } from '../pages/TodayPage';
import { renderWithProviders } from './test-utils';
import { api } from '../api/client';
import type { FollowUpItem, JobDetail, ProgressSummary, QueueItem } from '../api/types';

const mockProgress: ProgressSummary = {
  applied_today: 3,
  daily_size: 10,
  applied_this_week: 14,
  weekly_target: 30,
  follow_ups_due_count: 2,
};

const mockFollowUps: FollowUpItem[] = [
  {
    job_id: 301,
    company: 'Stripe',
    title: 'Software Engineer',
    status: 'applied',
    applied_at: '2026-09-20T00:00:00Z',
    days_since_applied: 12,
  },
  {
    job_id: 302,
    company: 'Datadog',
    title: 'Backend Engineer',
    status: 'applied',
    applied_at: '2026-09-22T00:00:00Z',
    days_since_applied: 10,
  },
];

const mockQueue: QueueItem[] = [
  {
    rank: 1,
    job_id: 1,
    company: 'Acme Corp',
    title: 'Staff Python Engineer',
    location: 'Bangalore, India',
    tier: 'A',
    score: 9.5,
    matched_skills: 'Python, FastAPI',
    gaps: '',
    flags: 'high_priority',
    url: 'https://example.com/jobs/1',
    prepared: 'y',
  },
  {
    rank: 2,
    job_id: 2,
    company: 'Beta Labs',
    title: 'Senior Distributed Systems Engineer',
    location: 'Remote',
    tier: 'B',
    score: 8.2,
    matched_skills: 'Python, Docker',
    gaps: 'Kubernetes (pref)',
    flags: '',
    url: 'https://example.com/jobs/2',
    prepared: 'n',
  },
];

const mockJob1: JobDetail = {
  id: 1,
  fingerprint: 'fp1',
  source: 'HackerNews',
  company: 'Acme Corp',
  title: 'Staff Python Engineer',
  location: 'Bangalore, India',
  url: 'https://example.com/jobs/1',
  description: 'Detailed description of Acme job...',
  discovered_at: '2026-10-01T00:00:00Z',
  last_seen_at: '2026-10-01T00:00:00Z',
  status: 'queued',
  score: {
    total: 9.5,
    tier: 'A',
    scored_at: '2026-10-01T00:00:00Z',
    covered_skills: [{ skill: 'Python', level: 'VERIFIED_PROFESSIONAL', evidence_ids: ['b1'] }],
    partial_skills: [],
    learning_only_skills: [],
    missing_skills: [],
    flags: ['high_priority'],
  },
  application: {
    job_id: 1,
    tailored_summary: 'Staff engineer tailoring for Acme Corp',
    short_note: 'Note for Acme',
  },
};

const mockJob2: JobDetail = {
  id: 2,
  fingerprint: 'fp2',
  source: 'Remotive',
  company: 'Beta Labs',
  title: 'Senior Distributed Systems Engineer',
  location: 'Remote',
  url: 'https://example.com/jobs/2',
  description: 'Detailed description of Beta Labs job...',
  discovered_at: '2026-10-01T00:00:00Z',
  last_seen_at: '2026-10-01T00:00:00Z',
  status: 'queued',
  score: {
    total: 8.2,
    tier: 'B',
    scored_at: '2026-10-01T00:00:00Z',
    covered_skills: [{ skill: 'Python', level: 'VERIFIED_PROFESSIONAL', evidence_ids: ['b1'] }],
    partial_skills: [{ skill: 'Docker', level: 'VERIFIED_PROJECT', evidence_ids: ['p1'] }],
    learning_only_skills: [],
    missing_skills: ['Kubernetes'],
    flags: [],
  },
  application: null,
};

describe('TodayPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'getProgress').mockResolvedValue(mockProgress);
    vi.spyOn(api, 'getFollowUps').mockResolvedValue(mockFollowUps);
    vi.spyOn(api, 'getQueue').mockResolvedValue(mockQueue);
    vi.spyOn(api, 'getJobDetail').mockImplementation(async (id: number) => {
      if (id === 1) return mockJob1;
      if (id === 2) return mockJob2;
      throw new Error(`Job ${id} not found`);
    });
  });

  it('renders progress strip and follow-ups alert banner', async () => {
    renderWithProviders(<TodayPage />);

    // Progress strip values
    await waitFor(() => {
      expect(screen.getByText('3 / 10')).toBeInTheDocument();
      expect(screen.getByText('14 / 30')).toBeInTheDocument();
    });

    // Follow-ups banner
    expect(screen.getByTestId('follow-ups-banner')).toHaveTextContent(
      'Follow-ups due: 2 application(s) awaiting your follow-up'
    );
  });

  it('renders ranked queue list with tier, score, title, and company', async () => {
    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText('Staff Python Engineer')).toBeInTheDocument();
      expect(screen.getByText('Senior Distributed Systems Engineer')).toBeInTheDocument();
    });

    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText('#2')).toBeInTheDocument();
    expect(screen.getAllByText('Tier A')[0]).toBeInTheDocument();
    expect(screen.getByText('Tier B')).toBeInTheDocument();
    expect(screen.getByText('★ 9.5')).toBeInTheDocument();
    expect(screen.getByText('★ 8.2')).toBeInTheDocument();
  });

  it('selects the first job by default and displays its details', async () => {
    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText('Staff engineer tailoring for Acme Corp')).toBeInTheDocument();
    });
  });

  it('selecting a job shows its detail and keeps selection in the URL', async () => {
    renderWithProviders(<TodayPage />, {
      routerProps: { initialEntries: ['/?job=1'] },
    });

    await waitFor(() => {
      expect(screen.getByText('Staff engineer tailoring for Acme Corp')).toBeInTheDocument();
    });

    // Click Job 2 in the queue
    const job2Item = screen.getByTestId('queue-item-2');
    fireEvent.click(job2Item);

    // Detail should update to Beta Labs
    await waitFor(() => {
      expect(
        screen.getByText('Detailed description of Beta Labs job...')
      ).toBeInTheDocument();
      expect(screen.getAllByText('Beta Labs').length).toBe(2);
    });
  });

  it('renders empty queue state when queue has no items', async () => {
    vi.spyOn(api, 'getQueue').mockResolvedValue([]);

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText("Today's queue is empty")).toBeInTheDocument();
      expect(screen.getAllByTestId('empty-state').length).toBe(2);
    });
  });

  it('renders error state with a working Retry button when API fails', async () => {
    const queueSpy = vi
      .spyOn(api, 'getQueue')
      .mockRejectedValueOnce(new Error('Network connection timeout'))
      .mockResolvedValueOnce(mockQueue);

    renderWithProviders(<TodayPage />);

    // Error state should display
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText('Failed to load queue')).toBeInTheDocument();
      expect(screen.getByText('Network connection timeout')).toBeInTheDocument();
    });

    // Click Retry
    const retryBtn = screen.getByRole('button', { name: /retry/i });
    fireEvent.click(retryBtn);

    // After retry, queue should render
    await waitFor(() => {
      expect(screen.getByText('Staff Python Engineer')).toBeInTheDocument();
    });

    expect(queueSpy).toHaveBeenCalledTimes(2);
  });
});
