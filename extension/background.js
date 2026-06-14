let storedAppId = null;

async function getCookies() {
  // Fetch specific known cookie names individually
  const names = ['tenant', '_csrf', 'x-csrf-token', 'I18nextLngHiagent'];
  const cookies = [];
  for (const name of names) {
    const cookie = await chrome.cookies.get({
      url: 'https://agent.tongji.edu.cn/',
      name: name
    });
    if (cookie) {
      cookies.push(cookie);
    }
  }
  // Also try getAll for any cookies we might have missed
  const allCookies = await chrome.cookies.getAll({ url: 'https://agent.tongji.edu.cn/' });
  for (const c of allCookies) {
    if (!cookies.find(x => x.name === c.name)) {
      cookies.push(c);
    }
  }
  const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join('; ');
  const csrf = cookies.find((c) => c.name === 'x-csrf-token')?.value ?? '';
  return { cookieHeader, csrf };
}

function getAppId() {
  return storedAppId;
}

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

// Keep Service Worker alive via persistent port from offscreen document
chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'keepalive') {
    port.onDisconnect.addListener(() => {
      // offscreen closed, no action needed
    });
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message.type === 'appId') {
      storedAppId = message.data.appId;
      await ensureOffscreen();
      sendResponse({ ok: true });
    } else if (message.type === 'getCookies') {
      const result = await getCookies();
      sendResponse(result);
    } else if (message.type === 'getAppId') {
      sendResponse({ appId: getAppId() });
    } else if (message.type === 'ensureOffscreen') {
      await ensureOffscreen();
      sendResponse({ ok: true });
    }
  })();
  return true;
});
