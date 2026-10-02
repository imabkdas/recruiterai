import { screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JobsPage } from '../pages/JobsPage';
import { renderWithProviders } from './test-utils';
import { api } from '../api/client';
import type { JobListItem } from '../api/types';

const jobs: JobListItem[] = [
  {
    job_id: 1,
    company: 'Northwind',
    title: 'Java Engineer',
    status: 'needs_jd',
    url: 'https://example.com/jobs/1',
    location: 'Bangalore',
    posted_at: '2026-09-28T00:00:00+00:00',
  },
  {
    job_id: 2,
    company: 'Contoso',
    title: 'Backend Engineer',
    status: 'queued',
    url: 'https://example.com/jobs/2',
    location: 'Remote',
    posted_at: '2026-10-01',
  },
];

describe('JobsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('lists searched jobs and filters by status', async () => {
    const getJobs = vi.spyOn(api, 'getJobs').mockImplementation(async (filters) => {
      if (filters?.status === 'needs_jd') return jobs.filter((job) => job.status === 'needs_jd');
      return jobs;
    });

    renderWithProviders(<JobsPage />);

    await waitFor(() => {
      expect(screen.getByText('Java Engineer')).toBeInTheDocument();
    });
    expect(screen.getByText('Northwind')).toBeInTheDocument();
    expect(screen.getByText('Bangalore')).toBeInTheDocument();
    expect(screen.getByText('2026-09-28')).toBeInTheDocument();
    expect(screen.getAllByText('Needs JD').length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: 'Open career page for Northwind' })).toHaveAttribute(
      'href',
      'https://example.com/jobs/1'
    );

    fireEvent.change(screen.getByLabelText('Filter by status'), { target: { value: 'needs_jd' } });

    await waitFor(() => {
      expect(getJobs).toHaveBeenCalledWith(expect.objectContaining({ status: 'needs_jd' }));
      expect(screen.queryByText('Backend Engineer')).not.toBeInTheDocument();
    });
  });

  it('sends the location and posted-date filters', async () => {
    const getJobs = vi.spyOn(api, 'getJobs').mockResolvedValue(jobs);
    renderWithProviders(<JobsPage />);
    await waitFor(() => expect(getJobs).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText('Filter by location'), { target: { value: 'Pune' } });
    fireEvent.change(screen.getByLabelText('Posted from'), { target: { value: '2026-09-01' } });

    await waitFor(() => {
      expect(getJobs).toHaveBeenCalledWith(
        expect.objectContaining({ location: 'Pune', postedFrom: '2026-09-01' })
      );
    });
  });
});
