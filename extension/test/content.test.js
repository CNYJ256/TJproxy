// test_content.js — Tests for AppID extraction from page URL
//
// Under test: the regex-based extraction in content.js
//   URL format: https://agent.tongji.edu.cn/product/llm/mall/application/{APPID}/chat
//   Regex: /application/([^/]+)/chat
//
import { describe, it, expect, vi } from 'vitest';

import '../lib/content-utils.js';

const { extractAppId, observeNavigation } = globalThis.TJproxyContentUtils;

// ---------------------------------------------------------------------------
// Happy path
// ---------------------------------------------------------------------------
describe('AppID extraction — happy path', () => {
  it('extracts AppID from a standard chat URL', () => {
    const url = 'https://agent.tongji.edu.cn/product/llm/mall/application/d7e4f2a1b3c4/chat';
    expect(extractAppId(url)).toBe('d7e4f2a1b3c4');
  });

  it('extracts short AppID (single character)', () => {
    const url = 'https://agent.tongji.edu.cn/product/llm/mall/application/a/chat';
    expect(extractAppId(url)).toBe('a');
  });

  it('extracts long UUID-style AppID', () => {
    const id = '550e8400-e29b-41d4-a716-446655440000';
    const url = `https://agent.tongji.edu.cn/product/llm/mall/application/${id}/chat`;
    expect(extractAppId(url)).toBe(id);
  });

  it('extracts AppID with trailing query params on the URL', () => {
    // The regex is anchored to /chat so query params after /chat will not break it.
    const url = 'https://agent.tongji.edu.cn/product/llm/mall/application/abc123/chat?foo=bar';
    expect(extractAppId(url)).toBe('abc123');
  });

  it('extracts AppID with a hash fragment', () => {
    const url = 'https://agent.tongji.edu.cn/product/llm/mall/application/abc123/chat#section';
    expect(extractAppId(url)).toBe('abc123');
  });
});

// ---------------------------------------------------------------------------
// URLs that should NOT match
// ---------------------------------------------------------------------------
describe('AppID extraction — non-matching URLs', () => {
  it('returns null for a non-chat application page (no /chat suffix)', () => {
    const url = 'https://agent.tongji.edu.cn/product/llm/mall/application/d7e4f2a1b3c4/detail';
    expect(extractAppId(url)).toBeNull();
  });

  it('returns null for the application mall listing page', () => {
    const url = 'https://agent.tongji.edu.cn/product/llm/mall/application';
    expect(extractAppId(url)).toBeNull();
  });

  it('still extracts AppID from /application/xxx/chat/history (regex not anchored)', () => {
    // The regex /application/([^/]+)/chat matches anywhere in the URL.
    // /application/abc/chat/history → still finds "abc" since /chat follows the AppID.
    const url = 'https://agent.tongji.edu.cn/product/llm/mall/application/abc/chat/history';
    expect(extractAppId(url)).toBe('abc');
  });

  it('returns null for the product root page', () => {
    const url = 'https://agent.tongji.edu.cn/product/llm/mall';
    expect(extractAppId(url)).toBeNull();
  });

  it('returns null for completely unrelated URLs', () => {
    const urls = [
      'https://example.com/',
      'https://agent.tongji.edu.cn/',
      'https://agent.tongji.edu.cn/product/llm',
      'https://agent.tongji.edu.cn/product/llm/mall/application//chat',  // empty AppID
    ];
    for (const url of urls) {
      expect(extractAppId(url), `URL: ${url}`).toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------
describe('AppID extraction — edge cases', () => {
  it('extracts AppID from URL with host:port', () => {
    const url = 'https://agent.tongji.edu.cn:443/product/llm/mall/application/xyz/chat';
    expect(extractAppId(url)).toBe('xyz');
  });

  it('extracts AppID when URL contains encoded characters in other parts', () => {
    const url = 'https://agent.tongji.edu.cn/product/llm/mall/application/test%20id/chat';
    // The AppID segment contains "%20" — this is still valid as a captured group.
    expect(extractAppId(url)).toBe('test%20id');
  });

  it('does not treat application as part of the AppID', () => {
    const url = 'https://agent.tongji.edu.cn/product/llm/mall/application/real-app-id/chat';
    expect(extractAppId(url)).toBe('real-app-id');
  });
});

describe('SPA navigation observation', () => {
  it('notifies once when the page URL changes', () => {
    const listeners = new Map();
    let poll;
    const target = {
      location: { href: 'https://agent.tongji.edu.cn/application/app-a/chat' },
      addEventListener: vi.fn((name, callback) => listeners.set(name, callback)),
      removeEventListener: vi.fn((name) => listeners.delete(name)),
      setInterval: vi.fn((callback) => {
        poll = callback;
        return 42;
      }),
      clearInterval: vi.fn(),
    };
    const notify = vi.fn();

    const stop = observeNavigation(target, notify);
    target.location.href = 'https://agent.tongji.edu.cn/application/app-b/chat';
    poll();
    poll();

    expect(notify).toHaveBeenCalledTimes(1);
    stop();
    expect(target.clearInterval).toHaveBeenCalledWith(42);
  });
});
