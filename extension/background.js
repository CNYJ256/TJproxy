importScripts('lib/background-utils.js');

const {
  ensureOffscreen,
  getAppId,
  getBridgeToken,
  getCookies,
  setAppId,
} = globalThis.TJproxyBackgroundUtils;

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'keepalive') {
    port.onDisconnect.addListener(() => {});
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message.type === 'appId') {
      const appId = message.data?.appId ?? null;
      await setAppId(chrome.storage.session, appId);
      if (appId) {
        await ensureOffscreen(chrome.offscreen);
      }
      sendResponse({ ok: true });
    } else if (message.type === 'getCookies') {
      sendResponse(await getCookies(chrome.cookies));
    } else if (message.type === 'getAppId') {
      sendResponse({ appId: await getAppId(chrome.storage.session) });
    } else if (message.type === 'getBridgeToken') {
      sendResponse({ token: await getBridgeToken(chrome.storage.local) });
    } else if (message.type === 'ensureOffscreen') {
      await ensureOffscreen(chrome.offscreen);
      sendResponse({ ok: true });
    }
  })().catch((error) => {
    console.error('[TJproxy] background message failed', error);
    sendResponse({ error: String(error) });
  });
  return true;
});
