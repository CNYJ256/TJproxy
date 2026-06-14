const WS_URL = 'ws://localhost:8765';
const API_URL = 'https://agent.tongji.edu.cn/api/bypass/app/?Version=2023-08-01&Action=ChatQueryInAppCenter';
const {
  buildBridgeUrl,
  buildChatRequest,
  parseSSELine,
} = globalThis.TJproxyOffscreenUtils;

let ws = null;
let reconnectDelay = 1000;
const MAX_RECONNECT_DELAY = 30000;
let reconnectTimer = null;
let connecting = false;

async function getCookies() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: 'getCookies' }, resolve);
  });
}

async function getAppId() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: 'getAppId' }, (resp) => {
      resolve(resp?.appId ?? null);
    });
  });
}

async function getBridgeToken() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: 'getBridgeToken' }, (resp) => {
      resolve(resp?.token ?? null);
    });
  });
}

function send(ws, data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(data));
  }
}

async function handleChat(ws, message) {
  console.log(`[TJproxy] >>> ${message}`);

  const appId = await getAppId();
  if (!appId) {
    send(ws, { type: 'error', message: '未打开同济应用页面' });
    return;
  }

  const { cookieHeader, csrf } = await getCookies();
  if (!cookieHeader) {
    send(ws, { type: 'error', message: '未登录' });
    return;
  }

  let response;
  try {
    response = await fetch(API_URL, buildChatRequest(message, appId, csrf, cookieHeader));
  } catch (err) {
    send(ws, { type: 'error', message: '网络请求失败' });
    return;
  }

  if (!response.ok) {
    send(ws, { type: 'error', message: '未登录' });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const state = { lastEvent: null };
  let totalTokens = 0;
  let buffer = '';
  let fullReply = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const { tokens, isDone } = parseSSELine(state, line);
        for (const token of tokens) {
          totalTokens++;
          fullReply += token;
          send(ws, { type: 'token', content: token });
        }
        if (isDone) {
          console.log(`[TJproxy] <<< ${fullReply}`);
          send(ws, { type: 'done', tokens: totalTokens });
          return;
        }
      }
    }
  } catch (err) {
    send(ws, { type: 'error', message: '流读取中断' });
  }
}

async function connect() {
  if (connecting || (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING))) {
    return;
  }

  connecting = true;
  const token = await getBridgeToken();
  if (!token) {
    connecting = false;
    scheduleReconnect();
    return;
  }

  ws = new WebSocket(buildBridgeUrl(WS_URL, token));

  ws.onopen = () => {
    connecting = false;
    reconnectDelay = 1000;
  };

  ws.onmessage = (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }

    if (data.type === 'chat' && data.message) {
      handleChat(ws, data.message);
    }
  };

  ws.onclose = () => {
    connecting = false;
    ws = null;
    scheduleReconnect();
  };

  ws.onerror = () => {
    ws.close();
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    void connect();
    reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
  }, reconnectDelay);
}

// Keep background Service Worker alive via persistent port
chrome.runtime.connect({ name: 'keepalive' });

void connect();
