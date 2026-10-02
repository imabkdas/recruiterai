import { screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { Route, Routes } from 'react-router-dom';
import { ResumesPage } from '../pages/ResumesPage';
import { renderWithProviders } from './test-utils';
import { api } from '../api/client';

describe('ResumesPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('edits the base resume and does not load a job', async () => {
    const source = vi.spyOn(api, 'getBaseResume').mockResolvedValue({
      source: '\\documentclass{article}',
      pdf_url: '/api/resume.pdf',
    });
    const queue = vi.spyOn(api, 'getQueue');

    renderWithProviders(
      <Routes>
        <Route path="/resumes" element={<ResumesPage />} />
      </Routes>,
      { routerProps: { initialEntries: ['/resumes?job=7'] } }
    );

    expect(await screen.findByTestId('resume-source')).toHaveValue('\\documentclass{article}');
    expect(source).toHaveBeenCalledOnce();
    expect(queue).not.toHaveBeenCalled();
    expect(screen.getByTestId('resume-pdf-frame')).toHaveAttribute(
      'src',
      expect.stringContaining('/api/resume.pdf')
    );
  });
});
