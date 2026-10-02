import type { JobDetail } from '../../api/types';
import { useToast } from '../../context/useToast';
import { safeExternalUrl } from '../../lib/safeUrl';

interface JobDetailViewProps {
  job: JobDetail;
  onPrepare?: () => void;
  isPreparing?: boolean;
  onOpenMarkApplied?: () => void;
  onOpenResume?: () => void;
  onSkip?: () => void;
  isSkipping?: boolean;
}

const PREVIEW_NOTE =
  'The source only published a short preview of this posting, so the rest of the description is not available here.';

function jobDescriptionBody(description: string | null | undefined): {
  body: string;
  isPreview: boolean;
} {
  const raw = description?.trim() ?? '';
  if (!raw) return { body: '', isPreview: false };
  if (!raw.includes(PREVIEW_NOTE)) return { body: raw, isPreview: false };
  return { body: raw.replace(PREVIEW_NOTE, '').trim(), isPreview: true };
}

function skillMentions(item: object): number {
  const value = (item as { mentions?: number }).mentions;
  return typeof value === 'number' ? value : 0;
}

async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator?.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fallback below
    }
  }

  try {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    const success = document.execCommand('copy');
    document.body.removeChild(textArea);
    return success;
  } catch {
    return false;
  }
}

export function JobDetailView({
  job,
  onPrepare,
  isPreparing,
  onOpenMarkApplied,
  onOpenResume,
  onSkip,
  isSkipping,
}: JobDetailViewProps) {
  const { showToast } = useToast();
  const { body: descriptionBody, isPreview } = jobDescriptionBody(job.description);

  const handleCopy = async (text: string, label: string) => {
    const ok = await copyToClipboard(text);
    if (ok) {
      showToast(`Copied ${label} to clipboard!`, 'success');
    } else {
      showToast(`Failed to copy ${label}`, 'error');
    }
  };

  const safeUrl = safeExternalUrl(job.url || job.apply_url);
  const score = job.score;
  const isTierA = score?.tier?.toUpperCase() === 'A';
  const isTierB = score?.tier?.toUpperCase() === 'B';

  const coveredSkills = score?.covered_skills || [];
  const partialSkills = score?.partial_skills || [];
  const learningSkills = score?.learning_only_skills || [];
  const missingSkills = score?.missing_skills || [];

  return (
    <div className="p-6 space-y-6 max-w-4xl mx-auto overflow-y-auto h-full">
      {/* Header Info */}
      <div className="flex flex-col md:flex-row md:items-start justify-between gap-4 pb-6 border-b border-gray-200 dark:border-gray-800">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-indigo-600 dark:text-indigo-400">
              {job.company}
            </span>
            <span className="text-gray-300 dark:text-gray-700">•</span>
            <span className="text-xs text-gray-500 dark:text-gray-400">Source: {job.source}</span>
          </div>
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 leading-tight">
            {job.title}
          </h2>
          <div className="flex flex-wrap items-center gap-3 text-xs text-gray-600 dark:text-gray-400 pt-1">
            {job.location && <span>📍 {job.location}</span>}
            {job.remote_type && <span>🌐 {job.remote_type}</span>}
            {job.posted_at && <span>📅 Posted: {job.posted_at}</span>}
          </div>
        </div>

        {/* Actions & Links */}
        <div className="flex flex-wrap items-center gap-2 flex-shrink-0">
          {onOpenResume && (
            <button
              type="button"
              onClick={onOpenResume}
              data-testid="edit-resume"
              aria-label="Edit resume"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white hover:bg-gray-50 dark:bg-gray-900 dark:hover:bg-gray-800 text-gray-800 dark:text-gray-100 border border-gray-300 dark:border-gray-700 text-xs font-semibold rounded-lg shadow-2xs transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-gray-900"
            >
              Edit resume
            </button>
          )}

          {onPrepare && (
            <button
              type="button"
              onClick={onPrepare}
              disabled={isPreparing}
              aria-label="Prepare application drafts"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950/60 dark:hover:bg-indigo-900/80 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 disabled:opacity-50 text-xs font-semibold rounded-lg shadow-2xs transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-gray-900"
            >
              {isPreparing ? (
                <>
                  <span className="animate-spin text-xs">⏳</span>
                  <span>Preparing...</span>
                </>
              ) : (
                <>
                  <span>Prepare</span>
                  <kbd className="hidden sm:inline px-1 py-0.2 text-[10px] font-mono bg-white/70 dark:bg-black/40 rounded border border-indigo-300 dark:border-indigo-700">
                    p
                  </kbd>
                </>
              )}
            </button>
          )}

          {onOpenMarkApplied && (
            <button
              type="button"
              onClick={onOpenMarkApplied}
              aria-label="Mark job as applied"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg shadow-2xs transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 dark:focus:ring-offset-gray-900"
            >
              <span>Mark applied</span>
              <kbd className="hidden sm:inline px-1 py-0.2 text-[10px] font-mono bg-emerald-800/60 rounded border border-emerald-500">
                a
              </kbd>
            </button>
          )}

          {onSkip && (
            <button
              type="button"
              onClick={onSkip}
              disabled={isSkipping}
              aria-label="Skip job"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 disabled:opacity-50 text-xs font-medium rounded-lg shadow-2xs transition-colors focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-offset-2 dark:focus:ring-offset-gray-900"
            >
              <span>Skip</span>
              <kbd className="hidden sm:inline px-1 py-0.2 text-[10px] font-mono bg-white dark:bg-gray-900 rounded border border-gray-300 dark:border-gray-700">
                s
              </kbd>
            </button>
          )}

          {safeUrl ? (
            <a
              href={safeUrl}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Open job link"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg shadow-2xs transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-gray-900"
            >
              <span>Open job</span>
              <span>↗</span>
              <kbd className="hidden sm:inline px-1 py-0.2 text-[10px] font-mono bg-indigo-800/60 rounded border border-indigo-400">
                o
              </kbd>
            </a>
          ) : (
            <span
              aria-disabled="true"
              aria-label="Invalid link"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-100 dark:bg-gray-800 text-gray-400 dark:text-gray-500 text-xs font-medium rounded-lg cursor-not-allowed border border-gray-200 dark:border-gray-700 select-none"
            >
              <span>Invalid link</span>
            </span>
          )}
        </div>
      </div>

      <section className="space-y-2" data-testid="job-description">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            Job Description
          </h3>
          {descriptionBody ? (
            <span className="text-xs text-gray-400 dark:text-gray-500">
              {descriptionBody.length.toLocaleString()} chars
            </span>
          ) : null}
        </div>
        {descriptionBody ? (
          <div
            key={job.id}
            data-testid="jd-text"
            className="p-4 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl text-sm whitespace-pre-wrap leading-relaxed text-gray-800 dark:text-gray-200"
          >
            {descriptionBody}
          </div>
        ) : (
          <div
            data-testid="jd-missing-note"
            className="p-4 bg-amber-50/50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900/40 rounded-xl text-xs text-amber-800 dark:text-amber-300"
          >
            Full job description not attached. Paste JD in Needs JD.
          </div>
        )}
        {isPreview ? (
          <p
            data-testid="jd-preview-note"
            className="text-xs text-amber-800 dark:text-amber-300"
          >
            {PREVIEW_NOTE}
          </p>
        ) : null}
      </section>

      {/* Score & Tier Card */}
      {score && (
        <div className="p-4 bg-gray-50 dark:bg-gray-850 border border-gray-200 dark:border-gray-800 rounded-xl space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span
                className={`px-2.5 py-1 rounded-md text-xs font-bold tracking-wide uppercase ${
                  isTierA
                    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
                    : isTierB
                    ? 'bg-cyan-100 text-cyan-800 dark:bg-cyan-950 dark:text-cyan-300'
                    : 'bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-300'
                }`}
              >
                Tier {score.tier}
              </span>
              <span className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Score: {score.total.toFixed(1)}
              </span>
            </div>
            {score.scored_at && (
              <span className="text-[11px] text-gray-400">Scored: {score.scored_at.slice(0, 10)}</span>
            )}
          </div>

          {/* Flags */}
          {score.flags && score.flags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {score.flags.map((flag, idx) => (
                <span
                  key={idx}
                  className="px-2 py-0.5 text-xs font-medium rounded bg-amber-100 text-amber-800 dark:bg-amber-950/80 dark:text-amber-300"
                >
                  🚩 {flag}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Skills Grouping Breakdown */}
      {score && (
        <div className="space-y-4">
          <h3 className="text-sm font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
            Skills Breakdown
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* 1. Covered Skills */}
            <div className="p-4 bg-emerald-50/50 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-900/40 rounded-xl space-y-2">
              <div className="flex items-center justify-between text-xs font-semibold text-emerald-900 dark:text-emerald-200">
                <span>Covered (Professional)</span>
                <span>{coveredSkills.length} skills</span>
              </div>
              <div className="flex flex-wrap gap-1.5" data-testid="covered-skills">
                {coveredSkills.length > 0 ? (
                  coveredSkills.map((item, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded bg-emerald-100 dark:bg-emerald-900/60 text-emerald-800 dark:text-emerald-200 text-xs font-medium"
                    >
                      <span>{item.skill}</span>
                      {skillMentions(item) > 0 && (
                        <span className="text-[10px] text-emerald-700 dark:text-emerald-300">
                          ×{skillMentions(item)}
                        </span>
                      )}
                      {item.evidence_ids && item.evidence_ids.length > 0 && (
                        <span className="text-[10px] text-emerald-600 dark:text-emerald-400">
                          [{item.evidence_ids.join(', ')}]
                        </span>
                      )}
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-gray-400 italic">None</span>
                )}
              </div>
            </div>

            {/* 2. Partial Skills */}
            <div className="p-4 bg-blue-50/50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-900/40 rounded-xl space-y-2">
              <div className="flex items-center justify-between text-xs font-semibold text-blue-900 dark:text-blue-200">
                <span>Partial (Project / Cert)</span>
                <span>{partialSkills.length} skills</span>
              </div>
              <div className="flex flex-wrap gap-1.5" data-testid="partial-skills">
                {partialSkills.length > 0 ? (
                  partialSkills.map((item, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded bg-blue-100 dark:bg-blue-900/60 text-blue-800 dark:text-blue-200 text-xs font-medium"
                    >
                      <span>{item.skill}</span>
                      {skillMentions(item) > 0 && (
                        <span className="text-[10px] text-blue-700 dark:text-blue-300">
                          ×{skillMentions(item)}
                        </span>
                      )}
                      {item.evidence_ids && item.evidence_ids.length > 0 && (
                        <span className="text-[10px] text-blue-600 dark:text-blue-400">
                          [{item.evidence_ids.join(', ')}]
                        </span>
                      )}
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-gray-400 italic">None</span>
                )}
              </div>
            </div>

            {/* 3. Learning-only Skills */}
            <div className="p-4 bg-purple-50/50 dark:bg-purple-950/20 border border-purple-200 dark:border-purple-900/40 rounded-xl space-y-2">
              <div className="flex items-center justify-between text-xs font-semibold text-purple-900 dark:text-purple-200">
                <span>Learning Only</span>
                <span>{learningSkills.length} skills</span>
              </div>
              <div className="flex flex-wrap gap-1.5" data-testid="learning-skills">
                {learningSkills.length > 0 ? (
                  learningSkills.map((item, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded bg-purple-100 dark:bg-purple-900/60 text-purple-800 dark:text-purple-200 text-xs font-medium"
                    >
                      <span>{item.skill}</span>
                      <span className="text-[10px] text-purple-600 dark:text-purple-400">(learning)</span>
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-gray-400 italic">None</span>
                )}
              </div>
            </div>

            {/* 4. Missing Skills */}
            <div className="p-4 bg-rose-50/50 dark:bg-rose-950/20 border border-rose-200 dark:border-rose-900/40 rounded-xl space-y-2">
              <div className="flex items-center justify-between text-xs font-semibold text-rose-900 dark:text-rose-200">
                <span>Missing Skills</span>
                <span>{missingSkills.length} skills</span>
              </div>
              <div className="flex flex-wrap gap-1.5" data-testid="missing-skills">
                {missingSkills.length > 0 ? (
                  missingSkills.map((skillName, i) => (
                    <span
                      key={i}
                      className="px-2 py-1 rounded bg-rose-100 dark:bg-rose-900/60 text-rose-800 dark:text-rose-200 text-xs font-medium"
                    >
                      {skillName}
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-gray-400 italic">None</span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Application Drafts */}
      <div className="space-y-4 pt-2">
        <h3 className="text-sm font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Application Drafts
        </h3>

        {job.application ? (
          <div className="space-y-4">
            {/* Tailored Summary */}
            {job.application.tailored_summary && (
              <div className="p-4 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                    Tailored Summary
                  </h4>
                  <button
                    type="button"
                    onClick={() => handleCopy(job.application!.tailored_summary!, 'tailored summary')}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded transition-colors"
                  >
                    <span>📋</span>
                    <span>Copy</span>
                  </button>
                </div>
                <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap leading-relaxed">
                  {job.application.tailored_summary}
                </p>
              </div>
            )}

            {/* Short Note */}
            {job.application.short_note && (
              <div className="p-4 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                    Short Note
                  </h4>
                  <button
                    type="button"
                    onClick={() => handleCopy(job.application!.short_note!, 'short note')}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded transition-colors"
                  >
                    <span>📋</span>
                    <span>Copy</span>
                  </button>
                </div>
                <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap leading-relaxed">
                  {job.application.short_note}
                </p>
              </div>
            )}

            {/* Outreach Draft */}
            {job.application.outreach_draft && (
              <div className="p-4 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                    Outreach Draft
                  </h4>
                  <button
                    type="button"
                    onClick={() => handleCopy(job.application!.outreach_draft!, 'outreach draft')}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded transition-colors"
                  >
                    <span>📋</span>
                    <span>Copy</span>
                  </button>
                </div>
                <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap leading-relaxed">
                  {job.application.outreach_draft}
                </p>
              </div>
            )}
          </div>
        ) : (
          <div className="p-4 bg-gray-50 dark:bg-gray-850 border border-dashed border-gray-300 dark:border-gray-700 rounded-xl text-center text-xs text-gray-500 dark:text-gray-400">
            Application drafts not yet prepared for this job.
          </div>
        )}
      </div>

    </div>
  );
}
