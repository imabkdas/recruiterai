import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { ErrorState } from '../components/common/ErrorState';
import { Skeleton } from '../components/common/Skeleton';
import type { ProfileReport } from '../api/types';

export function ProfileCheckPage() {
  const {
    data: report,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<ProfileReport>({
    queryKey: ['profile-report'],
    queryFn: api.getProfileReport,
  });

  if (isLoading) {
    return (
      <div className="p-8 max-w-5xl mx-auto space-y-6">
        <Skeleton className="h-8 w-48 mb-2" />
        <Skeleton className="h-4 w-72 mb-8" />
        <div className="space-y-4">
          <Skeleton className="h-32 w-full rounded-xl" />
          <Skeleton className="h-32 w-full rounded-xl" />
          <Skeleton className="h-48 w-full rounded-xl" />
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-8 max-w-5xl mx-auto">
        <ErrorState
          title="Failed to load profile report"
          message={error instanceof Error ? error.message : 'Unknown error'}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  const validationErrors = report?.validation_errors || [];
  const validationWarnings = report?.validation_warnings || [];
  const unsetSettings = report?.unset_settings || [];
  const unmatchedSkills = report?.unmatched_skills || [];

  return (
    <div className="p-8 max-w-5xl mx-auto space-y-8 overflow-y-auto h-full">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          Profile & Resume Health Check
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Integrity report, validation findings, candidate settings coverage, and missing market skills.
        </p>
      </div>

      {/* 1. Validation Errors & Warnings Section */}
      <div className="space-y-4">
        <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">
          Profile & Resume Validation
        </h2>

        {/* Validation Errors */}
        {validationErrors.length > 0 ? (
          <div
            role="alert"
            data-testid="validation-errors-alert"
            className="p-5 bg-red-50 dark:bg-red-950/40 border-l-4 border-red-600 rounded-xl space-y-2 text-xs"
          >
            <div className="flex items-center gap-2 font-bold text-sm text-red-900 dark:text-red-200">
              <span>❌</span>
              <span>Validation Errors ({validationErrors.length})</span>
            </div>
            <ul className="list-disc list-inside space-y-1 text-red-800 dark:text-red-300">
              {validationErrors.map((err, idx) => (
                <li key={idx} className="font-mono">
                  {err}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div
            data-testid="no-errors-banner"
            className="p-4 bg-emerald-50/60 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 rounded-xl text-xs text-emerald-900 dark:text-emerald-200 flex items-center gap-2"
          >
            <span className="font-bold">✓</span>
            <span>No validation errors found in profile.yaml or resume_base.yaml.</span>
          </div>
        )}

        {/* Validation Warnings */}
        {validationWarnings.length > 0 && (
          <div
            role="alert"
            data-testid="validation-warnings-alert"
            className="p-5 bg-amber-50 dark:bg-amber-950/40 border-l-4 border-amber-500 rounded-xl space-y-2 text-xs"
          >
            <div className="flex items-center gap-2 font-bold text-sm text-amber-900 dark:text-amber-200">
              <span>⚠️</span>
              <span>Validation Warnings ({validationWarnings.length})</span>
            </div>
            <ul className="list-disc list-inside space-y-1 text-amber-800 dark:text-amber-300">
              {validationWarnings.map((warn, idx) => (
                <li key={idx} className="font-mono">
                  {warn}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* 2. Unset Settings Section */}
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-6 shadow-xs space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">
            Unset Candidate Settings
          </h2>
          <span
            data-testid="unset-count-badge"
            className={`px-2.5 py-0.5 rounded text-xs font-semibold ${
              unsetSettings.length > 0
                ? 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300'
                : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
            }`}
          >
            {unsetSettings.length} Unset
          </span>
        </div>

        {/* Explanatory Banner */}
        <div
          data-testid="unset-settings-explanation"
          className="p-3 bg-blue-50 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-900 rounded-lg text-xs text-blue-900 dark:text-blue-200 leading-relaxed"
        >
          <strong>Notice:</strong> Unset settings make the tool answer <strong>NEEDS YOUR INPUT</strong> for questionnaire prompts and application forms that ask about these criteria (such as notice period, salary expectations, relocation preferences, or visa sponsorship). Candidate settings are never guessed.
        </div>

        {unsetSettings.length === 0 ? (
          <p className="text-xs text-gray-500 dark:text-gray-400">
            All candidate settings are fully specified in profile.yaml.
          </p>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-gray-600 dark:text-gray-400">
              The following settings are null or missing:
            </p>
            <div className="flex flex-wrap gap-2">
              {unsetSettings.map((setting) => (
                <span
                  key={setting}
                  data-testid={`unset-setting-badge-${setting}`}
                  className="px-3 py-1 bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 font-mono text-xs rounded-md"
                >
                  {setting}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 3. Unmatched Skills Section (Read-only) */}
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-6 shadow-xs space-y-4">
        <div>
          <h2 className="text-base font-bold text-gray-900 dark:text-gray-100">
            Unmatched Market Skills (Read-Only)
          </h2>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            Skills frequently required across analyzed job listings that have no matching claims in your profile.
          </p>
        </div>

        {unmatchedSkills.length === 0 ? (
          <div
            data-testid="empty-unmatched-skills"
            className="p-6 text-center text-xs text-gray-400 dark:text-gray-500 bg-gray-50 dark:bg-gray-800/50 rounded-xl"
          >
            No unmatched skills detected across current listings.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table
              data-testid="unmatched-skills-table"
              className="w-full text-left text-xs text-gray-700 dark:text-gray-300"
            >
              <thead className="bg-gray-50 dark:bg-gray-800/80 text-[11px] font-bold uppercase text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-800">
                <tr>
                  <th className="py-2.5 px-3">Skill</th>
                  <th className="py-2.5 px-3 text-right">Job Listings Requesting</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800 font-mono text-[11px]">
                {unmatchedSkills.map(([skill, count]) => (
                  <tr key={skill} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/40">
                    <td className="py-2.5 px-3 font-semibold text-gray-900 dark:text-gray-100">
                      {skill}
                    </td>
                    <td className="py-2.5 px-3 text-right font-bold text-indigo-600 dark:text-indigo-400">
                      {count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
