import { describe, it, expect } from 'vitest';
import { safeExternalUrl } from '../lib/safeUrl';

describe('safeExternalUrl', () => {
  it('returns valid https and http URLs', () => {
    expect(safeExternalUrl('https://example.com/job/123')).toBe('https://example.com/job/123');
    expect(safeExternalUrl('http://jobs.company.com/apply?id=9')).toBe('http://jobs.company.com/apply?id=9');
  });

  it('rejects javascript: protocol', () => {
    expect(safeExternalUrl('javascript:alert(document.cookie)')).toBeNull();
    expect(safeExternalUrl('JAVASCRIPT:void(0)')).toBeNull();
  });

  it('rejects data: protocol', () => {
    expect(safeExternalUrl('data:text/html,<script>alert(1)</script>')).toBeNull();
    expect(safeExternalUrl('DATA:text/plain;base64,SGVsbG8=')).toBeNull();
  });

  it('rejects vbscript: protocol', () => {
    expect(safeExternalUrl('vbscript:msgbox("hello")')).toBeNull();
    expect(safeExternalUrl('VBSCRIPT:test')).toBeNull();
  });

  it('rejects relative paths', () => {
    expect(safeExternalUrl('/jobs/123')).toBeNull();
    expect(safeExternalUrl('../admin/panel')).toBeNull();
    expect(safeExternalUrl('api/jobs/1')).toBeNull();
  });

  it('rejects empty string and whitespace-only string', () => {
    expect(safeExternalUrl('')).toBeNull();
    expect(safeExternalUrl('   ')).toBeNull();
  });

  it('rejects null and undefined', () => {
    expect(safeExternalUrl(null)).toBeNull();
    expect(safeExternalUrl(undefined)).toBeNull();
  });
});
