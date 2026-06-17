/**
 * Extract all headings from a markdown string.
 * Each heading is returned as { level: number, text: string }.
 * Supports ATX headings (# to ######).
 *
 * @param {string} markdown - The markdown text.
 * @returns {Array<{level: number, text: string}>}
 */
export function extractHeadings(markdown) {
  const headings = [];
  const lines = markdown.split('\n');
  const headingRegex = /^(#{1,6})\s+(.+)$/;

  for (const line of lines) {
    const match = line.match(headingRegex);
    if (match) {
      headings.push({
        level: match[1].length,
        text: match[2].trim()
      });
    }
  }

  return headings;
}
