/**
 * Safely validate external URLs.
 * Returns the URL string only when its protocol is http: or https:, otherwise null.
 */
export function safeExternalUrl(url: string | null | undefined): string | null {
  if (!url || typeof url !== 'string') {
    return null;
  }
  const trimmed = url.trim();
  if (!trimmed) {
    return null;
  }
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
    return null;
  } catch {
    return null;
  }
}
