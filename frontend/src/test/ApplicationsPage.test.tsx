import { screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ApplicationsPage, APPLICATION_STATUSES } from '../pages/ApplicationsPage';
import { renderWithProviders } from './test-utils';
import { api, ApiError } from '../api/client';
import type { ApplicationItem, FollowUpItem } from '../api/types';

const mockApplications: ApplicationItem[] = [
  {
    job_id: 201,
    company: 'Alpha Cloud',
    title: 'Senior Site Reliability Engineer',
    status: 'applied',
    url: 'https://example.com/jobs/alpha',
    channel: 'linkedin',
    applied_at: '2026-09-18',
    prepared_at: '2026-09-18T22:30:27.161403+00:00',
    referral_contact: 'alice@example.com',
    notes: 'Referral through Alice',
  },
  {
    job_id: 202,
    company: 'Beta Systems',
    title: 'Staff Platform Engineer',
    status: 'interview',
    channel: 'email',
    applied_at: '2026-09-25',
    prepared_at: '2026-09-26',
    referral_contact: null,
    notes: 'Technical screen scheduled',
  },
  {
    job_id: 203,
    company: 'Gamma AI',
    title: 'ML Infra Lead',
    status: 'rejected',
    channel: 'portal',
    applied_at: '2026-09-10',
    prepared_at: '2026-09-10',
    referral_contact: null,
    notes: 'Position closed',
  },
];

const mockFollowUps: FollowUpItem[] = [
  {
    job_id: 201,
    company: 'Alpha Cloud',
    title: 'Senior Site Reliability Engineer',
    status: 'applied',
    applied_at: '2026-09-18',
    days_since_applied: 14,
    channel: 'linkedin',
    referral_contact: 'alice@example.com',
    notes: 'Referral through Alice',
  },
];

describe('ApplicationsPage (Phase 5c-2a)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('verifies APPLICATION_STATUSES constant contains the required valid statuses', () => {
    expect(APPLICATION_STATUSES).toEqual([
      'new',
      'applied',
      'replied',
      'interview',
      'offer',
      'rejected',
    ]);
  });

  it('renders applications table with metadata and highlights follow-ups due', async () => {
    vi.spyOn(api, 'getApplications').mockResolvedValue(mockApplications);
    vi.spyOn(api, 'getFollowUps').mockResolvedValue(mockFollowUps);

    renderWithProviders(<ApplicationsPage />);

    await waitFor(() => {
      expect(screen.getByText('Alpha Cloud')).toBeInTheDocument();
      expect(screen.getByText('Senior Site Reliability Engineer')).toBeInTheDocument();
      expect(screen.getByText('Beta Systems')).toBeInTheDocument();
      expect(screen.getByText('Gamma AI')).toBeInTheDocument();
    });

    // Check table headers
    expect(screen.getByText('Company & Title')).toBeInTheDocument();
    expect(screen.getByText('Posted')).toBeInTheDocument();
    expect(screen.getByText('Location')).toBeInTheDocument();
    expect(screen.getByText('JD')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Job link')).toBeInTheDocument();
    expect(screen.getByText('Applied Date')).toBeInTheDocument();
    expect(screen.getByText('Note')).toBeInTheDocument();
    expect(screen.queryByText('Channel')).toBeNull();
    expect(screen.queryByText('Referral Contact')).toBeNull();
    expect(screen.queryByRole('button', { name: /Edit resume/i })).toBeNull();

    expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute(
      'href',
      'https://example.com/jobs/alpha'
    );
    expect(screen.getByText('Referral through Alice')).toBeInTheDocument();
    expect(screen.getByText('Technical screen scheduled')).toBeInTheDocument();
    expect(screen.getByLabelText('Applied date for Alpha Cloud')).toHaveValue('2026-09-18');
    expect(screen.getAllByText('No JD').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Edit job description for Alpha Cloud' })).toBeInTheDocument();
    expect(screen.queryByText('2026-09-18T22:30:27.161403+00:00')).toBeNull();
    expect(screen.getByRole('button', { name: 'Edit note for Alpha Cloud' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit job link for Alpha Cloud' })).toBeInTheDocument();
    expect(screen.getByLabelText('Change status for Alpha Cloud')).toHaveValue('applied');

    // Alpha Cloud (201) has a follow-up due: shows highlight badge
    const badge = screen.getByTestId('followup-badge-201');
    expect(badge).toHaveTextContent('⚠️ No reply after 14 days');

    // Beta Systems (202) does not have follow-up due
    expect(screen.queryByTestId('followup-badge-202')).toBeNull();
  });

  it('filters applications by status using the status filter dropdown', async () => {
    const getAppsSpy = vi
      .spyOn(api, 'getApplications')
      .mockImplementation(async (status?: string) => {
        if (!status || status === 'all') return mockApplications;
        return mockApplications.filter((a) => a.status === status);
      });
    vi.spyOn(api, 'getFollowUps').mockResolvedValue([]);

    renderWithProviders(<ApplicationsPage />);

    await waitFor(() => {
      expect(screen.getByText('Alpha Cloud')).toBeInTheDocument();
    });

    const filterSelect = screen.getByLabelText(/Filter applications by status/i);
    expect(filterSelect).toHaveValue('all');

    // Filter to 'interview'
    fireEvent.change(filterSelect, { target: { value: 'interview' } });

    await waitFor(() => {
      expect(getAppsSpy).toHaveBeenCalledWith('interview');
      expect(screen.getByText('Beta Systems')).toBeInTheDocument();
      expect(screen.queryByText('Alpha Cloud')).not.toBeInTheDocument();
      expect(screen.queryByText('Gamma AI')).not.toBeInTheDocument();
    });
  });

  it('updates status inline and displays success toast', async () => {
    vi.spyOn(api, 'getApplications').mockResolvedValue([...mockApplications]);
    vi.spyOn(api, 'getFollowUps').mockResolvedValue([]);
    const markJobSpy = vi.spyOn(api, 'updateApplication').mockResolvedValue({
      ...mockApplications[0],
      status: 'interview',
    });

    renderWithProviders(<ApplicationsPage />);

    await waitFor(() => {
      expect(screen.getByText('Alpha Cloud')).toBeInTheDocument();
    });

    const statusDropdown = screen.getByLabelText(/Change status for Alpha Cloud/i);
    expect(statusDropdown).toHaveValue('applied');

    // Change status to 'interview'
    fireEvent.change(statusDropdown, { target: { value: 'interview' } });

    await waitFor(() => {
      expect(markJobSpy).toHaveBeenCalledWith(201, { status: 'interview' });
    });

    expect(screen.getByText('Status updated to "Interview"')).toBeInTheDocument();
  });

  it('reverts status and displays backend error on 409 invalid transition conflict', async () => {
    vi.spyOn(api, 'getApplications').mockResolvedValue([...mockApplications]);
    vi.spyOn(api, 'getFollowUps').mockResolvedValue([]);
    vi.spyOn(api, 'updateApplication').mockRejectedValue(
      new ApiError(
        409,
        "Cannot transition job from 'rejected' to 'applied'. Allowed: none",
        'invalid_status_transition'
      )
    );

    renderWithProviders(<ApplicationsPage />);

    await waitFor(() => {
      expect(screen.getByText('Gamma AI')).toBeInTheDocument();
    });

    const gammaDropdown = screen.getByLabelText(/Change status for Gamma AI/i);
    expect(gammaDropdown).toHaveValue('rejected');

    // Attempt illegal transition: rejected -> applied
    fireEvent.change(gammaDropdown, { target: { value: 'applied' } });

    // Expect error toast with backend message
    await waitFor(() => {
      expect(
        screen.getByText("Cannot transition job from 'rejected' to 'applied'. Allowed: none")
      ).toBeInTheDocument();
    });

    // Expect dropdown to be reverted back to 'rejected'
    await waitFor(() => {
      expect(gammaDropdown).toHaveValue('rejected');
    });
  });

  it('renders empty state when applications list is empty', async () => {
    vi.spyOn(api, 'getApplications').mockResolvedValue([]);
    vi.spyOn(api, 'getFollowUps').mockResolvedValue([]);

    renderWithProviders(<ApplicationsPage />);

    await waitFor(() => {
      expect(screen.getByText('No jobs found')).toBeInTheDocument();
    });
  });

  it('renders error state and retries on failure', async () => {
    const getAppsSpy = vi
      .spyOn(api, 'getApplications')
      .mockRejectedValueOnce(new Error('Server unavailable'))
      .mockResolvedValueOnce(mockApplications);
    vi.spyOn(api, 'getFollowUps').mockResolvedValue([]);

    renderWithProviders(<ApplicationsPage />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load applications')).toBeInTheDocument();
      expect(screen.getByText('Server unavailable')).toBeInTheDocument();
    });

    const retryBtn = screen.getByRole('button', { name: /Retry/i });
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(getAppsSpy).toHaveBeenCalledTimes(2);
      expect(screen.getByText('Alpha Cloud')).toBeInTheDocument();
    });
  });
});
