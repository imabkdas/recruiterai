import { screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { JobDetailView } from '../components/today/JobDetailView';
import { renderWithProviders } from './test-utils';
import { api } from '../api/client';
import type { JobDetail } from '../api/types';

const mockJob: JobDetail = {
  id: 101,
  fingerprint: 'fp101',
  source: 'RemoteOK',
  company: 'CloudScale Inc',
  title: 'Senior Backend Engineer',
  location: 'Remote (India / APAC)',
  remote_type: 'full_remote',
  url: 'https://example.com/jobs/101',
  description: 'Line 1 of full job description.\nLine 2 of full description.',
  discovered_at: '2026-10-01T10:00:00Z',
  last_seen_at: '2026-10-01T10:00:00Z',
  status: 'queued',
  score: {
    total: 9.2,
    tier: 'A',
    scored_at: '2026-10-01T10:00:00Z',
    flags: ['high_match', 'visa_supported'],
    covered_skills: [
      { skill: 'Python', level: 'VERIFIED_PROFESSIONAL', evidence_ids: ['b01', 'b02'] },
      { skill: 'FastAPI', level: 'VERIFIED_PROFESSIONAL', evidence_ids: ['b03'] },
    ],
    partial_skills: [
      { skill: 'Docker', level: 'VERIFIED_PROJECT', evidence_ids: ['p01'] },
      { skill: 'AWS', level: 'VERIFIED_CERTIFICATION', evidence_ids: ['c01'] },
    ],
    learning_only_skills: [
      { skill: 'Kubernetes', level: 'LEARNING', evidence_ids: [] },
    ],
    missing_skills: ['Rust', 'GraphQL'],
  },
  application: {
    job_id: 101,
    tailored_summary: 'Senior backend engineer with 4 years building scalable Python services.',
    short_note: 'I noticed CloudScale is scaling its API gateway...',
    outreach_draft: 'Hi Hiring Team, I recently applied to the Senior Backend role...',
    bullet_ids: ['b01', 'b03'],
  },
};

describe('JobDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'getResumeSource').mockResolvedValue({
      source: '\\documentclass{article}\n',
      pdf_url: null,
    });
  });

  it('opens the resume editor when Edit resume is clicked', () => {
    const onOpenResume = vi.fn();
    renderWithProviders(<JobDetailView job={mockJob} onOpenResume={onOpenResume} />);
    fireEvent.click(screen.getByTestId('edit-resume'));
    expect(onOpenResume).toHaveBeenCalledOnce();
  });

  it('renders score, tier badge, company, title and open job link', () => {
    renderWithProviders(<JobDetailView job={mockJob} />);

    expect(screen.getByText('CloudScale Inc')).toBeInTheDocument();
    expect(screen.getByText('Senior Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText('Score: 9.2')).toBeInTheDocument();
    expect(screen.getByText('Tier A')).toBeInTheDocument();

    const openLink = screen.getByRole('link', { name: /open job/i });
    expect(openLink).toHaveAttribute('href', 'https://example.com/jobs/101');
    expect(openLink).toHaveAttribute('target', '_blank');
    expect(openLink).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('correctly groups skills into covered, partial, learning-only, and missing', () => {
    renderWithProviders(<JobDetailView job={mockJob} />);

    // 1. Covered Skills
    const coveredSection = screen.getByTestId('covered-skills');
    expect(coveredSection).toHaveTextContent('Python');
    expect(coveredSection).toHaveTextContent('[b01, b02]');
    expect(coveredSection).toHaveTextContent('FastAPI');
    expect(coveredSection).toHaveTextContent('[b03]');

    // 2. Partial Skills
    const partialSection = screen.getByTestId('partial-skills');
    expect(partialSection).toHaveTextContent('Docker');
    expect(partialSection).toHaveTextContent('[p01]');
    expect(partialSection).toHaveTextContent('AWS');
    expect(partialSection).toHaveTextContent('[c01]');

    // 3. Learning-only Skills
    const learningSection = screen.getByTestId('learning-skills');
    expect(learningSection).toHaveTextContent('Kubernetes');
    expect(learningSection).toHaveTextContent('(learning)');

    // 4. Missing Skills
    const missingSection = screen.getByTestId('missing-skills');
    expect(missingSection).toHaveTextContent('Rust');
    expect(missingSection).toHaveTextContent('GraphQL');
  });

  it('renders the current job description, not a clipped preview note', () => {
    const note =
      'The source only published a short preview of this posting, so the rest of the description is not available here.';
    const previewJob = {
      ...mockJob,
      description: `Prenosis builds precision medicine.\n\n${note}`,
    };
    const { rerender } = renderWithProviders(<JobDetailView job={previewJob} />);

    const jdContainer = screen.getByTestId('jd-text');
    expect(jdContainer).toHaveTextContent('Prenosis builds precision medicine.');
    expect(jdContainer).not.toHaveTextContent('short preview');
    expect(screen.getByTestId('jd-preview-note')).toHaveTextContent('short preview');
    expect(screen.queryByTestId('jd-toggle')).not.toBeInTheDocument();

    rerender(
      <JobDetailView
        key={17}
        job={{ ...mockJob, id: 17, description: 'Airspace Link builds airspace infrastructure.' }}
      />
    );
    expect(screen.getByTestId('jd-text')).toHaveTextContent(
      'Airspace Link builds airspace infrastructure.'
    );
    expect(screen.getByTestId('jd-text')).not.toHaveTextContent('Prenosis');
  });

  it('shows missing JD note when description is missing', () => {
    const jobWithoutJd = { ...mockJob, description: null };
    renderWithProviders(<JobDetailView job={jobWithoutJd} />);

    expect(screen.getByTestId('jd-missing-note')).toHaveTextContent(
      'Full job description not attached. Paste JD in Needs JD.'
    );
  });

  it('copies tailored summary with clipboard API and displays toast notification', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    renderWithProviders(<JobDetailView job={mockJob} />);

    const copyButtons = screen.getAllByRole('button', { name: /copy/i });
    expect(copyButtons.length).toBeGreaterThan(0);

    // Click the first copy button (tailored summary)
    fireEvent.click(copyButtons[0]);

    expect(writeTextMock).toHaveBeenCalledWith(
      'Senior backend engineer with 4 years building scalable Python services.'
    );

    // Toast message should appear
    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(
        'Copied tailored summary to clipboard!'
      );
    });
  });

  it('never displays contact details like candidate email or phone', () => {
    const { container } = renderWithProviders(<JobDetailView job={mockJob} />);

    const text = container.textContent || '';
    expect(text).not.toMatch(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/);
    expect(text).not.toMatch(/\+?\d{10,14}/);
  });

  it('renders literal text without HTML execution when description contains <script> and <img onerror>', () => {
    const xssJob: JobDetail = {
      ...mockJob,
      description: '<script>alert("xss")</script>\n<img src="invalid.jpg" onerror="alert(1)" />',
      application: {
        job_id: 101,
        tailored_summary: 'Summary with <script>console.log("safe")</script>',
        short_note: 'Note with <b onmouseover="alert(2)">bold</b>',
        outreach_draft: 'Draft with <iframe src="evil.com"></iframe>',
      },
    };

    const { container } = renderWithProviders(<JobDetailView job={xssJob} />);

    const jdElement = screen.getByTestId('jd-text');
    // Renders as plain text content
    expect(jdElement.textContent).toContain('<script>alert("xss")</script>');
    expect(jdElement.textContent).toContain('<img src="invalid.jpg" onerror="alert(1)" />');

    // Proves it does NOT render DOM elements for script or img
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img[onerror]')).toBeNull();
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('renders disabled "Invalid link" label when job URL is not http: or https:', () => {
    const unsafeJob: JobDetail = {
      ...mockJob,
      url: 'javascript:alert(document.domain)',
      apply_url: null,
    };

    renderWithProviders(<JobDetailView job={unsafeJob} />);

    // Link is not rendered
    expect(screen.queryByRole('link', { name: /open job/i })).not.toBeInTheDocument();

    // Disabled "Invalid link" label is rendered
    const invalidLabel = screen.getByLabelText(/invalid link/i);
    expect(invalidLabel).toBeInTheDocument();
    expect(invalidLabel).toHaveTextContent('Invalid link');
    expect(invalidLabel).toHaveAttribute('aria-disabled', 'true');
  });
});
