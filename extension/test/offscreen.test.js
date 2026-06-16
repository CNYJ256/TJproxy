// test_offscreen.js — Tests for SSE parsing in offscreen.js
//
// Under test: parseSSELine(buffer, line) → { tokens: string[], isDone: boolean }
//
// Tongji SSE format:
//   event: text
//   data: {"event":"message","answer":"你好","task_id":"..."}
//
// Events of interest:
//   "message"      → accumulate answer text as tokens
//   "message_end"  → signal completion (isDone = true)
//   "message_start"→ ignore
//   "think_message"→ ignore (should not pollute answer)
//
// The function processes one line at a time and maintains internal state
// between calls (event type from the previous "event:" line).

import { describe, it, expect } from 'vitest';

import '../lib/offscreen-utils.js';

const {
  buildBridgeUrl,
  buildChatRequest,
  parseSSELine,
} = globalThis.TJproxyOffscreenUtils;

function freshState() {
  return { lastEvent: null };
}

// ---------------------------------------------------------------------------
// Tests: basic SSE parsing
// ---------------------------------------------------------------------------
describe('parseSSELine — basic parsing', () => {
  it('extracts answer token from a "message" event data line', () => {
    const state = freshState();
    const result = parseSSELine(state, 'data: {"event":"message","answer":"你好"}');
    expect(result.tokens).toEqual(['你好']);
    expect(result.isDone).toBe(false);
  });

  it('returns empty tokens for an empty line', () => {
    const state = freshState();
    const result = parseSSELine(state, '');
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
  });

  it('returns empty tokens for a whitespace-only line', () => {
    const state = freshState();
    const result = parseSSELine(state, '   ');
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
  });

  it('returns empty tokens and isDone=false for a message_start event', () => {
    const state = freshState();
    const result = parseSSELine(state, 'data: {"event":"message_start","task_id":"t1"}');
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Tests: multi-line event/data pairs (event: then data:)
// ---------------------------------------------------------------------------
describe('parseSSELine — event/data pairing', () => {
  it('uses the preceding event: line type for a data: line without "event" field', () => {
    const state = freshState();

    // First line: event declaration
    let result = parseSSELine(state, 'event: message');
    expect(result.tokens).toEqual([]);

    // Second line: data without explicit event field → uses state.lastEvent
    result = parseSSELine(state, 'data: {"answer":"你好","task_id":"abc"}');
    expect(result.tokens).toEqual(['你好']);
    expect(result.isDone).toBe(false);
  });

  it('handles a full message_start → message → message_end sequence', () => {
    const state = freshState();

    // message_start — ignored
    let r = parseSSELine(state, 'event: text');
    expect(r.tokens).toEqual([]);

    r = parseSSELine(state, 'data: {"event":"message_start","task_id":"t123"}');
    expect(r.tokens).toEqual([]);
    expect(r.isDone).toBe(false);

    // message — collect token
    r = parseSSELine(state, 'event: text');
    r = parseSSELine(state, 'data: {"event":"message","answer":"Hello","task_id":"t123"}');
    expect(r.tokens).toEqual(['Hello']);

    // message_end — signal completion
    r = parseSSELine(state, 'event: text');
    r = parseSSELine(state, 'data: {"event":"message_end","task_id":"t123"}');
    expect(r.isDone).toBe(true);
    expect(r.tokens).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Tests: think_message is ignored
// ---------------------------------------------------------------------------
describe('parseSSELine — think_message is ignored', () => {
  it('does NOT collect answer from a think_message event', () => {
    const state = freshState();
    const result = parseSSELine(
      state,
      'data: {"event":"think_message","answer":"thinking...","task_id":"t1"}'
    );
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
    expect(result.hasActivity).toBe(true);
  });

  it('think_message does NOT leak into subsequent message tokens', () => {
    const state = freshState();

    // think_message line
    parseSSELine(state, 'data: {"event":"think_message","answer":"internal reasoning"}');

    // real message line
    const result = parseSSELine(state, 'data: {"event":"message","answer":"actual reply"}');
    expect(result.tokens).toEqual(['actual reply']);
    expect(result.isDone).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Tests: edge cases
// ---------------------------------------------------------------------------
describe('parseSSELine — edge cases', () => {
  it('handles data line with empty answer string', () => {
    const state = freshState();
    // answer is empty string — typeof is 'string', so it should be collected
    const result = parseSSELine(state, 'data: {"event":"message","answer":""}');
    expect(result.tokens).toEqual(['']);
  });

  it('handles data line where answer is missing', () => {
    const state = freshState();
    const result = parseSSELine(state, 'data: {"event":"message"}');
    // No answer field → nothing to collect
    expect(result.tokens).toEqual([]);
  });

  it('ignores malformed JSON gracefully', () => {
    const state = freshState();
    const result = parseSSELine(state, 'data: {not valid json');
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
    expect(result.hasActivity).toBe(false);
  });

  it('ignores comment lines (starting with colon)', () => {
    const state = freshState();
    const result = parseSSELine(state, ':ok\n');
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
  });

  it('ignores "id:" and "retry:" lines', () => {
    const state = freshState();

    const r1 = parseSSELine(state, 'id: 1');
    expect(r1.tokens).toEqual([]);

    const r2 = parseSSELine(state, 'retry: 3000');
    expect(r2.tokens).toEqual([]);
  });

  it('data line with only whitespace after "data:" returns nothing', () => {
    const state = freshState();
    const result = parseSSELine(state, 'data:   ');
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
  });

  it('data line with [DONE] special marker is not mistaken for done', () => {
    // [DONE] is not part of the Tongji protocol — only message_end signals done.
    // This test ensures we do NOT falsely interpret [DONE].
    const state = freshState();
    const result = parseSSELine(state, 'data: [DONE]');
    // JSON.parse fails → gracefully ignored
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Tests: state isolation
// ---------------------------------------------------------------------------
describe('parseSSELine — state isolation', () => {
  it('fresh state has lastEvent null', () => {
    const state = freshState();
    expect(state.lastEvent).toBeNull();
  });

  it('event type from event: line persists only within same SSE block', () => {
    const state = freshState();

    // Set event type
    parseSSELine(state, 'event: message');
    expect(state.lastEvent).toBe('message');

    // Overwrite with a new event type
    parseSSELine(state, 'event: message_end');
    expect(state.lastEvent).toBe('message_end');
  });
});

// ---------------------------------------------------------------------------
// Tests: double data: prefix (actual Tongji API format)
// ---------------------------------------------------------------------------
describe('parseSSELine — double data: prefix', () => {
  it('handles data:data: prefix from actual Tongji API', () => {
    const state = freshState();
    const result = parseSSELine(state, 'data:data: {"event":"message","answer":"你好"}');
    expect(result.tokens).toEqual(['你好']);
    expect(result.isDone).toBe(false);
  });

  it('handles message_start with double data:', () => {
    const state = freshState();
    const result = parseSSELine(state, 'data:data: {"event":"message_start","task_id":"t1"}');
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
  });

  it('handles think_message with double data:', () => {
    const state = freshState();
    const result = parseSSELine(state, 'data:data: {"event":"think_message","answer":"thinking"}');
    expect(result.tokens).toEqual([]);
    expect(result.isDone).toBe(false);
  });
});

describe('buildBridgeUrl', () => {
  it('adds the fixed bridge path and encoded token', () => {
    expect(buildBridgeUrl('ws://localhost:8765', 'a token/+')).toBe(
      'ws://localhost:8765/bridge?token=a%20token%2F%2B'
    );
  });
});

describe('buildChatRequest', () => {
  it('preserves the initial working manual Cookie header behavior', () => {
    const options = buildChatRequest('hello', 'app-1', 'csrf-value', 'tenant=abc');

    expect(options.headers).toEqual({
      'Content-Type': 'application/json',
      'x-csrf-token': 'csrf-value',
      'Cookie': 'tenant=abc',
    });
    expect(JSON.parse(options.body)).toMatchObject({ Query: 'hello', AppID: 'app-1' });
  });
});

// ---------------------------------------------------------------------------
// Tests: file upload — buildFileUploadConfigRequest
// ---------------------------------------------------------------------------
describe('buildFileUploadConfigRequest', () => {
  const {
    buildFileUploadConfigRequest,
  } = globalThis.TJproxyOffscreenUtils;

  it('builds a GET request for the upload config endpoint', () => {
    const result = buildFileUploadConfigRequest('token-csrf', 'session=abc');

    expect(result.url).toBe(
      'https://agent.tongji.edu.cn/api/bypass/up?Action=GetConfig&Version=2022-01-01&Region=cn-north-1'
    );
    expect(result.headers).toEqual({
      'x-csrf-token': 'token-csrf',
      'Cookie': 'session=abc',
    });
  });

  it('works with empty cookie header', () => {
    const result = buildFileUploadConfigRequest('csrf', '');

    expect(result.url).toContain('Action=GetConfig');
    expect(result.headers).toEqual({
      'x-csrf-token': 'csrf',
      'Cookie': '',
    });
  });
});

// ---------------------------------------------------------------------------
// Tests: file upload — computeSha256Hex
// ---------------------------------------------------------------------------
describe('computeSha256Hex', () => {
  const {
    computeSha256Hex,
  } = globalThis.TJproxyOffscreenUtils;

  it('returns the SHA-256 hex digest for known input', () => {
    // "Hello" encoded as bytes: [0x48, 0x65, 0x6c, 0x6c, 0x6f]
    const input = new Uint8Array([0x48, 0x65, 0x6c, 0x6c, 0x6f]);

    // Expected: SHA-256 of "Hello" —
    // echo -n 'Hello' | sha256sum
    // 185f8db32271fe25f561a6fc938b2e264306ec304eda518007d1764826381969
    const result = computeSha256Hex(input);

    expect(result).toBe(
      '185f8db32271fe25f561a6fc938b2e264306ec304eda518007d1764826381969'
    );
  });

  it('returns a 64-character hex string for any input', () => {
    const input = new Uint8Array([0x00, 0x01, 0x02]);
    const result = computeSha256Hex(input);

    expect(result).toHaveLength(64);
    expect(result).toMatch(/^[0-9a-f]{64}$/);
  });

  it('returns the correct digest for an empty buffer', () => {
    // SHA-256 of empty string: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    const result = computeSha256Hex(new Uint8Array(0));

    expect(result).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    );
  });
});

// ---------------------------------------------------------------------------
// Tests: file upload — buildUploadRawRequest
// ---------------------------------------------------------------------------
describe('buildUploadRawRequest', () => {
  const {
    buildUploadRawRequest,
  } = globalThis.TJproxyOffscreenUtils;

  it('builds a POST request with correct URL, headers, and body', () => {
    const fileBytes = new Uint8Array([0x48, 0x65, 0x6c, 0x6c, 0x6f]); // "Hello"
    const sha256Hex = '185f8db32271fe25f561a6fc938b2e264306ec304eda518007d1764826381969';

    const result = buildUploadRawRequest(fileBytes, 'csrf-token', 'cookie=val');

    expect(result.url).toBe(
      `https://agent.tongji.edu.cn/api/bypass/up?Action=UploadRaw&Version=2022-01-01&Region=cn-north-1&Id=${sha256Hex}&Expire=720h`
    );
    expect(result.headers).toEqual({
      'Content-Type': 'text/plain',
      'x-csrf-token': 'csrf-token',
      'Cookie': 'cookie=val',
    });
    expect(result.body).toBe(fileBytes);
  });

  it('computes SHA-256 from the file bytes for the Id parameter', () => {
    const fileBytes = new Uint8Array([0x41]); // "A"
    const expectedSha = '559aead08264d5795d3909718cdd05abd49572e84fe55590eef31a88a08fdffd';

    const result = buildUploadRawRequest(fileBytes, 'csrf', 'c=1');

    expect(result.url).toContain(`Id=${expectedSha}`);
  });
});

// ---------------------------------------------------------------------------
// Tests: file upload — parseUploadResponse
// ---------------------------------------------------------------------------
describe('parseUploadResponse', () => {
  const {
    parseUploadResponse,
  } = globalThis.TJproxyOffscreenUtils;

  it('extracts Path, Size, and Sha256 from a successful upload response', () => {
    const body = JSON.stringify({
      Result: {
        Path: 'upload/full/xx/yy/hash',
        Size: 11,
        Sha256: 'abc',
      },
    });

    const result = parseUploadResponse(body);

    expect(result).toEqual({
      path: 'upload/full/xx/yy/hash',
      size: 11,
      sha256: 'abc',
    });
  });

  it('handles response with extra fields like ShortLink and PresignKey', () => {
    const body = JSON.stringify({
      Result: {
        Path: 'upload/full/a5/91/sha256hexdigest',
        Size: 42,
        Sha256: 'sha256hexdigest',
        ShortLink: 'https://s.tongji.edu.cn/abc',
        PresignKey: 'presign-key-value',
      },
    });

    const result = parseUploadResponse(body);

    expect(result).toEqual({
      path: 'upload/full/a5/91/sha256hexdigest',
      size: 42,
      sha256: 'sha256hexdigest',
    });
  });

  it('returns null for a response without Result', () => {
    const body = JSON.stringify({ Error: 'something went wrong' });

    const result = parseUploadResponse(body);

    expect(result).toBeNull();
  });

  it('returns null for non-JSON input', () => {
    const result = parseUploadResponse('not json at all');

    expect(result).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Tests: file upload — buildChatRequestWithFiles
// ---------------------------------------------------------------------------
describe('buildChatRequestWithFiles', () => {
  const {
    buildChatRequestWithFiles,
  } = globalThis.TJproxyOffscreenUtils;

  it('builds a chat request with files in QueryExtends.Files', () => {
    const files = [
      { path: 'upload/full/a5/91/abc', name: 'hello.txt', size: 11 },
    ];

    const result = buildChatRequestWithFiles(
      'Describe this file',
      'd7eb286lvndfvk7hrsd0',
      'csrf-value',
      'tenant=abc',
      files
    );

    expect(result.method).toBe('POST');
    expect(result.headers).toEqual({
      'Content-Type': 'application/json',
      'x-csrf-token': 'csrf-value',
      'Cookie': 'tenant=abc',
    });

    const body = JSON.parse(result.body);
    expect(body.Query).toBe('Describe this file');
    expect(body.AppID).toBe('d7eb286lvndfvk7hrsd0');
    expect(body.InputData).toEqual([]);
    expect(body.QueryExtends.Files).toBeInstanceOf(Array);
    expect(body.QueryExtends.Files).toHaveLength(1);
  });

  it('auto-generates the download URL from each file path', () => {
    const files = [
      { path: 'upload/full/a5/91/sha256hex', name: 'hello.txt', size: 11 },
    ];

    const result = buildChatRequestWithFiles(
      'hi',
      'app-1',
      'csrf',
      'cookie',
      files
    );

    const body = JSON.parse(result.body);
    const fileEntry = body.QueryExtends.Files[0];

    expect(fileEntry.Path).toBe('upload/full/a5/91/sha256hex');
    expect(fileEntry.Name).toBe('hello.txt');
    expect(fileEntry.Size).toBe(11);

    // The download URL should encode the path and include the required query params
    const encodedPath = encodeURIComponent('upload/full/a5/91/sha256hex');
    expect(fileEntry.Url).toBe(
      `https://agent.tongji.edu.cn/api/proxy/down?Action=Download&Version=2022-01-01&IsAnonymous=true&Path=${encodedPath}`
    );
  });

  it('handles multiple files in a single request', () => {
    const files = [
      { path: 'upload/full/a/b/file1', name: 'a.txt', size: 10 },
      { path: 'upload/full/c/d/file2', name: 'b.txt', size: 20 },
    ];

    const result = buildChatRequestWithFiles(
      'Compare these files',
      'app-2',
      'csrf',
      'cookie',
      files
    );

    const body = JSON.parse(result.body);
    expect(body.QueryExtends.Files).toHaveLength(2);

    expect(body.QueryExtends.Files[0].Name).toBe('a.txt');
    expect(body.QueryExtends.Files[0].Path).toBe('upload/full/a/b/file1');
    expect(body.QueryExtends.Files[0].Size).toBe(10);
    expect(body.QueryExtends.Files[0].Url).toContain('Path=');

    expect(body.QueryExtends.Files[1].Name).toBe('b.txt');
    expect(body.QueryExtends.Files[1].Size).toBe(20);
  });

  it('handles empty files array (no attachments)', () => {
    const result = buildChatRequestWithFiles(
      'plain message',
      'app-3',
      'csrf',
      'cookie',
      []
    );

    const body = JSON.parse(result.body);
    expect(body.QueryExtends.Files).toEqual([]);
  });
});
