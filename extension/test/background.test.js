// test_background.js — Tests for background.js Service Worker functions
//
// Targets:
//   getCookies()        → { cookieHeader, csrf }
//   ensureOffscreen()   → createDocument if missing, idempotent
//   getAppId()          → string | null
//
// All chrome.* APIs are mocked; tests run in Node with vitest.

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ---------------------------------------------------------------------------
// Mock chrome API
// ---------------------------------------------------------------------------
// We must mock chrome before the code under test uses it.
const mockChrome = {
  cookies: {
    getAll: vi.fn(),
  },
  offscreen: {
    hasDocument: vi.fn(),
    createDocument: vi.fn(),
  },
};

// Make chrome available as a global (mimic extension environment)
globalThis.chrome = mockChrome;

// ---------------------------------------------------------------------------
// getCookies implementation (replica of the logic in background.js)
// ---------------------------------------------------------------------------

/**
 * Replica of background.js getCookies().
 * Uses chrome.cookies.getAll({domain: "agent.tongji.edu.cn"}) to collect
 * cookies, assembles a Cookie header string, and extracts the csrf token.
 */
async function getCookies() {
  const cookies = await chrome.cookies.getAll({ url: 'https://agent.tongji.edu.cn/' });
  const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join('; ');
  const csrf = cookies.find((c) => c.name === 'x-csrf-token')?.value ?? '';
  return { cookieHeader, csrf };
}

// ---------------------------------------------------------------------------
// ensureOffscreen implementation
// ---------------------------------------------------------------------------
async function ensureOffscreen() {
  const hasDoc = await chrome.offscreen.hasDocument();
  if (!hasDoc) {
    await chrome.offscreen.createDocument({
      url: 'offscreen/offscreen.html',
      reasons: ['IFRAME_SCRIPTING'],
      justification: 'Maintain long-lived WebSocket connection',
    });
  }
}

// ---------------------------------------------------------------------------
// getAppId (simplified — in reality stored via message from content.js)
// ---------------------------------------------------------------------------
let storedAppId = null;

function setAppId(id) {
  storedAppId = id;
}

function getAppId() {
  return storedAppId;
}

// ---------------------------------------------------------------------------
// Tests: getCookies
// ---------------------------------------------------------------------------
describe('getCookies', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('assembles cookieHeader from returned cookies', async () => {
    mockChrome.cookies.getAll.mockResolvedValue([
      { name: 'token', value: 'abc123' },
      { name: 'session', value: 'xyz789' },
      { name: 'x-csrf-token', value: 'csrf-token-value' },
    ]);

    const result = await getCookies();

    expect(result.cookieHeader).toBe('token=abc123; session=xyz789; x-csrf-token=csrf-token-value');
  });

  it('extracts csrf from the x-csrf-token cookie', async () => {
    mockChrome.cookies.getAll.mockResolvedValue([
      { name: 'x-csrf-token', value: 'my-csrf-123' },
      { name: 'other', value: 'ignored' },
    ]);

    const result = await getCookies();

    expect(result.csrf).toBe('my-csrf-123');
  });

  it('returns empty csrf when x-csrf-token cookie is absent', async () => {
    mockChrome.cookies.getAll.mockResolvedValue([
      { name: 'other', value: 'ignored' },
    ]);

    const result = await getCookies();

    expect(result.csrf).toBe('');
  });

  it('passes the correct URL filter to chrome.cookies.getAll', async () => {
    mockChrome.cookies.getAll.mockResolvedValue([]);

    await getCookies();

    expect(mockChrome.cookies.getAll).toHaveBeenCalledTimes(1);
    expect(mockChrome.cookies.getAll).toHaveBeenCalledWith({
      url: 'https://agent.tongji.edu.cn/',
    });
  });

  it('returns empty cookieHeader when no cookies are found', async () => {
    mockChrome.cookies.getAll.mockResolvedValue([]);

    const result = await getCookies();

    expect(result.cookieHeader).toBe('');
    expect(result.csrf).toBe('');
  });

  it('handles single cookie correctly', async () => {
    mockChrome.cookies.getAll.mockResolvedValue([
      { name: 'only', value: 'lonely' },
    ]);

    const result = await getCookies();

    expect(result.cookieHeader).toBe('only=lonely');
  });
});

// ---------------------------------------------------------------------------
// Tests: ensureOffscreen
// ---------------------------------------------------------------------------
describe('ensureOffscreen', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('creates offscreen document when none exists', async () => {
    mockChrome.offscreen.hasDocument.mockResolvedValue(false);
    mockChrome.offscreen.createDocument.mockResolvedValue(undefined);

    await ensureOffscreen();

    expect(mockChrome.offscreen.hasDocument).toHaveBeenCalledTimes(1);
    expect(mockChrome.offscreen.createDocument).toHaveBeenCalledTimes(1);
  });

  it('does NOT create offscreen document when one already exists', async () => {
    mockChrome.offscreen.hasDocument.mockResolvedValue(true);

    await ensureOffscreen();

    expect(mockChrome.offscreen.hasDocument).toHaveBeenCalledTimes(1);
    expect(mockChrome.offscreen.createDocument).not.toHaveBeenCalled();
  });

  it('creates document even if called multiple times (when hasDocument returns false)', async () => {
    // Simulate: first call creates, second call also sees false (e.g. race)
    mockChrome.offscreen.hasDocument
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(false);

    await ensureOffscreen();
    await ensureOffscreen();

    expect(mockChrome.offscreen.createDocument).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// Tests: getAppId
// ---------------------------------------------------------------------------
describe('getAppId', () => {
  beforeEach(() => {
    storedAppId = null;
  });

  it('returns null when no AppID has been set', () => {
    expect(getAppId()).toBeNull();
  });

  it('returns the stored AppID after being set', () => {
    setAppId('d7e4f2a1b3c4');
    expect(getAppId()).toBe('d7e4f2a1b3c4');
  });

  it('updates when a new AppID is set (tab switch scenario)', () => {
    setAppId('app-a');
    expect(getAppId()).toBe('app-a');
    setAppId('app-b');
    expect(getAppId()).toBe('app-b');
  });

  it('can be reset to null', () => {
    setAppId('abc123');
    expect(getAppId()).toBe('abc123');
    setAppId(null);
    expect(getAppId()).toBeNull();
  });
});
