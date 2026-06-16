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

  // SHA-256 constants
  const K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
  ];

  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }
  function ch(x, y, z) { return (x & y) ^ (~x & z); }
  function maj(x, y, z) { return (x & y) ^ (x & z) ^ (y & z); }
  function bsig0(x) { return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22); }
  function bsig1(x) { return rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25); }
  function ssig0(x) { return rotr(x, 7) ^ rotr(x, 18) ^ (x >>> 3); }
  function ssig1(x) { return rotr(x, 17) ^ rotr(x, 19) ^ (x >>> 10); }

  function computeSha256Hex(bytes) {
    const msgBytes = new Uint8Array(bytes.length + 1 + 8 + (64 - ((bytes.length + 9) & 63)));
    msgBytes.set(bytes);
    msgBytes[bytes.length] = 0x80;
    const bitLen = bytes.length * 8;
    const dv = new DataView(msgBytes.buffer, msgBytes.length - 8);
    dv.setUint32(0, (bitLen / 0x100000000) | 0);
    dv.setUint32(4, bitLen >>> 0);

    let H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const W = new Array(64);

    for (let off = 0; off < msgBytes.length; off += 64) {
      for (let t = 0; t < 16; t++) {
        W[t] = (msgBytes[off + t * 4] << 24) | (msgBytes[off + t * 4 + 1] << 16) |
               (msgBytes[off + t * 4 + 2] << 8) | msgBytes[off + t * 4 + 3];
      }
      for (let t = 16; t < 64; t++) {
        W[t] = (ssig1(W[t - 2]) + W[t - 7] + ssig0(W[t - 15]) + W[t - 16]) >>> 0;
      }

      let [a, b, c, d, e, f, g, h] = H;
      for (let t = 0; t < 64; t++) {
        const T1 = (h + bsig1(e) + ch(e, f, g) + K[t] + W[t]) >>> 0;
        const T2 = (bsig0(a) + maj(a, b, c)) >>> 0;
        h = g; g = f; f = e; e = (d + T1) >>> 0;
        d = c; c = b; b = a; a = (T1 + T2) >>> 0;
      }
      H = H.map((v, i) => (v + [a, b, c, d, e, f, g, h][i]) >>> 0);
    }

    return H.map(w => w.toString(16).padStart(8, '0')).join('');
  }

  function buildFileUploadConfigRequest(csrf, cookieHeader) {
    return {
      url: 'https://agent.tongji.edu.cn/api/bypass/up?Action=GetConfig&Version=2022-01-01&Region=cn-north-1',
      headers: { 'x-csrf-token': csrf, 'Cookie': cookieHeader },
    };
  }

  function buildUploadRawRequest(fileBytes, csrf, cookieHeader) {
    const sha256Hex = computeSha256Hex(fileBytes);
    return {
      url: `https://agent.tongji.edu.cn/api/bypass/up?Action=UploadRaw&Version=2022-01-01&Region=cn-north-1&Id=${sha256Hex}&Expire=720h`,
      headers: { 'Content-Type': 'text/plain', 'x-csrf-token': csrf, 'Cookie': cookieHeader },
      body: fileBytes,
    };
  }

  function parseUploadResponse(responseJson) {
    let parsed;
    try {
      parsed = JSON.parse(responseJson);
    } catch {
      return null;
    }
    const r = parsed?.Result;
    if (!r || typeof r.Path !== 'string') return null;
    return { path: r.Path, size: r.Size, sha256: r.Sha256 };
  }

  function buildChatRequestWithFiles(message, appId, csrf, cookieHeader, files) {
    const fileEntries = (files || []).map(f => ({
      Path: f.path,
      Name: f.name,
      Size: f.size,
      Url: `https://agent.tongji.edu.cn/api/proxy/down?Action=Download&Version=2022-01-01&IsAnonymous=true&Path=${encodeURIComponent(f.path)}`,
    }));
    return {
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
        QueryExtends: { Files: fileEntries },
      }),
    };
  }

  root.TJproxyOffscreenUtils = Object.freeze({
    buildBridgeUrl,
    buildChatRequest,
    buildChatRequestWithFiles,
    buildFileUploadConfigRequest,
    buildUploadRawRequest,
    computeSha256Hex,
    parseSSELine,
    parseUploadResponse,
  });
})(globalThis);
