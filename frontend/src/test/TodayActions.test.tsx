import { screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TodayPage } from '../pages/TodayPage';
import { renderWithProviders } from './test-utils';
import { api, ApiError } from '../api/client';
import type { FollowUpItem, JobDetail, JobStatusUpdate, ProgressSummary, QueueItem } from '../api/types';

const mockProgress: ProgressSummary = {
  applied_today: 3,
  daily_size: 10,
  applied_this_week: 14,
  weekly_target: 30,
  follow_ups_due_count: 0,
};

const mockFollowUps: FollowUpItem[] = [];

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
    prepared: 'n',
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
    gaps: 'Kubernetes',
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
  application: null,
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
    partial_skills: [],
    learning_only_skills: [],
    missing_skills: [],
    flags: [],
  },
  application: null,
};

describe('TodayPage Actions & Keyboard Controls (Phase 5c-1b)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'getProgress').mockResolvedValue(mockProgress);
    vi.spyOn(api, 'getFollowUps').mockResolvedValue(mockFollowUps);
    vi.spyOn(api, 'getQueue').mockResolvedValue(mockQueue);
    vi.spyOn(api, 'getResumeSource').mockResolvedValue({
      source: '\\documentclass{article}\n',
      pdf_url: null,
    });
    vi.spyOn(api, 'getJobDetail').mockImplementation(async (id: number) => {
      if (id === 1) return mockJob1;
      if (id === 2) return mockJob2;
      throw new Error(`Job ${id} not found`);
    });
  });

  // 1. Prepare button success path
  it('prepares job on Prepare button click and shows toast', async () => {
    const prepareSpy = vi.spyOn(api, 'prepareJob').mockResolvedValue({
      job_id: 1,
      tier: 'A',
      summary: 'Tailored summary for Acme Corp',
      note: 'Short note for Acme',
      bullet_ids: ['b1'],
      used_fallback: false,
    });

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /prepare application drafts/i })).toBeInTheDocument();
    });

    const prepareBtn = screen.getByRole('button', { name: /prepare application drafts/i });
    fireEvent.click(prepareBtn);

    await waitFor(() => {
      expect(prepareSpy).toHaveBeenCalledWith(1);
    });

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Application drafts prepared!');
    });
  });

  // 1b. Prepare button error path
  it('shows error toast when prepareJob fails with backend message', async () => {
    vi.spyOn(api, 'prepareJob').mockRejectedValue(
      new ApiError(400, 'Profile validation failed: no matching bullets', 'validation_error')
    );

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /prepare application drafts/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /prepare application drafts/i }));

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Profile validation failed: no matching bullets');
    });
  });

  // 2. Mark applied success path with dialog inputs
  it('opens mark applied dialog, accepts channel and notes, and calls api.markJob', async () => {
    const user = userEvent.setup();
    const markSpy = vi.spyOn(api, 'markJob').mockResolvedValue({
      job_id: 1,
      previous_status: 'queued',
      new_status: 'applied',
      channel: 'referral',
      note: 'Referred by Alice',
      referral_contact: 'Alice Smith',
      timestamp: '2026-10-02T00:00:00Z',
    });

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /mark job as applied/i })).toBeInTheDocument();
    });

    // Open dialog
    await user.click(screen.getByRole('button', { name: /mark job as applied/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Mark as Applied')).toBeInTheDocument();

    // Select channel
    const channelSelect = screen.getByLabelText(/application channel/i);
    await user.selectOptions(channelSelect, 'referral');

    // Fill referral contact
    const contactInput = screen.getByLabelText(/referral contact/i);
    await user.type(contactInput, 'Alice Smith');

    // Fill note
    const noteInput = screen.getByLabelText(/application note/i);
    await user.type(noteInput, 'Referred by Alice');

    // Submit dialog
    await user.click(screen.getByRole('button', { name: /submit mark as applied/i }));

    expect(markSpy).toHaveBeenCalledWith(1, {
      status: 'applied',
      channel: 'referral',
      referral_contact: 'Alice Smith',
      note: 'Referred by Alice',
    });

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Marked as applied!');
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  // 3 & 4. Optimistic update for mark applied, advances selection, and updates progress strip
  it('optimistically removes marked job, advances selection to next job, updates progress strip', async () => {
    const user = userEvent.setup();
    let resolveMark: (value: JobStatusUpdate) => void;
    vi.spyOn(api, 'markJob').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveMark = resolve;
        })
    );

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText('3 / 10')).toBeInTheDocument();
    });

    // Open and submit mark dialog
    await user.click(screen.getByRole('button', { name: /mark job as applied/i }));
    await user.click(screen.getByRole('button', { name: /submit mark as applied/i }));

    // Job 1 should immediately disappear from queue list
    expect(screen.queryByTestId('queue-item-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('queue-item-2')).toBeInTheDocument();

    // Selection should advance to Job 2 (Beta Labs)
    await waitFor(() => {
      expect(screen.getByText('Detailed description of Beta Labs job...')).toBeInTheDocument();
    });

    // Progress strip should optimistically increment (3 -> 4, 14 -> 15)
    expect(screen.getByText('4 / 10')).toBeInTheDocument();
    expect(screen.getByText('15 / 30')).toBeInTheDocument();

    // Resolve API call
    resolveMark!({
      job_id: 1,
      previous_status: 'queued',
      new_status: 'applied',
      timestamp: '2026-10-02T00:00:00Z',
    });
  });

  // 4. Rollback on 409 error
  it('rolls back queue, progress, and selection when markJob fails with 409', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'markJob').mockRejectedValue(
      new ApiError(409, 'Cannot transition from queued to applied', 'invalid_transition')
    );

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText('3 / 10')).toBeInTheDocument();
    });

    // Open and submit mark dialog
    await user.click(screen.getByRole('button', { name: /mark job as applied/i }));
    await user.click(screen.getByRole('button', { name: /submit mark as applied/i }));

    // On error, toast shows the backend 409 error
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Cannot transition from queued to applied');
    });

    // Queue rolled back: Job 1 is back in the queue
    expect(screen.getByTestId('queue-item-1')).toBeInTheDocument();

    // Progress rolled back to 3 / 10
    expect(screen.getByText('3 / 10')).toBeInTheDocument();
  });

  // 3 & 4. Skip optimistic update & rollback
  it('optimistically removes job on skip and rolls back on failure', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'skipJob').mockRejectedValue(
      new ApiError(500, 'Database write timeout', 'db_error')
    );

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByTestId('queue-item-1')).toBeInTheDocument();
    });

    // Click Skip
    await user.click(screen.getByRole('button', { name: /skip job/i }));

    // Error toast appears
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('Database write timeout');
    });

    // Rolled back: Job 1 is still in queue
    expect(screen.getByTestId('queue-item-1')).toBeInTheDocument();
  });

  // 5. Keyboard shortcuts: j, k, o, p, a, s, ?, Esc
  it('handles keyboard shortcuts j (down) and k (up) for navigation', async () => {
    renderWithProviders(<TodayPage />, {
      routerProps: { initialEntries: ['/?job=1'] },
    });

    await waitFor(() => {
      expect(screen.getByText('Detailed description of Acme job...')).toBeInTheDocument();
    });

    // Press 'j' -> moves to job 2
    fireEvent.keyDown(window, { key: 'j' });

    await waitFor(() => {
      expect(screen.getByText('Detailed description of Beta Labs job...')).toBeInTheDocument();
    });

    // Press 'k' -> moves back to job 1
    fireEvent.keyDown(window, { key: 'k' });

    await waitFor(() => {
      expect(screen.getByText('Detailed description of Acme job...')).toBeInTheDocument();
    });
  });

  it('handles keyboard shortcut o to open job URL in new window', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText('Detailed description of Acme job...')).toBeInTheDocument();
    });

    fireEvent.keyDown(window, { key: 'o' });

    expect(openSpy).toHaveBeenCalledWith('https://example.com/jobs/1', '_blank', 'noopener,noreferrer');
  });

  it('does nothing on keyboard shortcut o when job URL is invalid or unsafe', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const unsafeJob: JobDetail = {
      ...mockJob1,
      id: 999,
      url: 'javascript:alert(1)',
      apply_url: null,
    };
    vi.spyOn(api, 'getQueue').mockResolvedValue([
      {
        ...mockQueue[0],
        job_id: 999,
        url: 'javascript:alert(1)',
      },
    ]);
    vi.spyOn(api, 'getJobDetail').mockResolvedValue(unsafeJob);

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText('Detailed description of Acme job...')).toBeInTheDocument();
    });

    fireEvent.keyDown(window, { key: 'o' });

    expect(openSpy).not.toHaveBeenCalled();
  });

  it('handles keyboard shortcut p to prepare and s to skip', async () => {
    const prepareSpy = vi.spyOn(api, 'prepareJob').mockResolvedValue({
      job_id: 1,
      tier: 'A',
      summary: 'Summary',
      note: 'Note',
      bullet_ids: [],
      used_fallback: false,
    });
    const skipSpy = vi.spyOn(api, 'skipJob').mockResolvedValue({
      job_id: 1,
      previous_status: 'queued',
      new_status: 'skipped',
      timestamp: '2026-10-02T00:00:00Z',
    });

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText('Detailed description of Acme job...')).toBeInTheDocument();
    });

    // Shortcut 'p' prepares
    fireEvent.keyDown(window, { key: 'p' });
    await waitFor(() => {
      expect(prepareSpy).toHaveBeenCalledWith(1);
    });

    // Shortcut 's' skips
    fireEvent.keyDown(window, { key: 's' });
    await waitFor(() => {
      expect(skipSpy).toHaveBeenCalledWith(1);
    });
  });

  it('handles shortcut a to open mark applied dialog and Esc to close it', async () => {
    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText('Detailed description of Acme job...')).toBeInTheDocument();
    });

    // Press 'a' -> opens dialog
    fireEvent.keyDown(window, { key: 'a' });

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText('Mark as Applied')).toBeInTheDocument();
    });

    // Press 'Escape' -> closes dialog
    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('handles shortcut ? to open shortcut help overlay and Esc to close it', async () => {
    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText('Detailed description of Acme job...')).toBeInTheDocument();
    });

    // Press '?'
    fireEvent.keyDown(window, { key: '?' });

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText('Keyboard Shortcuts')).toBeInTheDocument();
    });

    // Press 'Escape'
    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  // 5b. Shortcuts disabled while typing in an input
  it('disables shortcuts while typing in an input, textarea or select', async () => {
    const user = userEvent.setup();
    const prepareSpy = vi.spyOn(api, 'prepareJob').mockResolvedValue({
      job_id: 1,
      tier: 'A',
      summary: 'Summary',
      note: 'Note',
      bullet_ids: [],
      used_fallback: false,
    });

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /mark job as applied/i })).toBeInTheDocument();
    });

    // Open mark dialog so we have an active input
    await user.click(screen.getByRole('button', { name: /mark job as applied/i }));

    const contactInput = screen.getByLabelText(/referral contact/i);
    contactInput.focus();

    // Type 'p' into the input
    fireEvent.keyDown(contactInput, { key: 'p' });

    // api.prepareJob should NOT have been called because user was typing in input
    expect(prepareSpy).not.toHaveBeenCalled();
  });

  // 6. Dialog focus trap and return focus on close
  it('traps focus inside dialog and returns focus to previous element on close', async () => {
    const user = userEvent.setup();

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /mark job as applied/i })).toBeInTheDocument();
    });

    const openMarkBtn = screen.getByRole('button', { name: /mark job as applied/i });
    openMarkBtn.focus();
    expect(document.activeElement).toBe(openMarkBtn);

    // Click to open dialog
    await user.click(openMarkBtn);

    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();

    // Focus should be trapped inside dialog
    expect(dialog.contains(document.activeElement)).toBe(true);

    // Close dialog
    await user.click(screen.getByRole('button', { name: /cancel application dialog/i }));

    // Focus returns to openMarkBtn
    await waitFor(() => {
      expect(document.activeElement).toBe(openMarkBtn);
    });
  });

  // 6b. Accessibility: aria-selected on queue items
  it('marks active queue item with aria-selected="true" and others as "false"', async () => {
    renderWithProviders(<TodayPage />, {
      routerProps: { initialEntries: ['/?job=1'] },
    });

    await waitFor(() => {
      expect(screen.getByTestId('queue-item-1')).toBeInTheDocument();
    });

    const item1 = screen.getByTestId('queue-item-1');
    const item2 = screen.getByTestId('queue-item-2');

    expect(item1).toHaveAttribute('aria-selected', 'true');
    expect(item2).toHaveAttribute('aria-selected', 'false');

    // Select item 2
    fireEvent.click(item2);

    expect(item1).toHaveAttribute('aria-selected', 'false');
    expect(item2).toHaveAttribute('aria-selected', 'true');
  });
});
