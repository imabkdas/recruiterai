/**
 * Typed API client for JobPilot with authentication token and normalized error handling.
 */

import type {
  ActionSuccessResponse,
  AddJDResult,
  AnswerBankItem,
  AnswerResult,
  ApplicationItem,
  FollowUpItem,
  FullStatsReport,
  JobDetail,
  JobListItem,
  JobStatusUpdate,
  MarkJobRequest,
  NeedsJdItem,
  PrepareResult,
  ProfileReport,
  ProgressSummary,
  QueueItem,
  RunStatusResponse,
  SetAnswerRequest,
} from './types';

export class ApiError extends Error {
  detail: string;
  code: string;
  status: number;

  constructor(status: number, detail: string, code: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

/**
 * Get API CSRF/auth token:
 * In dev: reads VITE_JOBPILOT_TOKEN.
 * In prod: reads <meta name="jobpilot-token"> injected into HTML.
 */
export function getApiToken(): string | null {
  if (import.meta.env.DEV && import.meta.env.VITE_JOBPILOT_TOKEN) {
    return import.meta.env.VITE_JOBPILOT_TOKEN;
  }
  if (typeof document !== 'undefined') {
    const meta = document.querySelector('meta[name="jobpilot-token"]');
    if (meta) {
      return meta.getAttribute('content');
    }
  }
  return null;
}

/**
 * Standard fetch wrapper sending X-JobPilot-Token on mutating requests
 * and normalizing errors into ApiError.
 */
export async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});

  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json');
  }

  // Non-GET mutating requests send X-JobPilot-Token
  if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
    const token = getApiToken();
    if (token) {
      headers.set('X-JobPilot-Token', token);
    }
    if (options.body && typeof options.body === 'string' && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
  }

  const response = await fetch(endpoint, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let detail = response.statusText || 'An error occurred';
    let code = 'error';

    try {
      const errJson = await response.json();
      if (errJson && typeof errJson === 'object') {
        if (typeof errJson.detail === 'string') {
          detail = errJson.detail;
        } else if (Array.isArray(errJson.detail)) {
          detail = JSON.stringify(errJson.detail);
        }
        if (typeof errJson.code === 'string') {
          code = errJson.code;
        }
      }
    } catch {
      // response body was not JSON
    }

    throw new ApiError(response.status, detail, code);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Typed API Calls
// ---------------------------------------------------------------------------

export const api = {
  getProgress: (): Promise<ProgressSummary> => apiFetch<ProgressSummary>('/api/progress'),

  getQueue: (size?: number): Promise<QueueItem[]> => {
    const url = size ? `/api/queue?size=${size}` : '/api/queue';
    return apiFetch<QueueItem[]>(url);
  },

  getNeedsJd: (): Promise<NeedsJdItem[]> => apiFetch<NeedsJdItem[]>('/api/needs-jd'),

  getJobDetail: (jobId: number): Promise<JobDetail> =>
    apiFetch<JobDetail>(`/api/jobs/${jobId}`),

  getFollowUps: (days?: number): Promise<FollowUpItem[]> => {
    const url = days ? `/api/follow-ups?days=${days}` : '/api/follow-ups';
    return apiFetch<FollowUpItem[]>(url);
  },

  prepareJob: (jobId: number): Promise<PrepareResult> =>
    apiFetch<PrepareResult>(`/api/jobs/${jobId}/prepare`, {
      method: 'POST',
    }),

  getResumeSource: (jobId: number): Promise<{ source: string; pdf_url: string | null }> =>
    apiFetch(`/api/jobs/${jobId}/resume/source`),

  compileResume: (jobId: number, source: string): Promise<{ pdf_url: string }> =>
    apiFetch(`/api/jobs/${jobId}/resume/compile`, {
      method: 'POST',
      body: JSON.stringify({ source }),
    }),

  getBaseResume: (): Promise<{ source: string; pdf_url: string | null }> =>
    apiFetch('/api/resume/source'),

  compileBaseResume: (source: string): Promise<{ pdf_url: string }> =>
    apiFetch('/api/resume/compile', {
      method: 'POST',
      body: JSON.stringify({ source }),
    }),

  markJob: (jobId: number, body: MarkJobRequest): Promise<JobStatusUpdate> =>
    apiFetch<JobStatusUpdate>(`/api/jobs/${jobId}/mark`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateApplication: (
    jobId: number,
    body: { status?: string; notes?: string; applied_at?: string; url?: string }
  ): Promise<ApplicationItem> =>
    apiFetch<ApplicationItem>(`/api/jobs/${jobId}/application`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  skipJob: (jobId: number): Promise<JobStatusUpdate> =>
    apiFetch<JobStatusUpdate>(`/api/jobs/${jobId}/skip`, {
      method: 'POST',
    }),

  addJd: (jobId: number, text: string): Promise<AddJDResult> =>
    apiFetch<AddJDResult>(`/api/jobs/${jobId}/jd`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  getJobs: (filters?: {
    location?: string;
    status?: string;
    postedFrom?: string;
    postedTo?: string;
  }): Promise<JobListItem[]> => {
    const params = new URLSearchParams();
    if (filters?.location) params.set('location', filters.location);
    if (filters?.status) params.set('status', filters.status);
    if (filters?.postedFrom) params.set('posted_from', filters.postedFrom);
    if (filters?.postedTo) params.set('posted_to', filters.postedTo);
    const query = params.toString();
    return apiFetch<JobListItem[]>(query ? `/api/jobs?${query}` : '/api/jobs');
  },

  getApplications: (status?: string): Promise<ApplicationItem[]> => {
    const url = status && status !== 'all' ? `/api/applications?status=${encodeURIComponent(status)}` : '/api/applications';
    return apiFetch<ApplicationItem[]>(url);
  },

  triggerRun: (): Promise<RunStatusResponse> =>
    apiFetch<RunStatusResponse>('/api/run', {
      method: 'POST',
    }),

  getRunStatus: (): Promise<RunStatusResponse> =>
    apiFetch<RunStatusResponse>('/api/run/status'),

  askQuestion: (question: string, jobId?: number | null): Promise<AnswerResult> =>
    apiFetch<AnswerResult>('/api/answers/ask', {
      method: 'POST',
      body: JSON.stringify({ question, job_id: jobId ?? null }),
    }),

  getAnswers: (): Promise<AnswerBankItem[]> =>
    apiFetch<AnswerBankItem[]>('/api/answers'),

  setAnswer: (answerId: number, body: SetAnswerRequest): Promise<ActionSuccessResponse> =>
    apiFetch<ActionSuccessResponse>(`/api/answers/${answerId}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  approveAnswer: (answerId: number): Promise<ActionSuccessResponse> =>
    apiFetch<ActionSuccessResponse>(`/api/answers/${answerId}/approve`, {
      method: 'POST',
    }),

  getStats: (weeks: number = 4): Promise<FullStatsReport> =>
    apiFetch<FullStatsReport>(`/api/stats?weeks=${weeks}`),

  getProfileReport: (): Promise<ProfileReport> =>
    apiFetch<ProfileReport>('/api/profile-report'),
};
