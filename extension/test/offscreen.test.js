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
