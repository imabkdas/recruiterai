import { useEffect, useState } from 'react';
import { api, ApiError } from '../../api/client';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { pdfViewerSrc } from '../../lib/pdfFrame';

interface ResumeEditorDialogProps {
  isOpen: boolean;
  jobId: number;
  company: string;
  title: string;
  onClose: () => void;
}

function previewUrl(pdfUrl: string): string {
  const [path, query = ''] = pdfUrl.split('?');
  const params = new URLSearchParams(query);
  params.delete('download');
  if (!params.has('v')) params.set('v', String(Date.now()));
  return `${path}?${params.toString()}`;
}

function downloadUrl(pdfUrl: string): string {
  const [path, query = ''] = pdfUrl.split('?');
  const params = new URLSearchParams(query);
  params.set('download', '1');
  return `${path}?${params.toString()}`;
}

function pdfFilename(company: string, title: string): string {
  const slug = `${company} ${title}`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  return `${slug || 'resume'}.pdf`;
}

export function ResumeEditorDialog({
  isOpen,
  jobId,
  company,
  title,
  onClose,
}: ResumeEditorDialogProps) {
  const containerRef = useFocusTrap(isOpen, onClose);
  const [source, setSource] = useState('');
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [compiling, setCompiling] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [compileError, setCompileError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setCompileError(null);
    setPdfUrl(null);
    api
      .getResumeSource(jobId)
      .then(async (body) => {
        if (cancelled) return;
        setSource(body.source);
        setLoading(false);
        if (body.pdf_url) {
          setPdfUrl(previewUrl(body.pdf_url));
          return;
        }
        if (!body.source.trim()) return;
        setCompiling(true);
        try {
          const result = await api.compileResume(jobId, body.source);
          if (!cancelled) setPdfUrl(previewUrl(result.pdf_url));
        } catch (err: unknown) {
          if (!cancelled) {
            setCompileError(err instanceof ApiError ? err.detail : 'Compile failed.');
          }
        } finally {
          if (!cancelled) setCompiling(false);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(err instanceof ApiError ? err.detail : 'Could not load the resume.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, jobId]);

  if (!isOpen) {
    return null;
  }

  const handleCompile = async () => {
    setCompiling(true);
    setCompileError(null);
    try {
      const result = await api.compileResume(jobId, source);
      setPdfUrl(previewUrl(result.pdf_url));
    } catch (err: unknown) {
      setCompileError(err instanceof ApiError ? err.detail : 'Compile failed.');
    } finally {
      setCompiling(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/50">
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="resume-editor-title"
        className="w-[96vw] h-[92vh] max-w-[1400px] flex flex-col bg-white dark:bg-gray-900 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-800"
      >
        <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-800">
          <div>
            <h2 id="resume-editor-title" className="text-lg font-bold text-gray-900 dark:text-gray-100">
              Edit resume
            </h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              {company} — {title}. The preview on the right matches the LaTeX on the left.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {pdfUrl ? (
              <a
                href={downloadUrl(pdfUrl)}
                download={pdfFilename(company, title)}
                data-testid="resume-pdf-download"
                className="text-sm font-semibold text-indigo-600 dark:text-indigo-400 hover:underline"
              >
                Download PDF
              </a>
            ) : null}
            <button
              type="button"
              data-testid="resume-compile"
              onClick={handleCompile}
              disabled={compiling || loading || Boolean(loadError) || !source.trim()}
              className="inline-flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-semibold rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              {compiling ? 'Compiling…' : 'Compile'}
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close dialog"
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1 rounded-md focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="flex-1 min-h-0 grid grid-cols-2 gap-3 p-3">
          <div className="min-h-0 flex flex-col">
            {loading ? (
              <p className="text-sm text-gray-500">Loading resume…</p>
            ) : loadError ? (
              <p className="text-sm text-red-600 dark:text-red-400" role="alert">
                {loadError}
              </p>
            ) : (
              <textarea
                data-testid="resume-source"
                aria-label="Resume LaTeX"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                spellCheck={false}
                wrap="soft"
                className="flex-1 min-h-0 w-full resize-none p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words bg-gray-50 dark:bg-gray-950 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            )}
            {compileError ? (
              <pre
                data-testid="resume-compile-error"
                role="alert"
                className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 rounded-lg p-3"
              >
                {compileError}
              </pre>
            ) : null}
          </div>

          <div className="min-h-0 h-full border border-gray-200 dark:border-gray-800 rounded-lg overflow-hidden bg-white">
            {pdfUrl ? (
              <iframe
                title="Compiled resume"
                data-testid="resume-pdf-frame"
                src={pdfViewerSrc(pdfUrl)}
                className="w-full h-full bg-white"
              />
            ) : (
              <div className="h-full flex items-center justify-center text-sm text-gray-400 p-6 text-center">
                {compiling ? 'Compiling the resume on the left…' : 'The compiled PDF will show here.'}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
