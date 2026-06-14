const WS_URL = 'ws://localhost:8765';
const API_URL = 'https://agent.tongji.edu.cn/api/bypass/app/?Version=2023-08-01&Action=ChatQueryInAppCenter';

let ws = null;
let reconnectDelay = 1000;
const MAX_RECONNECT_DELAY = 30000;
let reconnectTimer = null;

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

function parseSSELine(state, line) {
  const trimmed = line.trim();
  const tokens = [];
  let isDone = false;

  if (trimmed === '') {
    return { tokens, isDone };
  }

  if (trimmed.startsWith('event:')) {
    state.lastEvent = trimmed.slice(6).trim();
    return { tokens, isDone };
  }

  if (trimmed.startsWith('data:')) {
    let jsonStr = trimmed.slice(5).trim();
    // 同济 API 有双重 data: 前缀 (data:data: {...})
    if (jsonStr.startsWith('data: ')) {
      jsonStr = jsonStr.slice(6).trim();
    }
    if (!jsonStr) {
      return { tokens, isDone };
    }

    let parsed;
    try {
      parsed = JSON.parse(jsonStr);
    } catch {
      return { tokens, isDone };
    }

    const eventType = parsed.event || state.lastEvent;

    if (eventType === 'message' && typeof parsed.answer === 'string') {
      tokens.push(parsed.answer);
    } else if (eventType === 'message_end') {
      isDone = true;
    }
  }

  return { tokens, isDone };
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
    response = await fetch(API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-csrf-token': csrf,
        'Cookie': cookieHeader,
      },
      body: JSON.stringify({
        Query: message,
        AppID: appId,
        InputData: [],
        QueryExtends: { Files: [] },
      }),
    });
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

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
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
    connect();
    reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
  }, reconnectDelay);
}

// Keep background Service Worker alive via persistent port
chrome.runtime.connect({ name: 'keepalive' });

connect();
