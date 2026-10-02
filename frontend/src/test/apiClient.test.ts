import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiFetch, ApiError, getApiToken } from '../api/client';

describe('apiClient', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    document.head.innerHTML = '';
  });

  it('reads token from meta tag when present', () => {
    const meta = document.createElement('meta');
    meta.name = 'jobpilot-token';
    meta.content = 'prod-secret-token-xyz';
    document.head.appendChild(meta);

    expect(getApiToken()).toBe('prod-secret-token-xyz');
  });

  it('sends X-JobPilot-Token on mutating requests (POST, PUT, DELETE)', async () => {
    const meta = document.createElement('meta');
    meta.name = 'jobpilot-token';
    meta.content = 'active-session-token-123';
    document.head.appendChild(meta);

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true }),
    });
    vi.stubGlobal('fetch', fetchMock);

    // 1. POST request
    await apiFetch('/api/jobs/1/prepare', {
      method: 'POST',
      body: JSON.stringify({}),
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, options] = fetchMock.mock.calls[0];
    const headers = options.headers as Headers;
    expect(headers.get('X-JobPilot-Token')).toBe('active-session-token-123');
    expect(headers.get('Content-Type')).toBe('application/json');

    // 2. GET request should NOT attach X-JobPilot-Token
    fetchMock.mockClear();
    await apiFetch('/api/queue');
    const [, getOptions] = fetchMock.mock.calls[0];
    const getHeaders = getOptions.headers as Headers;
    expect(getHeaders.get('X-JobPilot-Token')).toBeNull();
  });

  it('normalizes error response body {detail, code} into typed ApiError', async () => {
    const errorBody = {
      detail: 'Cannot transition status from skipped to applied',
      code: 'invalid_status_transition',
    };

    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      json: async () => errorBody,
    });
    vi.stubGlobal('fetch', fetchMock);

    try {
      await apiFetch('/api/jobs/1/mark', {
        method: 'POST',
        body: JSON.stringify({ status: 'applied' }),
      });
      expect.fail('apiFetch should have thrown ApiError');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(409);
      expect(apiErr.code).toBe('invalid_status_transition');
      expect(apiErr.detail).toBe('Cannot transition status from skipped to applied');
    }
  });
});
