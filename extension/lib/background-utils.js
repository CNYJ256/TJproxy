(function (root) {
  const COOKIE_URL = 'https://agent.tongji.edu.cn/';
  const COOKIE_NAMES = ['tenant', '_csrf', 'x-csrf-token', 'I18nextLngHiagent'];

  async function getCookies(cookiesApi) {
    const cookies = [];
    for (const name of COOKIE_NAMES) {
      const cookie = await cookiesApi.get({ url: COOKIE_URL, name });
      if (cookie) {
        cookies.push(cookie);
      }
    }
    const allCookies = await cookiesApi.getAll({ url: COOKIE_URL });
    for (const cookie of allCookies) {
      if (!cookies.some((existing) => existing.name === cookie.name)) {
        cookies.push(cookie);
      }
    }
    const cookieHeader = cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; ');
    const csrf = cookies.find((cookie) => cookie.name === 'x-csrf-token')?.value ?? '';
    return { cookieHeader, csrf };
  }

  async function ensureOffscreen(offscreenApi) {
    if (await offscreenApi.hasDocument()) {
      return;
    }
    await offscreenApi.createDocument({
      url: 'offscreen/offscreen.html',
      reasons: ['IFRAME_SCRIPTING'],
      justification: 'Maintain long-lived WebSocket connection',
    });
  }

  async function setAppId(storage, appId) {
    if (appId) {
      await storage.set({ appId });
    } else {
      await storage.remove('appId');
    }
  }

  async function getAppId(storage) {
    const result = await storage.get('appId');
    return result.appId ?? null;
  }

  async function getBridgeToken(storage, randomUUID = () => crypto.randomUUID()) {
    const result = await storage.get('bridgeToken');
    if (result.bridgeToken) {
      return result.bridgeToken;
    }
    const bridgeToken = randomUUID();
    await storage.set({ bridgeToken });
    return bridgeToken;
  }

  root.TJproxyBackgroundUtils = Object.freeze({
    ensureOffscreen,
    getAppId,
    getBridgeToken,
    getCookies,
    setAppId,
  });
})(globalThis);
