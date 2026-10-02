import { useEffect, useState } from 'react';
import { api, ApiError } from '../../api/client';
import { pdfViewerSrc } from '../../lib/pdfFrame';

interface ResumeWorkbenchProps {
  jobId?: number;
}

export function ResumeWorkbench({ jobId }: ResumeWorkbenchProps) {
  const [source, setSource] = useState('');
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [compiling, setCompiling] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [compileError, setCompileError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setCompileError(null);
    const load = jobId == null ? api.getBaseResume() : api.getResumeSource(jobId);
    load
      .then((body) => {
        if (cancelled) return;
        setSource(body.source);
        setPdfUrl(body.pdf_url);
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
  }, [jobId]);

  const handleCompile = async () => {
    setCompiling(true);
    setCompileError(null);
    try {
      const result =
        jobId == null ? await api.compileBaseResume(source) : await api.compileResume(jobId, source);
      setPdfUrl(`${result.pdf_url}?v=${Date.now()}`);
    } catch (err: unknown) {
      setCompileError(err instanceof ApiError ? err.detail : 'Compile failed.');
    } finally {
      setCompiling(false);
    }
  };

  return (
    <section className="space-y-2 @container" data-testid="resume-workbench">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Edit resume
        </h3>
        <button
          type="button"
          data-testid="resume-compile"
          onClick={handleCompile}
          disabled={compiling || loading || Boolean(loadError) || !source.trim()}
          className="inline-flex items-center px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-xs font-semibold rounded-lg"
        >
          {compiling ? 'Compiling…' : 'Compile'}
        </button>
      </div>
      <div className="flex flex-col @min-[720px]:flex-row gap-3 h-[75vh] min-h-[32rem]">
        <div className="flex flex-1 flex-col min-h-0 min-w-0">
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
              className="flex-1 w-full min-h-0 h-full resize-none p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words bg-gray-50 dark:bg-gray-950 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          )}
          {compileError ? (
            <pre
              data-testid="resume-compile-error"
              role="alert"
              className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 rounded-lg p-3"
            >
              {compileError}
            </pre>
          ) : null}
        </div>
        <div className="flex-1 min-h-0 min-w-0 border border-gray-200 dark:border-gray-800 rounded-lg bg-white overflow-hidden">
          {pdfUrl ? (
            <iframe
              title="Compiled resume"
              data-testid="resume-pdf-frame"
              src={pdfViewerSrc(pdfUrl)}
              className="w-full h-full bg-white"
            />
          ) : (
            <div className="h-full flex items-center justify-center text-sm text-gray-400 p-6 text-center">
              Compile to preview the PDF here.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
