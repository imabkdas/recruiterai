import { screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NeedsJdPage } from '../pages/NeedsJdPage';
import { renderWithProviders } from './test-utils';
import { api, ApiError } from '../api/client';
import type { NeedsJdItem } from '../api/types';

interface ExtendedNeedsJdItem extends NeedsJdItem {
  snippet?: string;
  source?: string;
}

const mockNeedsJdItems: ExtendedNeedsJdItem[] = [
  {
    job_id: 101,
    company: 'TechCorp',
    title: 'Senior Software Engineer',
    location: 'Remote, India',
    url: 'https://techcorp.example.com/job/101',
    snippet: 'Looking for a Senior Python developer with strong background in distributed systems.',
    source: 'linkedin_alert',
  },
  {
    job_id: 102,
    company: 'SuspiciousCo',
    title: 'Security Researcher',
    location: 'Bangalore',
    url: 'javascript:alert("exploit")',
    snippet: 'JD snippet for 102',
    source: 'web',
  },
];

describe('NeedsJdPage (Phase 5c-2a)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders items with title, company, location, snippet, source and safe external URL link', async () => {
    vi.spyOn(api, 'getNeedsJd').mockResolvedValue(mockNeedsJdItems);

    renderWithProviders(<NeedsJdPage />);

    await waitFor(() => {
      expect(screen.getByText('TechCorp')).toBeInTheDocument();
      expect(screen.getByText('Senior Software Engineer')).toBeInTheDocument();
      expect(screen.getByText('📍 Remote, India')).toBeInTheDocument();
      expect(screen.getByText('Source: linkedin_alert')).toBeInTheDocument();
      expect(
        screen.getByText('Looking for a Senior Python developer with strong background in distributed systems.')
      ).toBeInTheDocument();
    });

    // TechCorp has a valid https URL: link should be clickable with safe attributes
    const openJobLinks = screen.getAllByRole('link', { name: /Open job/i });
    expect(openJobLinks[0]).toHaveAttribute('href', 'https://techcorp.example.com/job/101');
    expect(openJobLinks[0]).toHaveAttribute('target', '_blank');
    expect(openJobLinks[0]).toHaveAttribute('rel', 'noopener noreferrer');

    // SuspiciousCo has a javascript: URL: should show "Invalid link" disabled badge
    expect(screen.getByText('Invalid link')).toBeInTheDocument();
  });

  it('renders snippet with script and img tags as plain literal text', async () => {
    const maliciousItem: ExtendedNeedsJdItem = {
      job_id: 103,
      company: 'SecTest',
      title: 'QA Engineer',
      location: 'Hyderabad',
      url: 'https://example.com/job/103',
      snippet: 'Test <script>alert(1)</script> and <img onerror="bad()" src="x"> plain text',
    };
    vi.spyOn(api, 'getNeedsJd').mockResolvedValue([maliciousItem]);

    renderWithProviders(<NeedsJdPage />);

    await waitFor(() => {
      expect(
        screen.getByText('Test <script>alert(1)</script> and <img onerror="bad()" src="x"> plain text')
      ).toBeInTheDocument();
    });

    // Ensure no actual script tag or img tag is injected into DOM
    expect(document.querySelector('script[src*="alert"]')).toBeNull();
    expect(document.querySelector('img[onerror]')).toBeNull();
  });

  it('enforces character counter and disables submit when empty or exceeding 200,000 characters', async () => {
    vi.spyOn(api, 'getNeedsJd').mockResolvedValue([mockNeedsJdItems[0]]);

    renderWithProviders(<NeedsJdPage />);

    await waitFor(() => {
      expect(screen.getByText('TechCorp')).toBeInTheDocument();
    });

    const submitBtn = screen.getByRole('button', {
      name: /Submit job description for TechCorp/i,
    });
    const textarea = screen.getByPlaceholderText(/Paste the full job description text here/i);
    const charCounter = screen.getByTestId('char-count-101');

    // Initially empty: 0 characters, submit button disabled
    expect(charCounter).toHaveTextContent('0 / 200,000 characters');
    expect(submitBtn).toBeDisabled();

    // Type 50 characters: counter updates, button enabled
    const validText = 'A'.repeat(50);
    fireEvent.change(textarea, { target: { value: validText } });
    expect(charCounter).toHaveTextContent('50 / 200,000 characters');
    expect(submitBtn).not.toBeDisabled();

    // Exceed limit: 200,001 characters
    const overLimitText = 'B'.repeat(200001);
    fireEvent.change(textarea, { target: { value: overLimitText } });
    expect(charCounter).toHaveTextContent('200,001 / 200,000 characters (exceeds limit)');
    expect(submitBtn).toBeDisabled();
  });

  it('submits JD, removes item from list optimistically, and shows success toast with link to /run', async () => {
    let currentItems = [...mockNeedsJdItems];
    vi.spyOn(api, 'getNeedsJd').mockImplementation(async () => currentItems);
    const addJdSpy = vi.spyOn(api, 'addJd').mockImplementation(async (id: number, text: string) => {
      currentItems = currentItems.filter((i) => i.job_id !== id);
      return { job_id: id, status: 'analyzed', char_count: text.length };
    });

    renderWithProviders(<NeedsJdPage />);

    await waitFor(() => {
      expect(screen.getByText('TechCorp')).toBeInTheDocument();
    });

    const textarea = screen.getAllByPlaceholderText(/Paste the full job description text here/i)[0];
    fireEvent.change(textarea, {
      target: { value: 'Full description for TechCorp Python position...' },
    });

    const submitBtn = screen.getByRole('button', {
      name: /Submit job description for TechCorp/i,
    });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(addJdSpy).toHaveBeenCalledWith(101, 'Full description for TechCorp Python position...');
    });

    // Item 101 is removed from view
    await waitFor(() => {
      expect(screen.queryByTestId('needs-jd-card-101')).not.toBeInTheDocument();
    });

    // Success toast appears with "Added. Run the pipeline to analyze and score it." and link to Run screen
    expect(
      screen.getByText('Added. Run the pipeline to analyze and score it.')
    ).toBeInTheDocument();
    const runLink = screen.getByRole('link', { name: /Run screen/i });
    expect(runLink).toHaveAttribute('href', '/run');
  });

  it('shows error toast when addJd fails', async () => {
    vi.spyOn(api, 'getNeedsJd').mockResolvedValue([mockNeedsJdItems[0]]);
    vi.spyOn(api, 'addJd').mockRejectedValue(
      new ApiError(400, 'Invalid JD text provided', 'bad_request')
    );

    renderWithProviders(<NeedsJdPage />);

    await waitFor(() => {
      expect(screen.getByText('TechCorp')).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/Paste the full job description text here/i);
    fireEvent.change(textarea, { target: { value: 'Short test JD' } });

    const submitBtn = screen.getByRole('button', {
      name: /Submit job description for TechCorp/i,
    });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText('Invalid JD text provided')).toBeInTheDocument();
    });
  });

  it('renders empty state when there are no jobs needing JDs', async () => {
    vi.spyOn(api, 'getNeedsJd').mockResolvedValue([]);

    renderWithProviders(<NeedsJdPage />);

    await waitFor(() => {
      expect(screen.getByText('No jobs waiting for descriptions')).toBeInTheDocument();
    });
  });

  it('renders error state and handles retry when getNeedsJd fails', async () => {
    const getNeedsJdSpy = vi
      .spyOn(api, 'getNeedsJd')
      .mockRejectedValueOnce(new Error('Network error'))
      .mockResolvedValueOnce(mockNeedsJdItems);

    renderWithProviders(<NeedsJdPage />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load jobs needing descriptions')).toBeInTheDocument();
      expect(screen.getByText('Network error')).toBeInTheDocument();
    });

    const retryBtn = screen.getByRole('button', { name: /Retry/i });
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(getNeedsJdSpy).toHaveBeenCalledTimes(2);
      expect(screen.getByText('TechCorp')).toBeInTheDocument();
    });
  });
});
