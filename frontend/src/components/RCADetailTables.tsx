import { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertCircle, BarChart3, FileSearch, Zap, HardDrive, ArrowRight, Wrench } from 'lucide-react';

// ── Types ────────────────────────────────────────────────────────────

interface ErrorPattern {
  pattern: string;
  source: string;
  classification: string;
  significance: string;
}

interface CoverageFile {
  file: string;
  type: string;
  result: string;
}

interface CoverageRound {
  title: string;
  note: string;
  files: CoverageFile[];
}

interface CostRow {
  phase: string;
  inputTokens: string;
  outputTokens: string;
  cost: string;
  indent?: boolean;
}

interface CompressionFile {
  file: string;
  type: string;
  originalBytes: string;
  payloadBytes: string;
  ratio: string;
}

interface CompressionData {
  totalInput: string;
  totalPayload: string;
  overallRatio: string;
  files: CompressionFile[];
}

interface Resolution {
  num: string;
  issue: string;
  priority: string;
  shortTerm: string;
  longTerm: string;
  validation: string;
}

interface ParsedTables {
  errorPatterns: ErrorPattern[];
  resolutions: Resolution[];
  resolutionsFallback: string;
  coverageRounds: CoverageRound[];
  coverageMissing: string[];
  coverageTiers: string[];
  keyLimitation: string;
  costRows: CostRow[];
  costTotal: string;
  compression: CompressionData | null;
}

// ── Priority ranking (shared by Remediation) ───────

const PRIORITY_RANK: Record<string, number> = {
  critical: 0, immediate: 0,
  high: 1,
  medium: 2,
  low: 3,
};

function priorityRank(priority: string): number {
  const pl = priority.toLowerCase();
  for (const [key, rank] of Object.entries(PRIORITY_RANK)) {
    if (pl.includes(key)) return rank;
  }
  return 99;
}

// ── Parsers ──────────────────────────────────────────────────────────

function parseMarkdownTable(text: string): string[][] {
  const rows: string[][] = [];
  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!t.startsWith('|')) continue;
    const cells = t.split('|').slice(1, -1).map(c => c.trim());
    if (cells.some(c => /^[\s\-:]+$/.test(c))) continue;
    rows.push(cells);
  }
  return rows;
}

function parseErrorPatterns(section: string): ErrorPattern[] {
  const rows = parseMarkdownTable(section);
  if (rows.length < 2) return [];
  return rows.slice(1).map(r => ({
    pattern: (r[0] || '').replace(/\*\*/g, ''),
    source: (r[1] || '').replace(/`/g, ''),
    classification: (r[2] || ''),
    significance: (r[3] || '').replace(/\*\*/g, ''),
  }));
}

/**
 * Canonicalise remediation prose so both pass formats render identically.
 * Converts plain numbered items and un-bolded labels to the ### / **bold** style
 * that the High-pass agentic prompt produces.
 */
function normalizeRemediationProse(text: string): string {
  // "1. [SEVERITY] Title"  →  "### 1. [SEVERITY] Title"
  // Only at the true start of a line and only when not already a heading.
  let out = text.replace(/^(?!#)(\d+\.\s+\[)/gm, '### $1');

  // Plain "Steps:" / "Validation:" / "Note:" on their own line → bold variant
  out = out.replace(/^(\s*)(Steps|Validation|Note):\s*$/gm, '$1**$2:**');

  return out;
}

function parseResolutions(section: string): { items: Resolution[]; fallback: string } {
  let items: Resolution[] = [];

  // Strip fenced code blocks before table detection. Bash pipe operators
  // (e.g. `| grep -i "foo\|bar"`) start with `|` and would otherwise be
  // mistakenly parsed as markdown table rows.
  const sectionNoCode = section.replace(/`{3}[\s\S]*?`{3}/g, '');

  // Only accept a pipe-table if the header row has ≥4 columns AND contains
  // recognisable column keywords — guards against single-pipe shell fragments.
  const rows = parseMarkdownTable(sectionNoCode);
  const isRealTable =
    rows.length >= 2 &&
    rows[0].length >= 4 &&
    rows[0].some(cell => /issue|action|priority|resolution|#/i.test(cell));

  if (isRealTable) {
    items = rows.slice(1).map(r => ({
      num: (r[0] || '').replace(/\*\*/g, '').trim(),
      issue: (r[1] || '').replace(/\*\*/g, '').trim(),
      priority: (r[2] || '').replace(/\*\*/g, '').trim(),
      shortTerm: (r[3] || '').replace(/\*\*/g, '').trim(),
      longTerm: (r[4] || '').replace(/\*\*/g, '').trim(),
      validation: (r[5] || '').replace(/\*\*/g, '').trim(),
    }));
  }

  // Second path: numbered items with labelled bullet fields
  // (e.g. `- Short-term: ...`, `- Validation: ...`)
  if (items.length === 0) {
    const numbered = section.split(/(?=^\s*\d+\.\s)/m).filter(Boolean);
    for (const block of numbered) {
      const hdr = block.match(/^\s*(\d+)\.\s+(.*)/);
      if (!hdr) continue;
      const num = hdr[1];
      const titleLine = hdr[2].replace(/\*\*/g, '').trim();

      const priorityMatch = titleLine.match(/\[(.*?)\]/);
      const priority = priorityMatch ? priorityMatch[1] : '';
      const issue = titleLine.replace(/\[.*?\]\s*/, '').replace(/[—–-]\s*$/, '').trim();

      const lines = block.split('\n').slice(1);
      let shortTerm = '';
      let longTerm = '';
      let validation = '';

      for (const line of lines) {
        const t = line.trim().replace(/\*\*/g, '');
        if (/^[-•]\s*short[- ]?term/i.test(t)) {
          shortTerm = t.replace(/^[-•]\s*short[- ]?term\s*[:：]\s*/i, '').trim();
        } else if (/^[-•]\s*long[- ]?term/i.test(t)) {
          longTerm = t.replace(/^[-•]\s*long[- ]?term\s*[:：]\s*/i, '').trim();
        } else if (/^[-•]\s*validation/i.test(t)) {
          validation = t.replace(/^[-•]\s*validation\s*[:：]\s*/i, '').trim();
        }
      }

      items.push({ num, issue, priority, shortTerm, longTerm, validation });
    }

    // If none of the items have action details it means this is the prose
    // format (Steps: / Validation: sections with code blocks) — fall through
    // to the ReactMarkdown fallback which renders it correctly.
    const hasActionDetails = items.some(
      item => item.shortTerm || item.longTerm || item.validation
    );
    if (!hasActionDetails) items = [];
  }

  if (items.length === 0) return { items: [], fallback: normalizeRemediationProse(section.trim()) };

  // Sort by priority: CRITICAL/IMMEDIATE > HIGH > MEDIUM > LOW > untagged
  items.sort((a, b) => priorityRank(a.priority) - priorityRank(b.priority));
  // Re-number after sorting
  items = items.map((item, i) => ({ ...item, num: String(i + 1) }));

  return { items, fallback: '' };
}

function parseCoverageSection(section: string): {
  rounds: CoverageRound[];
  missing: string[];
  tiers: string[];
  keyLimitation: string;
} {
  const rounds: CoverageRound[] = [];
  const missing: string[] = [];
  const tiers: string[] = [];
  let keyLimitation = '';

  // Strip any trailing ====... cost block that leaked into this section
  const cleaned = section.replace(/={4,}[\s\S]*$/, '').trim();

  const blocks = cleaned.split(/\n(?=###\s)/);
  for (const block of blocks) {
    const heading = block.match(/^###\s+(.+)/);
    if (!heading) continue;
    const title = heading[1].replace(/\*\*/g, '').trim();
    const bodyLines = block.split('\n').slice(1);

    if (/round\s+\d/i.test(title)) {
      const rows = parseMarkdownTable(block);
      const note = bodyLines
        .filter(l => {
          const t = l.trim();
          return t.startsWith('**') || t.startsWith('⚠') || /^Round\s+\d/i.test(t);
        })
        .map(l => l.replace(/\*\*/g, '').trim())
        .filter(Boolean)
        .join(' ')
        .trim();
      rounds.push({
        title,
        note,
        files: rows.length > 1
          ? rows.slice(1).map(r => ({
              file: (r[0] || '').replace(/`/g, ''),
              type: r[1] || '',
              result: (r[2] || '').replace(/\*\*/g, ''),
            }))
          : [],
      });
    } else if (/not\s+found|absent|missing/i.test(title)) {
      for (const line of bodyLines) {
        const bullet = line.match(/^\s*[-•]\s+(.+)/);
        if (bullet) {
          missing.push(
            bullet[1]
              .replace(/`/g, '')
              .replace(/\*\*/g, '')
              .replace(/\s*\(.*$/, '')
              .trim()
          );
        }
      }
    } else if (/priority\s+tiers/i.test(title)) {
      for (const line of bodyLines) {
        const bullet = line.match(/^\s*[-•]\s+(.+)/);
        if (bullet) tiers.push(bullet[1].replace(/\*\*/g, '').trim());
      }
    } else if (/key\s+limitation/i.test(title)) {
      keyLimitation = bodyLines
        .map(l => l.trim())
        .filter(l => l && !l.startsWith('='))
        .join(' ')
        .replace(/\*\*/g, '')
        .replace(/`/g, '')
        .trim();
    }
  }
  return { rounds, missing, tiers, keyLimitation };
}

function parseCostSection(text: string): { rows: CostRow[]; total: string } {
  const rows: CostRow[] = [];
  let total = '';

  for (const line of text.split('\n')) {
    const t = line.trimEnd();
    if (!t.trim()) continue;

    const totalMatch = t.match(/Total\s+\$\s*([\d.,]+)/i);
    if (totalMatch) {
      total = `$${totalMatch[1].trim()}`;
      continue;
    }

    const phaseMatch = t.match(
      /^\s{2,}(\d+\.\s+.+?)\s{2,}([\d,]+|—)\s+([\d,]+|—)\s+\$?\s*([\d.,]+|—)/
    );
    if (phaseMatch) {
      rows.push({
        phase: phaseMatch[1].trim(),
        inputTokens: phaseMatch[2].trim(),
        outputTokens: phaseMatch[3].trim(),
        cost: `$${phaseMatch[4].trim()}`,
      });
      continue;
    }

    const iterMatch = t.match(/^\s+\((\d+\s+iterations)\)/);
    if (iterMatch && rows.length > 0) {
      rows[rows.length - 1].phase += ` (${iterMatch[1]})`;
      continue;
    }

    const subMatch = t.match(
      /^\s{2,}(\d+\.\s+.+?)\s{2,}([\d,]+|—)\s+([\d,]+|—)\s+\$?\s*([\d.,]+|—)/
    );
    if (!subMatch) {
      const simplePhase = t.match(
        /^\s{2,}(\d+\.\s+.+?)\s{2,}(—)\s+(—)\s+(—|[\$\d.,]+)/
      );
      if (simplePhase) {
        rows.push({
          phase: simplePhase[1].trim(),
          inputTokens: '—',
          outputTokens: '—',
          cost: simplePhase[4].trim() === '—' ? '—' : `$${simplePhase[4].replace('$', '').trim()}`,
        });
      }
    }
  }
  return { rows, total };
}

function parseCompressionSection(text: string): CompressionData | null {
  let totalInput = '';
  let totalPayload = '';
  let overallRatio = '';
  const files: CompressionFile[] = [];

  for (const line of text.split('\n')) {
    const t = line.trim();

    const summaryMatch = t.match(
      /Total input.*?:\s*([\d,]+)\s*bytes.*?Payload.*?:\s*([\d,]+)\s*bytes/i
    );
    if (summaryMatch) {
      totalInput = summaryMatch[1];
      totalPayload = summaryMatch[2];
    }

    const ratioMatch = t.match(/Overall compression ratio:\s*([\d.,]+x)/i);
    if (ratioMatch) overallRatio = ratioMatch[1];

    const fileMatch = t.match(
      /^\s{2,}(\S+)\s+(yaml|log|json)\s+([\d,]+)\s+([\d,]+)\s+([\d.,]+x)/i
    );
    if (fileMatch) {
      files.push({
        file: fileMatch[1],
        type: fileMatch[2],
        originalBytes: fileMatch[3],
        payloadBytes: fileMatch[4],
        ratio: fileMatch[5],
      });
    }
  }

  if (!totalInput && files.length === 0) return null;
  return { totalInput, totalPayload, overallRatio, files };
}

function parseTables(rcaText: string): ParsedTables | null {
  if (!rcaText) return null;

  // IMPORTANT: strip the plain-text cost/compression footer BEFORE building sections.
  // The footer starts with ====...LLM API TOKEN and is NOT a ## heading.
  const rcaForSections = rcaText.replace(/\n*={4,}[\t ]*\n[\t ]*LLM API TOKEN[\s\S]*$/, '');

  // Extract markdown sections (## headings) from the cost-table-free text
  const sections: Record<string, string> = {};
  const lines = rcaForSections.split('\n');
  let current = '';
  let buffer: string[] = [];
  for (const line of lines) {
    const heading = line.match(/^##\s+(.+)/);
    if (heading) {
      if (current) sections[current] = buffer.join('\n').trim();
      current = heading[1].trim();
      buffer = [];
    } else {
      buffer.push(line);
    }
  }
  if (current) sections[current] = buffer.join('\n').trim();

  // Extract the plain-text cost/compression block from the ORIGINAL text
  const costBlockMatch = rcaText.match(
    /={3,}\s*\n\s*LLM API TOKEN & COST SUMMARY.*?\n([\s\S]*?)(?:={3,}\s*$|$)/m
  );
  const costBlock = costBlockMatch ? costBlockMatch[1] : '';

  const compressionMatch = costBlock.match(
    /COMPRESSION RATIO[\s\S]*/i
  );
  const compressionBlock = compressionMatch ? compressionMatch[0] : '';
  const costOnly = compressionMatch
    ? costBlock.slice(0, compressionMatch.index)
    : costBlock;

  const errorPatterns = parseErrorPatterns(sections['Aggregated Error Patterns'] || '');

  const resolutionsRaw = Object.entries(sections)
    .find(([k]) => {
      const norm = k.toLowerCase().replace(/[^a-z]/g, '');
      return norm.startsWith('resolution') || norm.startsWith('remediation');
    })?.[1] || '';
  const resolutionsParsed = parseResolutions(resolutionsRaw);

  const coverage = parseCoverageSection(sections['Analysis Coverage'] || '');

  const cost = parseCostSection(costOnly);

  const compression = parseCompressionSection(compressionBlock);

  if (
    errorPatterns.length === 0 &&
    resolutionsParsed.items.length === 0 &&
    !resolutionsParsed.fallback &&
    coverage.rounds.length === 0 &&
    cost.rows.length === 0 &&
    !compression
  ) {
    return null;
  }

  return {
    errorPatterns,
    resolutions: resolutionsParsed.items,
    resolutionsFallback: resolutionsParsed.fallback,
    coverageRounds: coverage.rounds,
    coverageMissing: coverage.missing,
    coverageTiers: coverage.tiers,
    keyLimitation: coverage.keyLimitation,
    costRows: cost.rows,
    costTotal: cost.total,
    compression,
  };
}

// ── Styled table wrapper ─────────────────────────────────────────────

function SectionCard({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      <div className="flex items-center gap-2 px-6 py-4 border-b border-slate-100 bg-slate-50/60">
        {icon}
        <h2 className="text-[15px] font-semibold text-slate-800">{title}</h2>
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function StyledTable({
  headers,
  rows,
  highlightCol,
}: {
  headers: string[];
  rows: React.ReactNode[][];
  highlightCol?: number;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="w-full text-left text-[12.5px]">
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200">
            {headers.map((h, i) => (
              <th
                key={i}
                className="px-4 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px] whitespace-nowrap"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row, ri) => (
            <tr key={ri} className="hover:bg-slate-50/60 transition-colors">
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className={`px-4 py-2.5 text-slate-700 leading-relaxed
                    ${ci === highlightCol ? 'font-semibold text-brand-red' : ''}
                    ${ci === 0 ? 'font-medium' : ''}`}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function ErrorPatternsTable({ patterns }: { patterns: ErrorPattern[] }) {
  if (patterns.length === 0) return null;

  const classColor = (c: string) => {
    const cl = c.toLowerCase();
    if (cl.includes('high')) return 'bg-red-50 text-red-700 border-red-200';
    if (cl.includes('error')) return 'bg-amber-50 text-amber-700 border-amber-200';
    if (cl.includes('no error')) return 'bg-green-50 text-green-700 border-green-200';
    return 'bg-slate-50 text-slate-600 border-slate-200';
  };

  return (
    <SectionCard
      icon={<AlertCircle className="w-4.5 h-4.5 text-brand-red" />}
      title="Aggregated Error Patterns"
    >
      <StyledTable
        headers={['Pattern', 'Source', 'Classification', 'Significance']}
        highlightCol={3}
        rows={patterns.map(p => [
          <span>{p.pattern}</span>,
          <code className="text-[11px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-600 break-all">
            {p.source}
          </code>,
          <span className={`inline-block text-[10px] font-semibold px-2 py-0.5 rounded-full border ${classColor(p.classification)}`}>
            {p.classification}
          </span>,
          <span>{p.significance}</span>,
        ])}
      />
    </SectionCard>
  );
}

function ResolutionsTable({ resolutions, fallback }: { resolutions: Resolution[]; fallback: string }) {
  if (resolutions.length === 0 && !fallback) return null;

  const priorityBadge = (p: string) => {
    const pl = p.toLowerCase();
    if (pl.includes('critical') || pl.includes('immediate'))
      return 'bg-red-50 text-red-700 border-red-200';
    if (pl.includes('high'))
      return 'bg-amber-50 text-amber-700 border-amber-200';
    if (pl.includes('medium'))
      return 'bg-blue-50 text-blue-700 border-blue-200';
    if (pl.includes('low'))
      return 'bg-green-50 text-green-700 border-green-200';
    return 'bg-slate-50 text-slate-600 border-slate-200';
  };

  return (
    <SectionCard
      icon={<Wrench className="w-4.5 h-4.5 text-brand-red" />}
      title="Resolution"
    >
      {resolutions.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full text-left text-[12.5px]">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200">
                <th className="px-3 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px] w-8">#</th>
                <th className="px-3 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px]">Issue</th>
                <th className="px-3 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px] whitespace-nowrap">Priority</th>
                <th className="px-3 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px]">Short-term Action</th>
                <th className="px-3 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px]">Long-term Action</th>
                <th className="px-3 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px]">Validation</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {resolutions.map((r, i) => (
                <tr key={i} className="hover:bg-slate-50/60 transition-colors align-top">
                  <td className="px-3 py-2.5 font-bold text-slate-500 text-center">{r.num}</td>
                  <td className="px-3 py-2.5 text-slate-800 font-medium leading-relaxed">{r.issue}</td>
                  <td className="px-3 py-2.5">
                    {r.priority && (
                      <span className={`inline-block text-[10px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap ${priorityBadge(r.priority)}`}>
                        {r.priority}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-slate-700 leading-relaxed">{r.shortTerm}</td>
                  <td className="px-3 py-2.5 text-slate-700 leading-relaxed">{r.longTerm}</td>
                  <td className="px-3 py-2.5 text-slate-700 leading-relaxed">{r.validation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="prose prose-slate max-w-none prose-headings:text-slate-800">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h3: ({ children }) => (
                <h3 className="text-base font-semibold text-slate-800 mt-6 mb-2">{children}</h3>
              ),
              strong: ({ children }) => (
                <strong className="text-brand-red font-semibold">{children}</strong>
              ),
              table: ({ children }) => (
                <div className="overflow-x-auto my-4 rounded-lg border border-slate-200">
                  <table className="w-full text-left text-[12.5px]">{children}</table>
                </div>
              ),
              thead: ({ children }) => (
                <thead className="bg-slate-50 border-b border-slate-200">{children}</thead>
              ),
              th: ({ children }) => (
                <th className="px-4 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px] whitespace-nowrap">
                  {children}
                </th>
              ),
              td: ({ children }) => (
                <td className="px-4 py-2.5 text-slate-700 leading-relaxed">{children}</td>
              ),
              tr: ({ children }) => (
                <tr className="border-b border-slate-100 hover:bg-slate-50/60 transition-colors">
                  {children}
                </tr>
              ),
              code: ({ className, children, ...props }) => {
                const isBlock = className?.includes('language-');
                if (!isBlock) {
                  return (
                    <code
                      className="bg-slate-100 px-1.5 py-0.5 rounded text-[12px] font-mono text-slate-700"
                      {...props}
                    >
                      {children}
                    </code>
                  );
                }
                return <code {...props}>{children}</code>;
              },
              pre: ({ children }) => (
                <pre className="my-3 p-4 rounded-lg bg-slate-100 overflow-x-auto text-[12.5px] font-mono text-slate-800">
                  {children}
                </pre>
              ),
              li: ({ children }) => (
                <li className="text-slate-600 leading-relaxed flex items-start gap-2">
                  <span className="text-brand-red mt-1.5 text-xs flex-shrink-0">●</span>
                  <span>{children}</span>
                </li>
              ),
              ul: ({ children }) => <ul className="space-y-2 list-none pl-0 my-2">{children}</ul>,
              ol: ({ children }) => <ol className="space-y-3 list-decimal pl-5 my-2">{children}</ol>,
              p: ({ children }) => (
                <p className="text-[13px] text-slate-600 leading-relaxed my-2">{children}</p>
              ),
              blockquote: ({ children }) => (
                <blockquote className="border-l-4 border-amber-400 bg-amber-50/50 pl-4 py-2 my-3 text-[13px] text-slate-700 rounded-r">
                  {children}
                </blockquote>
              ),
            }}
          >
            {fallback}
          </ReactMarkdown>
        </div>
      )}
    </SectionCard>
  );
}

function CoverageTable({
  rounds,
  missing,
  tiers,
  keyLimitation,
}: {
  rounds: CoverageRound[];
  missing: string[];
  tiers: string[];
  keyLimitation: string;
}) {
  if (rounds.length === 0 && missing.length === 0) return null;

  const resultBadge = (r: string) => {
    const rl = r.toLowerCase();
    if (rl.includes('error') || rl.includes('failure'))
      return 'bg-red-50 text-red-700';
    if (rl.includes('no error') || rl.includes('no errors'))
      return 'bg-green-50 text-green-700';
    if (rl.includes('processed') || rl.includes('collected'))
      return 'bg-blue-50 text-blue-700';
    return '';
  };

  return (
    <SectionCard
      icon={<FileSearch className="w-4.5 h-4.5 text-brand-red" />}
      title="Analysis Coverage"
    >
      <div className="space-y-5">
        {rounds.map((round, ri) => (
          <div key={ri}>
            <h3 className="text-[13px] font-semibold text-slate-700 mb-2">{round.title}</h3>
            {round.files.length > 0 && (
              <StyledTable
                headers={['File', 'Type', 'Result']}
                rows={round.files.map(f => [
                  <code className="text-[11px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-600 break-all">
                    {f.file}
                  </code>,
                  <span className="text-[10.5px] font-medium uppercase text-slate-500">{f.type}</span>,
                  <span className={`text-[12px] ${resultBadge(f.result)}`}>{f.result}</span>,
                ])}
              />
            )}
            {round.note && (
              <p className="mt-2 text-[11.5px] text-slate-500 italic leading-relaxed">{round.note}</p>
            )}
          </div>
        ))}

        {missing.length > 0 && (
          <div>
            <h3 className="text-[13px] font-semibold text-slate-700 mb-2">
              Files Not Found in Must-Gather
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
              {missing.map((f, i) => (
                <div key={i} className="flex items-start gap-2 text-[11.5px] text-slate-500">
                  <span className="text-red-400 mt-0.5">✕</span>
                  <code className="bg-slate-50 px-1 py-0.5 rounded text-[10.5px] break-all">{f}</code>
                </div>
              ))}
            </div>
          </div>
        )}

        {tiers.length > 0 && (
          <div>
            <h3 className="text-[13px] font-semibold text-slate-700 mb-2">Priority Tiers</h3>
            <div className="space-y-1">
              {tiers.map((t, i) => (
                <div key={i} className="text-[12px] text-slate-600">{t}</div>
              ))}
            </div>
          </div>
        )}

        {keyLimitation && (
          <div className="bg-amber-50 border-l-4 border-amber-400 rounded-r-lg px-5 py-4">
            <div className="flex items-center gap-2 mb-1.5">
              <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0" />
              <span className="text-[13px] font-bold text-amber-800">Key Limitation</span>
            </div>
            <p className="text-[12.5px] text-amber-800/90 leading-relaxed">
              {keyLimitation}
            </p>
          </div>
        )}
      </div>
    </SectionCard>
  );
}

function CostTable({ rows, total }: { rows: CostRow[]; total: string }) {
  if (rows.length === 0) return null;

  return (
    <SectionCard
      icon={<Zap className="w-4.5 h-4.5 text-brand-red" />}
      title="LLM API Token & Cost Summary"
    >
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full text-left text-[12.5px]">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200">
              <th className="px-4 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px]">Phase</th>
              <th className="px-4 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px] text-right">Input Tokens</th>
              <th className="px-4 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px] text-right">Output Tokens</th>
              <th className="px-4 py-2.5 font-semibold text-slate-600 uppercase tracking-wide text-[10.5px] text-right">Cost (USD)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r, i) => (
              <tr key={i} className="hover:bg-slate-50/60 transition-colors">
                <td className={`px-4 py-2.5 text-slate-700 font-medium ${r.indent ? 'pl-8' : ''}`}>
                  {r.phase}
                </td>
                <td className="px-4 py-2.5 text-slate-600 text-right font-mono text-[11.5px]">
                  {r.inputTokens}
                </td>
                <td className="px-4 py-2.5 text-slate-600 text-right font-mono text-[11.5px]">
                  {r.outputTokens}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-[11.5px] font-semibold text-brand-red">
                  {r.cost}
                </td>
              </tr>
            ))}
          </tbody>
          {total && (
            <tfoot>
              <tr className="bg-slate-50 border-t border-slate-300">
                <td className="px-4 py-3 font-bold text-slate-800" colSpan={3}>Total</td>
                <td className="px-4 py-3 text-right font-mono font-bold text-brand-red text-[13px]">
                  {total}
                </td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </SectionCard>
  );
}

function fmt(bytesStr: string) {
  const n = Number((bytesStr || '0').replace(/,/g, ''));
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)} MB`;
  if (n >= 1_024) return `${(n / 1_024).toFixed(1)} KB`;
  return `${n.toLocaleString()} B`;
}

function CompressionTable({ data }: { data: CompressionData }) {
  return (
    <SectionCard
      icon={<HardDrive className="w-4.5 h-4.5 text-brand-red" />}
      title="Compression Ratio"
    >
      <div className="space-y-5">
        {/* Pipeline visualisation */}
        {data.totalInput && (
          <div className="flex items-center gap-2 flex-wrap">
            {/* Box 1 — shortlisted files total */}
            <div className="flex-1 min-w-[130px] bg-slate-50 border border-slate-200 rounded-lg px-4 py-3 text-center">
              <div className="text-[11px] uppercase tracking-wide text-slate-500 font-semibold mb-1">
                Shortlisted Files
              </div>
              <div className="text-lg font-bold text-slate-700">{fmt(data.totalInput)}</div>
              <div className="text-[10px] text-slate-400 mt-0.5">total original size</div>
            </div>

            <ArrowRight className="w-4 h-4 text-slate-400 flex-shrink-0" />

            {/* Box 2 — payload to LLM */}
            <div className="flex-1 min-w-[130px] bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 text-center">
              <div className="text-[11px] uppercase tracking-wide text-amber-600 font-semibold mb-1">
                Payload to LLM
              </div>
              <div className="text-lg font-bold text-amber-700">{fmt(data.totalPayload)}</div>
              <div className="text-[10px] text-amber-500 mt-0.5">after extraction</div>
            </div>

            {data.overallRatio && (
              <>
                <ArrowRight className="w-4 h-4 text-slate-400 flex-shrink-0" />
                {/* Box 3 — overall ratio */}
                <div className="flex-1 min-w-[110px] bg-brand-red/5 border border-brand-red/20 rounded-lg px-4 py-3 text-center">
                  <div className="text-[11px] uppercase tracking-wide text-brand-red font-semibold mb-1">
                    Compression
                  </div>
                  <div className="text-2xl font-bold text-brand-red">{data.overallRatio}</div>
                  <div className="text-[10px] text-brand-red/60 mt-0.5">overall ratio</div>
                </div>
              </>
            )}
          </div>
        )}

        {/* Per-file table */}
        {data.files.length > 0 && (
          <StyledTable
            headers={['File', 'Type', 'Original', 'Payload', 'Ratio']}
            highlightCol={4}
            rows={data.files.map(f => [
              <code className="text-[11px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-600 break-all">
                {f.file}
              </code>,
              <span className="text-[10.5px] font-medium uppercase text-slate-500">{f.type}</span>,
              <span className="font-mono text-[11.5px] text-right block">{fmt(f.originalBytes)}</span>,
              <span className="font-mono text-[11.5px] text-right block">{fmt(f.payloadBytes)}</span>,
              <span className="font-semibold">{f.ratio}</span>,
            ])}
          />
        )}
      </div>
    </SectionCard>
  );
}

// ── Per-stage individual cost card ───────────────────────────────────

interface StageRCAResult {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  payload_bytes?: number;
}

function PerStageCostCard({
  stageName,
  cost,
}: {
  stageName: string;
  cost: StageRCAResult;
}) {
  function tok(n: number) { return n > 0 ? n.toLocaleString() : '—'; }
  function usd(n: number) { return n > 0 ? `$${n.toFixed(6)}` : '—'; }

  const rows: { label: string; in: string; out: string; cost: string }[] = [
    {
      label: `5. RCA (${stageName} pass only)`,
      in: tok(cost.input_tokens),
      out: tok(cost.output_tokens),
      cost: usd(cost.cost_usd),
    },
  ];

  return (
    <SectionCard
      icon={<Zap className="w-4.5 h-4.5 text-amber-500" />}
      title={`Cost — ${stageName} Pass Only`}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200">
              {['Phase', 'Input tokens', 'Output tokens', 'Cost (USD)'].map(h => (
                <th key={h} className="px-4 py-2.5 text-left font-semibold text-slate-500 uppercase tracking-wide text-[10.5px] whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-slate-100">
                <td className="px-4 py-2 text-slate-700">{r.label}</td>
                <td className="px-4 py-2 text-right font-mono text-slate-600">{r.in}</td>
                <td className="px-4 py-2 text-right font-mono text-slate-600">{r.out}</td>
                <td className="px-4 py-2 text-right font-semibold text-brand-red">{r.cost}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="bg-slate-50">
              <td className="px-4 py-2 font-semibold text-slate-700">Total (this pass)</td>
              <td colSpan={2} />
              <td className="px-4 py-2 text-right font-bold text-brand-red">{usd(cost.cost_usd)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      {cost.payload_bytes != null && cost.payload_bytes > 0 && (
        <p className="mt-2 text-[11px] text-slate-400 px-1">
          Payload sent to LLM: {fmt(String(cost.payload_bytes))}
        </p>
      )}
    </SectionCard>
  );
}

// ── 4-tab Cost + Compression section ─────────────────────────────────

type CostTabKey = 'Total' | 'Tier_1' | 'Tier_2' | 'Final';

const COST_TAB_DEFS: Record<CostTabKey, { short: string; label: string; sublabel: string }> = {
  Total:  { short: 'Σ',  label: 'Total',  sublabel: 'All passes' },
  Tier_1: { short: 'T1', label: 'Tier 1', sublabel: 'Tier 1 only' },
  Tier_2: { short: 'T2', label: 'Tier 2', sublabel: 'Tier 2 only' },
  Final:  { short: 'F',  label: 'Final',  sublabel: 'Final only' },
};

function CostCompressionSection({
  stageReports,
  perStageCosts,
}: {
  stageReports: { stage: 'Tier_1' | 'Tier_2' | 'Final'; text: string }[];
  perStageCosts?: Record<string, StageRCAResult>;
}) {
  const availableStages = stageReports.map(r => r.stage);
  const availableTabs: CostTabKey[] = ['Total', ...availableStages as CostTabKey[]];

  const [activeTab, setActiveTab] = useState<CostTabKey>('Total');

  // For 'Total' use the last stageReport (fully cumulative table)
  const totalParsed = useMemo(() => {
    const text = stageReports[stageReports.length - 1]?.text ?? '';
    return parseTables(text);
  }, [stageReports]);

  // For individual stage tabs, parse that stage's report
  const stageText = useMemo(() => {
    if (activeTab === 'Total') return '';
    return stageReports.find(r => r.stage === activeTab)?.text ?? '';
  }, [stageReports, activeTab]);

  const stageParsed = useMemo(() => stageText ? parseTables(stageText) : null, [stageText]);

  const activeStageCost = activeTab !== 'Total' ? perStageCosts?.[activeTab] : null;

  if (!totalParsed || (totalParsed.costRows.length === 0 && !totalParsed.compression)) return null;

  const isActive = (tab: CostTabKey) => activeTab === tab;

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-medium text-slate-500 mr-1">Cost / Compression:</span>
        {availableTabs.map(tab => {
          const def = COST_TAB_DEFS[tab];
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium
                          border transition-all duration-200
                          ${isActive(tab)
                            ? 'bg-brand-red text-white border-brand-red shadow-sm'
                            : 'bg-white text-slate-600 border-slate-200 hover:border-brand-red/50 hover:text-brand-red'
                          }`}
            >
              <span
                className={`w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold
                            ${isActive(tab) ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-500'}`}
              >
                {def.short}
              </span>
              {def.label}
              <span className={`text-[10px] ${isActive(tab) ? 'text-white/70' : 'text-slate-400'}`}>
                · {def.sublabel}
              </span>
            </button>
          );
        })}
      </div>

      {/* Content area */}
      {activeTab === 'Total' ? (
        <>
          <CostTable rows={totalParsed.costRows} total={totalParsed.costTotal} />
          {totalParsed.compression && <CompressionTable data={totalParsed.compression} />}
        </>
      ) : activeStageCost ? (
        <>
          <PerStageCostCard stageName={activeTab} cost={activeStageCost} />
          {stageParsed?.compression && <CompressionTable data={stageParsed.compression} />}
        </>
      ) : stageParsed ? (
        <>
          <CostTable rows={stageParsed.costRows} total={stageParsed.costTotal} />
          {stageParsed.compression && <CompressionTable data={stageParsed.compression} />}
        </>
      ) : null}
    </div>
  );
}

// ── Main Export ───────────────────────────────────────────────────────

interface RCADetailTablesProps {
  rcaText: string;
  /** Stage reports for the 4-tab cost selector (Total / H / M / L). */
  stageReports?: { stage: 'Tier_1' | 'Tier_2' | 'Final'; text: string }[];
  /** Per-stage individual RCA costs from the backend session results. */
  perStageCosts?: Record<string, StageRCAResult>;
  /** Which sections to render. Default: all true. */
  showErrorPatterns?: boolean;
  showResolutions?: boolean;
  showCoverage?: boolean;
  showCost?: boolean;
}

export default function RCADetailTables({
  rcaText,
  stageReports = [],
  perStageCosts,
  showErrorPatterns = true,
  showResolutions = true,
  showCoverage = true,
  showCost = true,
}: RCADetailTablesProps) {
  const parsed = useMemo(() => parseTables(rcaText), [rcaText]);

  const hasContent = parsed || stageReports.length > 0;
  if (!hasContent) return null;

  return (
    <div className="space-y-6">
      {showErrorPatterns && parsed && <ErrorPatternsTable patterns={parsed.errorPatterns} />}

      {showResolutions && parsed && (
        <ResolutionsTable resolutions={parsed.resolutions} fallback={parsed.resolutionsFallback} />
      )}

      {showCoverage && parsed && (
        <CoverageTable
          rounds={parsed.coverageRounds}
          missing={parsed.coverageMissing}
          tiers={parsed.coverageTiers}
          keyLimitation={parsed.keyLimitation}
        />
      )}

      {/* Cost + compression: 4-tab selector when stageReports available */}
      {showCost && (
        stageReports.length > 0
          ? <CostCompressionSection stageReports={stageReports} perStageCosts={perStageCosts} />
          : parsed && (
            <>
              <CostTable rows={parsed.costRows} total={parsed.costTotal} />
              {parsed.compression && <CompressionTable data={parsed.compression} />}
            </>
          )
      )}
    </div>
  );
}

/**
 * Returns the RCA text with the table/cost sections stripped out,
 * so the markdown renderer doesn't duplicate them as raw text.
 *
 * Works by splitting on ## headings, dropping matched sections by name,
 * and chopping everything from the plain-text ===...LLM API TOKEN block onward.
 */
export function stripTableSections(rcaText: string): string {
  const STRIP_EXACT = new Set([
    'aggregated error patterns',
    'analysis coverage',
    // Chronology of events is RETAINED in RCA Report
  ]);

  function isStripped(title: string): boolean {
    if (STRIP_EXACT.has(title)) return true;
    const norm = title.replace(/[^a-z]/g, '');
    return norm.startsWith('recommendation') || norm.startsWith('resolution') || norm.startsWith('remediation');
  }

  // 1. Chop the plain-text cost/compression footer (starts with === lines)
  let text = rcaText.replace(/\n*={4,}[\t ]*\n[\t ]*LLM API TOKEN[\s\S]*$/, '');

  // 1b. Some model outputs omit the "## Analysis Coverage" heading but still emit
  // coverage blocks (Round 1 table, Files Not Found, Priority Tiers, Key Limitation)
  // at the tail. Remove that tail so it does not leak into main markdown.
  const coverageAnchors = [
    /^\s*#{0,3}\s*Round\s*1\s*[—-]\s*HIGH\s+Priority\s+Files\b/im,
    /^\s*#{0,3}\s*Files\s+Not\s+Found\s+in\s+This\s+Must-Gather\b/im,
    /^\s*#{0,3}\s*Priority\s+Tiers\s+Analyzed\b/im,
    /^\s*#{0,3}\s*Key\s+Limitation\b/im,
  ];
  let earliest = -1;
  for (const re of coverageAnchors) {
    const m = re.exec(text);
    if (m && (earliest === -1 || m.index < earliest)) earliest = m.index;
  }
  if (earliest >= 0) {
    text = text.slice(0, earliest).trimEnd();
  }

  // 2. Remove unheaded remediation/recommendation tails if present.
  // No `m` flag — $ must match end of the full string, not end of each line.
  text = text.replace(
    /\n(?:\*\*)?(?:remediation|remedition|resolutions?|recommendations?)(?:\*\*)?\s*[:\-][\s\S]*$/i,
    ''
  );

  // 3. Split into heading-delimited chunks and filter.
  // Rules:
  //   a) Lines inside a fenced code block (``` ... ```) are never treated as
  //      headings — bash comments like "# Check the state" would otherwise
  //      match as H1 and incorrectly reset the skip flag.
  //   b) Track the heading level that triggered skipping so that sub-headings
  //      inside a skipped section (e.g. ### under ## Remediation) do NOT
  //      accidentally reset skipping back to false.
  const lines = text.split('\n');
  const output: string[] = [];
  let skipping = false;
  let skippingLevel = 0;
  let inCodeBlock = false;

  for (const line of lines) {
    // Toggle code-block state on every fence line (``` or ~~~).
    if (/^[ \t]*(`{3,}|~{3,})/.test(line)) {
      inCodeBlock = !inCodeBlock;
      if (skipping) continue;
      output.push(line);
      continue;
    }

    // Inside a code block: never do heading detection.
    if (inCodeBlock) {
      if (skipping) continue;
      output.push(line);
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.+)/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const title = headingMatch[2].replace(/\*\*/g, '').trim().toLowerCase();

      // Sub-heading inside an already-skipped section — keep skipping.
      if (skipping && level > skippingLevel) {
        continue;
      }

      if (isStripped(title)) {
        skipping = true;
        skippingLevel = level;
        continue;
      }
      skipping = false;
      skippingLevel = 0;
    }

    if (skipping) continue;
    output.push(line);
  }

  // 4. Clean trailing --- separators and whitespace
  let result = output.join('\n');
  result = result.replace(/(\n---\s*)+\s*$/, '').trimEnd();
  return result;
}
