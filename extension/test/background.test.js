import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../lib/background-utils.js';

const {
  ensureOffscreen,
  getAppId,
  getBridgeToken,
  getCookies,
  setAppId,
} = globalThis.TJproxyBackgroundUtils;

function createStorage(initial = {}) {
  const values = { ...initial };
  return {
    get: vi.fn(async (key) => ({ [key]: values[key] })),
    set: vi.fn(async (updates) => Object.assign(values, updates)),
    remove: vi.fn(async (key) => delete values[key]),
  };
}

describe('getCookies', () => {
  const cookiesApi = { get: vi.fn(), getAll: vi.fn() };

  beforeEach(() => {
    vi.resetAllMocks();
    cookiesApi.get.mockResolvedValue(null);
  });

  it('assembles the Cookie header and extracts the csrf token', async () => {
    cookiesApi.getAll.mockResolvedValue([
      { name: 'token', value: 'abc123' },
      { name: 'x-csrf-token', value: 'csrf-value' },
    ]);

    await expect(getCookies(cookiesApi)).resolves.toEqual({
      cookieHeader: 'token=abc123; x-csrf-token=csrf-value',
      csrf: 'csrf-value',
    });
    expect(cookiesApi.getAll).toHaveBeenCalledWith({
      url: 'https://agent.tongji.edu.cn/',
    });
  });

  it('returns empty values when no cookies exist', async () => {
    cookiesApi.getAll.mockResolvedValue([]);
    await expect(getCookies(cookiesApi)).resolves.toEqual({
      cookieHeader: '',
      csrf: '',
    });
  });
});

describe('ensureOffscreen', () => {
  const offscreenApi = {
    hasDocument: vi.fn(),
    createDocument: vi.fn(),
  };

  beforeEach(() => vi.resetAllMocks());

  it('creates the offscreen document only when missing', async () => {
    offscreenApi.hasDocument.mockResolvedValue(false);
    await ensureOffscreen(offscreenApi);

    expect(offscreenApi.createDocument).toHaveBeenCalledWith({
      url: 'offscreen/offscreen.html',
      reasons: ['IFRAME_SCRIPTING'],
      justification: 'Maintain long-lived WebSocket connection',
    });
  });

  it('does not recreate an existing offscreen document', async () => {
    offscreenApi.hasDocument.mockResolvedValue(true);
    await ensureOffscreen(offscreenApi);
    expect(offscreenApi.createDocument).not.toHaveBeenCalled();
  });
});

describe('stored AppID', () => {
  it('persists and reads AppID from session storage', async () => {
    const storage = createStorage();
    await setAppId(storage, 'app-a');
    await expect(getAppId(storage)).resolves.toBe('app-a');
  });

  it('removes the AppID when leaving an application page', async () => {
    const storage = createStorage({ appId: 'app-a' });
    await setAppId(storage, null);
    await expect(getAppId(storage)).resolves.toBeNull();
    expect(storage.remove).toHaveBeenCalledWith('appId');
  });
});

describe('bridge token', () => {
  it('reuses a token from persistent local storage', async () => {
    const storage = createStorage({ bridgeToken: 'existing-token' });
    const randomUUID = vi.fn(() => 'new-token');

    await expect(getBridgeToken(storage, randomUUID)).resolves.toBe('existing-token');
    expect(randomUUID).not.toHaveBeenCalled();
  });

  it('creates and persists a token when missing', async () => {
    const storage = createStorage();
    await expect(getBridgeToken(storage, () => 'new-token')).resolves.toBe('new-token');
    expect(storage.set).toHaveBeenCalledWith({ bridgeToken: 'new-token' });
  });
});
