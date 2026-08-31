/**
 * Split RCA markdown into the text up to and including a given `## ` section
 * (matched case-insensitively), and everything after it (from the next `## `
 * heading onward). Used to splice a non-markdown component (e.g. the causal
 * DAG diagram) between two sections without re-parsing the whole report.
 *
 * If the heading isn't found, or it is the last section in the report,
 * `before` is the full text and `after` is empty — callers should render
 * their spliced content after `before` in that case (graceful degradation,
 * consistent with other heading-based parsers in this codebase).
 */
export function splitAfterHeading(markdown: string, headingTitle: string): { before: string; after: string } {
  if (!markdown) return { before: markdown, after: '' };

  const lines = markdown.split('\n');
  const target = headingTitle.trim().toLowerCase();
  let foundTarget = false;
  let splitIndex = -1;

  for (let i = 0; i < lines.length; i++) {
    const heading = lines[i].match(/^##\s+(.+)/);
    if (!heading) continue;
    const title = heading[1].trim().toLowerCase();
    if (foundTarget) {
      splitIndex = i;
      break;
    }
    if (title === target) {
      foundTarget = true;
    }
  }

  if (!foundTarget || splitIndex === -1) {
    return { before: markdown, after: '' };
  }

  return {
    before: lines.slice(0, splitIndex).join('\n').trimEnd(),
    after: lines.slice(splitIndex).join('\n').trimStart(),
  };
}

export interface ChronologyRow {
  timestamp: string;
  event: string;
  source: string;
}

/** Split collapsed `| row | | row |` sequences onto separate lines. */
export function expandCollapsedPipeRows(text: string): string {
  if (!text.includes('|')) return text;
  return text.replace(
    /\|\s*\|(?=\s*(?:[\-:*]|(?:\*\*)?\d{4}|(?:\*\*)?\d{2}[:/]|\*\*|\w))/g,
    '|\n|',
  );
}

function parsePipeCells(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith('|')) return null;
  const cells = trimmed.split('|').slice(1, -1).map(c => c.trim());
  if (cells.length === 0) return null;
  if (cells.every(c => /^[\s\-:]+$/.test(c))) return null;
  const c0 = (cells[0] || '').replace(/\*\*/g, '');
  const c1 = (cells[1] || '').replace(/\*\*/g, '');
  if (/^(?:timestamp|time\b|date)/i.test(c0) && /event|description|detail/i.test(c1)) return null;
  return cells;
}

/** Parse chronology body whether it is bullet list or pipe table (including single-line tables). */
export function parseChronologyRows(text: string): ChronologyRow[] {
  if (!text.trim()) return [];

  const normalized = expandCollapsedPipeRows(text.trim());
  const rows: ChronologyRow[] = [];

  for (const line of normalized.split('\n')) {
    const cells = parsePipeCells(line);
    if (!cells) continue;

    if (cells.length >= 3) {
      rows.push({
        timestamp: cells[0],
        event: cells[1],
        source: cells.slice(2).join(' | '),
      });
    } else if (cells.length === 2) {
      rows.push({ timestamp: cells[0], event: cells[1], source: '' });
    } else {
      rows.push({ timestamp: cells[0], event: '', source: '' });
    }
  }

  return rows;
}

export function chronologyRowsToMarkdown(rows: ChronologyRow[]): string {
  const header = '| Time (UTC) | Event | Source |';
  const separator = '| --- | --- | --- |';
  const body = rows.map(r =>
    `| ${r.timestamp.replace(/\|/g, '\\|')} | ${r.event.replace(/\|/g, '\\|')} | ${r.source.replace(/\|/g, '\\|')} |`,
  );
  return [header, separator, ...body].join('\n');
}

function normalizeChronologySectionBody(body: string): string {
  const trimmed = body.trim();
  if (!trimmed) return body;

  // Bullet chronology — leave unchanged.
  if (/^[-•]\s/m.test(trimmed) && !/^\|/m.test(trimmed.split('\n')[0]?.trim() ?? '')) {
    return body;
  }

  if (!trimmed.includes('|')) return body;

  const rows = parseChronologyRows(trimmed);
  if (rows.length > 0) {
    return `\n\n${chronologyRowsToMarkdown(rows)}\n`;
  }

  return `\n\n${expandCollapsedPipeRows(trimmed)}\n`;
}

/**
 * Normalize RCA markdown before ReactMarkdown — fixes Tier 2/3 chronology tables
 * that the LLM emits as a single collapsed pipe-delimited line.
 *
 * Handles heading variations across passes: ##/### level, "Chronology of Events",
 * "Chronology of Key Events", "Event Chronology", "Event Timeline", bold markers, etc.
 */
export function prepareRcaMarkdownForRender(rcaText: string): string {
  if (!rcaText) return rcaText;

  // Match any heading whose text contains "chronolog" or "timeline" (case-insensitive),
  // at any heading level (# through ######), with optional **bold** wrapping.
  const chronologyRe =
    /(#{1,6}\s+[^\n]*?(?:chronolog|timeline)[^\n]*\n)([\s\S]*?)(?=\n#{1,6}\s|\n---\s*\n|\n={4,}|$)/gi;

  let result = rcaText.replace(chronologyRe, (_match, heading: string, body: string) =>
    `${heading}${normalizeChronologySectionBody(body)}`,
  );

  // Fallback: if no heading-anchored section was found but the text still contains
  // collapsed pipe rows (e.g. the LLM used a completely unexpected heading),
  // expand them globally so remarkGfm can at least parse the table structure.
  if (result === rcaText && rcaText.includes('|')) {
    result = expandCollapsedPipeRows(rcaText);
  }

  return result;
}
