import { screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ProfileCheckPage } from '../pages/ProfileCheckPage';
import { renderWithProviders } from './test-utils';
import { api } from '../api/client';
import type { ProfileReport } from '../api/types';

const mockReportWithIssues: ProfileReport = {
  validation_errors: [
    'Profile error: years_for_forms must not exceed total experience.',
  ],
  validation_warnings: [
    'Skill "Docker" has no associated projects.',
  ],
  unset_settings: [
    'salary.expected_min',
    'notice_period_days',
    'relocation',
  ],
  unmatched_skills: [
    ['Kubernetes', 14],
    ['Go', 8],
    ['Rust', 3],
  ],
};

const mockCleanReport: ProfileReport = {
  validation_errors: [],
  validation_warnings: [],
  unset_settings: [],
  unmatched_skills: [],
};

describe('ProfileCheckPage (Phase 5c-2b)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders validation errors, warnings, unset settings explanation, and unmatched skills table', async () => {
    vi.spyOn(api, 'getProfileReport').mockResolvedValue(mockReportWithIssues);

    renderWithProviders(<ProfileCheckPage />);

    await waitFor(() => {
      expect(screen.getByText('Profile & Resume Health Check')).toBeInTheDocument();
    });

    // Validation errors & warnings
    expect(screen.getByTestId('validation-errors-alert')).toHaveTextContent(
      'Profile error: years_for_forms must not exceed total experience.'
    );
    expect(screen.getByTestId('validation-warnings-alert')).toHaveTextContent(
      'Skill "Docker" has no associated projects.'
    );

    // Unset settings count & badges
    expect(screen.getByTestId('unset-count-badge')).toHaveTextContent('3 Unset');
    expect(screen.getByTestId('unset-setting-badge-salary.expected_min')).toBeInTheDocument();
    expect(screen.getByTestId('unset-setting-badge-notice_period_days')).toBeInTheDocument();

    // Required explanation that unset settings cause NEEDS YOUR INPUT
    const explanation = screen.getByTestId('unset-settings-explanation');
    expect(explanation).toHaveTextContent(
      'Unset settings make the tool answer NEEDS YOUR INPUT'
    );

    // Unmatched skills table
    const skillsTable = screen.getByTestId('unmatched-skills-table');
    expect(skillsTable).toBeInTheDocument();
    expect(screen.getByText('Kubernetes')).toBeInTheDocument();
    expect(screen.getByText('14')).toBeInTheDocument();
    expect(screen.getByText('Go')).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
  });

  it('renders clean state when there are no validation errors or unmatched skills', async () => {
    vi.spyOn(api, 'getProfileReport').mockResolvedValue(mockCleanReport);

    renderWithProviders(<ProfileCheckPage />);

    await waitFor(() => {
      expect(screen.getByTestId('no-errors-banner')).toHaveTextContent(
        'No validation errors found in profile.yaml or resume_base.yaml.'
      );
    });

    expect(screen.queryByTestId('validation-errors-alert')).toBeNull();
    expect(screen.queryByTestId('validation-warnings-alert')).toBeNull();
    expect(screen.getByTestId('unset-count-badge')).toHaveTextContent('0 Unset');
    expect(screen.getByTestId('empty-unmatched-skills')).toHaveTextContent(
      'No unmatched skills detected across current listings.'
    );
  });

  it('renders error state and retries on failure', async () => {
    const reportSpy = vi
      .spyOn(api, 'getProfileReport')
      .mockRejectedValueOnce(new Error('Profile file read error'))
      .mockResolvedValueOnce(mockCleanReport);

    renderWithProviders(<ProfileCheckPage />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load profile report')).toBeInTheDocument();
      expect(screen.getByText('Profile file read error')).toBeInTheDocument();
    });

    const retryBtn = screen.getByRole('button', { name: /Retry/i });
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(reportSpy).toHaveBeenCalledTimes(2);
      expect(screen.getByText('Profile & Resume Health Check')).toBeInTheDocument();
    });
  });
});
