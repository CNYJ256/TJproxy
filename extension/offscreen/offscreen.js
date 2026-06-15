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
const activeRequests = new Map();

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

function send(ws, data, requestId = null) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const payload = requestId ? { ...data, request_id: requestId } : data;
    ws.send(JSON.stringify(payload));
  }
}

async function handleChat(ws, message, requestId) {
  console.log(`[TJproxy] >>> ${message}`);

  const controller = new AbortController();
  if (requestId) {
    activeRequests.set(requestId, controller);
  }

  try {
    const appId = await getAppId();
    if (!appId) {
      send(ws, { type: 'error', message: '未打开同济应用页面' }, requestId);
      return;
    }

    const { cookieHeader, csrf } = await getCookies();
    if (!cookieHeader) {
      send(ws, { type: 'error', message: '未登录' }, requestId);
      return;
    }

    let response;
    try {
      response = await fetch(
        API_URL,
        buildChatRequest(message, appId, csrf, cookieHeader, controller.signal),
      );
    } catch (err) {
      if (!controller.signal.aborted) {
        send(ws, { type: 'error', message: '网络请求失败' }, requestId);
      }
      return;
    }

    if (!response.ok) {
      send(ws, { type: 'error', message: '未登录' }, requestId);
      return;
    }
    if (!response.body) {
      send(ws, { type: 'error', message: '上游未返回流式响应' }, requestId);
      return;
    }

    send(ws, { type: 'started' }, requestId);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const state = { lastEvent: null };
    let totalTokens = 0;
    let buffer = '';
    let fullReply = '';

    const processLine = (line) => {
      const { tokens, isDone, hasActivity } = parseSSELine(state, line);
      if (hasActivity && tokens.length === 0 && !isDone) {
        send(ws, { type: 'activity' }, requestId);
      }
      for (const token of tokens) {
        totalTokens++;
        fullReply += token;
        send(ws, { type: 'token', content: token }, requestId);
      }
      if (isDone) {
        console.log(`[TJproxy] <<< ${fullReply}`);
        send(ws, { type: 'done', tokens: totalTokens }, requestId);
      }
      return isDone;
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (processLine(line)) {
            return;
          }
        }
      }

      buffer += decoder.decode();
      if (buffer && processLine(buffer)) {
        return;
      }
      if (!controller.signal.aborted) {
        send(ws, { type: 'error', message: '上游流意外结束' }, requestId);
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        send(ws, { type: 'error', message: '流读取中断' }, requestId);
      }
    } finally {
      try {
        await reader.cancel();
      } catch {
        // The stream may already be closed.
      }
    }
  } finally {
    if (requestId && activeRequests.get(requestId) === controller) {
      activeRequests.delete(requestId);
    }
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
      void handleChat(ws, data.message, data.request_id ?? null);
    } else if (data.type === 'cancel' && data.request_id) {
      activeRequests.get(data.request_id)?.abort();
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
