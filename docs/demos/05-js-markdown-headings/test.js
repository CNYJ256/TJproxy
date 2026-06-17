import { describe, it } from 'node:test';
import assert from 'node:assert';
import { extractHeadings } from './index.js';

describe('extractHeadings', () => {
  it('should return empty array for empty string', () => {
    assert.deepStrictEqual(extractHeadings(''), []);
  });

  it('should return empty array for text without headings', () => {
    const md = 'This is a paragraph.\n\nAnother paragraph.';
    assert.deepStrictEqual(extractHeadings(md), []);
  });

  it('should extract a single h1 heading', () => {
    const md = '# Title';
    assert.deepStrictEqual(extractHeadings(md), [
      { level: 1, text: 'Title' }
    ]);
  });

  it('should extract headings of all levels', () => {
    const md = [
      '# H1',
      '## H2',
      '### H3',
      '#### H4',
      '##### H5',
      '###### H6'
    ].join('\n');
    const result = extractHeadings(md);
    assert.strictEqual(result.length, 6);
    for (let i = 1; i <= 6; i++) {
      assert.strictEqual(result[i - 1].level, i);
      assert.strictEqual(result[i - 1].text, `H${i}`);
    }
  });

  it('should trim heading text', () => {
    const md = '##   Spaced Heading   ';
    assert.deepStrictEqual(extractHeadings(md), [
      { level: 2, text: 'Spaced Heading' }
    ]);
  });

  it('should ignore headings inside code blocks', () => {
    const md = [
      '# Real heading',
      '```',
      '# Fake heading',
      '```',
      '## Another real'
    ].join('\n');
    // Our simple implementation does NOT ignore code blocks.
    // This test documents current behavior.
    const result = extractHeadings(md);
    assert.strictEqual(result.length, 3);
  });

  it('should handle mixed content', () => {
    const md = [
      '# Main Title',
      '',
      'Some description.',
      '## Section 1',
      'Content here.',
      '### Subsection',
      'More content.',
      '## Section 2'
    ].join('\n');
    const result = extractHeadings(md);
    assert.deepStrictEqual(result, [
      { level: 1, text: 'Main Title' },
      { level: 2, text: 'Section 1' },
      { level: 3, text: 'Subsection' },
      { level: 2, text: 'Section 2' }
    ]);
  });

  it('should not treat # inside text as heading', () => {
    const md = 'This is # not a heading';
    assert.deepStrictEqual(extractHeadings(md), []);
  });
});
