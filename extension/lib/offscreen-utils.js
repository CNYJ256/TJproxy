(function (root) {
  function parseSSELine(state, line) {
    const trimmed = line.trim();
    const tokens = [];
    let isDone = false;
    let hasActivity = false;

    if (!trimmed) {
      return { tokens, isDone, hasActivity };
    }

    if (trimmed.startsWith('event:')) {
      state.lastEvent = trimmed.slice(6).trim();
      hasActivity = true;
      return { tokens, isDone, hasActivity };
    }

    if (trimmed.startsWith('data:')) {
      let jsonText = trimmed.slice(5).trim();
      if (jsonText.startsWith('data: ')) {
        jsonText = jsonText.slice(6).trim();
      }
      if (!jsonText) {
        return { tokens, isDone, hasActivity };
      }

      let parsed;
      try {
        parsed = JSON.parse(jsonText);
      } catch {
        return { tokens, isDone, hasActivity };
      }

      hasActivity = true;
      const eventType = parsed.event || state.lastEvent;
      if (eventType === 'message' && typeof parsed.answer === 'string') {
        tokens.push(parsed.answer);
      } else if (eventType === 'message_end') {
        isDone = true;
      }
    }

    return { tokens, isDone, hasActivity };
  }

  function buildBridgeUrl(baseUrl, token) {
    return `${baseUrl}/bridge?token=${encodeURIComponent(token)}`;
  }

  function buildChatRequest(message, appId, csrf, cookieHeader, signal) {
    const options = {
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
    };
    if (signal) {
      options.signal = signal;
    }
    return options;
  }

  root.TJproxyOffscreenUtils = Object.freeze({
    buildBridgeUrl,
    buildChatRequest,
    parseSSELine,
  });
})(globalThis);
