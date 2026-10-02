import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ResumeEditorDialog } from '../components/today/ResumeEditorDialog';
import { renderWithProviders } from './test-utils';
import { api, ApiError } from '../api/client';

describe('ResumeEditorDialog', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('loads the LaTeX source and compiles an edited copy', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'getResumeSource').mockResolvedValue({
      source: '\\documentclass{article}\nHello',
      pdf_url: null,
    });
    const compile = vi.spyOn(api, 'compileResume').mockResolvedValue({
      pdf_url: '/api/jobs/7/resume.pdf',
    });

    renderWithProviders(
      <ResumeEditorDialog
        isOpen
        jobId={7}
        company="Prenosis"
        title="Software Engineer"
        onClose={() => undefined}
      />
    );

    const editor = await screen.findByTestId('resume-source');
    expect(editor).toHaveValue('\\documentclass{article}\nHello');

    await waitFor(() => {
      expect(compile).toHaveBeenCalledWith(7, '\\documentclass{article}\nHello');
      expect(screen.getByTestId('resume-compile')).toBeEnabled();
    });
    expect(await screen.findByTestId('resume-pdf-frame')).toHaveAttribute(
      'src',
      expect.stringContaining('/api/jobs/7/resume.pdf?v=')
    );

    const edited = '\\documentclass{article}\n\\begin{document}edited\\end{document}';
    fireEvent.change(editor, { target: { value: edited } });
    await user.click(screen.getByTestId('resume-compile'));

    await waitFor(() => {
      expect(compile).toHaveBeenLastCalledWith(7, edited);
    });
    const download = screen.getByTestId('resume-pdf-download');
    expect(download).toHaveAttribute('href', expect.stringContaining('download=1'));
    expect(download).toHaveAttribute('download', 'prenosis-software-engineer.pdf');
  });

  it('shows the saved PDF beside the source without compiling again', async () => {
    const compile = vi.spyOn(api, 'compileResume');
    vi.spyOn(api, 'getResumeSource').mockResolvedValue({
      source: '\\documentclass{article}\nHello',
      pdf_url: '/api/jobs/7/resume.pdf',
    });

    renderWithProviders(
      <ResumeEditorDialog
        isOpen
        jobId={7}
        company="Prenosis"
        title="Software Engineer"
        onClose={() => undefined}
      />
    );

    const frame = await screen.findByTestId('resume-pdf-frame');
    expect(frame).toHaveAttribute('src', expect.stringContaining('/api/jobs/7/resume.pdf?v='));
    expect(compile).not.toHaveBeenCalled();
  });

  it('shows the compiler error and keeps the editor open', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'getResumeSource').mockResolvedValue({
      source: '\\documentclass{article}',
      pdf_url: '/api/jobs/7/resume.pdf',
    });
    vi.spyOn(api, 'compileResume').mockRejectedValue(
      new ApiError(422, '! LaTeX Error: Undefined control sequence.', 'validation_error')
    );

    renderWithProviders(
      <ResumeEditorDialog
        isOpen
        jobId={7}
        company="Prenosis"
        title="Software Engineer"
        onClose={() => undefined}
      />
    );

    await screen.findByTestId('resume-source');
    await user.click(screen.getByTestId('resume-compile'));

    expect(await screen.findByTestId('resume-compile-error')).toHaveTextContent(
      'Undefined control sequence'
    );
    expect(screen.getByTestId('resume-source')).toBeInTheDocument();
  });
});
