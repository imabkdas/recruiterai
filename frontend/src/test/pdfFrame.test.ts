import { describe, expect, it } from 'vitest';
import { pdfViewerSrc } from '../lib/pdfFrame';

describe('pdfViewerSrc', () => {
  it('fits the page to the pane and hides the thumbnail sidebar', () => {
    expect(pdfViewerSrc('/api/jobs/7/resume.pdf?v=1')).toBe(
      '/api/jobs/7/resume.pdf?v=1#navpanes=0&toolbar=0&view=FitH'
    );
  });
});
