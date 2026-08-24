import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, AlertTriangle, Info, GitBranch, Clock } from 'lucide-react';

// ── Types ────────────────────────────────────────────────────────────

interface CauseItem {
  title: string;
  details: string[];
  type: 'primary' | 'secondary';
}

interface TimelineEvent {
  timestamp: string;
  description: string;
  phase?: string;
}

interface DiagramData {
  effect: string;
  causes: CauseItem[];
  timeline: TimelineEvent[];
}

// ── RCA Markdown Parser ──────────────────────────────────────────────

function extractSections(text: string): Record<string, string> {
  const sections: Record<string, string> = {};
  const lines = text.split('\n');
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
  return sections;
}

function parsePrimaryCauses(text: string): CauseItem[] {
  if (!text) return [];
  const causes: CauseItem[] = [];
  const parts = text.split(/\n(?=\d+\.\s)/);

  for (const part of parts) {
    const match = part.match(/^\d+\.\s*(.+)/);
    if (!match) continue;
    const title = match[1].replace(/\*\*/g, '').trim();
    const details: string[] = [];
    for (const line of part.split('\n').slice(1)) {
      const bullet = line.match(/^\s*[-•]\s+(.+)/);
      if (bullet) details.push(bullet[1].replace(/\*\*/g, '').trim());
    }
    causes.push({ title, details, type: 'primary' });
  }
  return causes;
}

function parseSecondaryCauses(text: string): CauseItem[] {
  if (!text) return [];
  const causes: CauseItem[] = [];
  const lines = text.split('\n');
  let title = '';
  let details: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const bullet = trimmed.match(/^[-•]\s+(.+)/);
    if (bullet) {
      details.push(bullet[1].replace(/\*\*/g, '').trim());
    } else {
      if (title) causes.push({ title, details, type: 'secondary' });
      title = trimmed.replace(/\*\*/g, '').trim();
      details = [];
    }
  }
  if (title) causes.push({ title, details, type: 'secondary' });
  return causes;
}

function parseChronology(text: string): TimelineEvent[] {
  if (!text) return [];
  const events: TimelineEvent[] = [];
  let phase = '';

  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!t) continue;

    const bulletMatch = t.match(/^[-•]\s+\*\*(.+?)\*\*[:\s]+(.+)/);
    if (bulletMatch) {
      events.push({ timestamp: bulletMatch[1].trim(), description: bulletMatch[2].trim(), phase });
      continue;
    }

    if (t.startsWith('|')) {
      const cells = t.split('|').filter(c => c.trim());
      if (
        cells.length >= 2 &&
        !cells[0].match(/^[\s\-:]+$/) &&
        !/^timestamp$/i.test(cells[0].trim()) &&
        !/^event$/i.test(cells[0].trim())
      ) {
        events.push({
          timestamp: cells[0].trim(),
          description: cells.slice(1).map(c => c.trim()).filter(Boolean).join(' — '),
          phase,
        });
      }
      continue;
    }

    if (!t.startsWith('|') && !t.startsWith('-') && !t.match(/^\d+\./)) {
      phase = t.replace(/\*\*/g, '').trim();
    }
  }
  return events;
}

function parseRCAForDiagram(rcaText: string): DiagramData | null {
  if (!rcaText) return null;
  const sections = extractSections(rcaText);

  const effect = sections['User Reported Issue'] || '';
  if (!effect) return null;

  const primary = parsePrimaryCauses(
    sections['Primary Root Cause(s)'] || sections['Primary Root Causes'] || ''
  );
  const secondary = parseSecondaryCauses(
    sections['Secondary Causes / Contributing Factors'] ||
    sections['Secondary Causes'] ||
    sections['Contributing Factors'] || ''
  );

  const causes = [...primary, ...secondary];
  if (causes.length === 0) return null;

  const timeline = parseChronology(
    sections['Chronology of Events'] || sections['Chronology'] || ''
  );

  return { effect, causes, timeline };
}

// ── Helpers ──────────────────────────────────────────────────────────

function trunc(text: string, max: number) {
  if (text.length <= max) return text;
  return text.slice(0, max - 1) + '\u2026';
}

function effectLabel(fullText: string): string {
  const first = fullText.split('\n').filter(l => l.trim())[0] || fullText;
  return trunc(first.replace(/\*\*/g, '').trim(), 100);
}

// ── Fishbone SVG ─────────────────────────────────────────────────────

function FishboneSVG({ data }: { data: DiagramData }) {
  const n = data.causes.length;

  const SPINE_PAD = 50;
  const SPACING = Math.max(155, Math.min(210, 860 / n));
  const EFFECT_W = 210;
  const EFFECT_H = 70;
  const BRANCH_DY = 88;
  const BRANCH_DX = 48;
  const LABEL_W = 150;
  const LABEL_H = 42;

  const spineLen = n * SPACING + 50;
  const W = SPINE_PAD + spineLen + 24 + EFFECT_W + 16;
  const H = BRANCH_DY * 2 + LABEL_H * 2 + 80;
  const SY = H / 2;
  const sEnd = SPINE_PAD + spineLen;

  return (
    <div className="overflow-x-auto py-2">
      <svg
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        className="mx-auto block"
        style={{ minWidth: Math.min(W, 680) }}
      >
        <defs>
          <marker id="fb-arrow" markerWidth="14" markerHeight="10" refX="14" refY="5" orient="auto">
            <polygon points="0 0, 14 5, 0 10" fill="#EE0000" />
          </marker>
          <filter id="card-shadow" x="-4%" y="-8%" width="108%" height="120%">
            <feDropShadow dx="0" dy="1" stdDeviation="2" floodOpacity="0.08" />
          </filter>
        </defs>

        {/* Spine */}
        <line
          x1={SPINE_PAD} y1={SY} x2={sEnd} y2={SY}
          stroke="#EE0000" strokeWidth={3.5} markerEnd="url(#fb-arrow)"
        />

        {/* Effect (fish head) */}
        <rect
          x={sEnd + 18} y={SY - EFFECT_H / 2}
          width={EFFECT_W} height={EFFECT_H}
          rx={12} fill="#EE0000"
          filter="url(#card-shadow)"
        />
        <foreignObject x={sEnd + 18} y={SY - EFFECT_H / 2} width={EFFECT_W} height={EFFECT_H}>
          <div
            style={{
              width: EFFECT_W, height: EFFECT_H,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              padding: '8px 14px', textAlign: 'center',
              color: 'white', fontSize: 11.5, fontWeight: 700, lineHeight: '1.35',
              overflow: 'hidden',
            }}
          >
            {effectLabel(data.effect)}
          </div>
        </foreignObject>

        {/* Branches */}
        {data.causes.map((cause, i) => {
          const jx = SPINE_PAD + (i + 0.5) * SPACING + 25;
          const isTop = i % 2 === 0;
          const dir = isTop ? -1 : 1;
          const endX = jx - BRANCH_DX;
          const endY = SY + dir * BRANCH_DY;
          const isPrimary = cause.type === 'primary';

          const col = isPrimary ? '#EE0000' : '#64748b';
          const lx = endX - LABEL_W / 2;
          const ly = isTop ? endY - LABEL_H - 4 : endY + 4;
          const lineTargetY = isTop ? ly + LABEL_H : ly;

          const badgeW = isPrimary ? 58 : 86;
          const badgeLabel = isPrimary ? 'PRIMARY' : 'CONTRIBUTING';
          const badgeCol = isPrimary ? '#EE0000' : '#94a3b8';
          const badgeX = lx + (LABEL_W - badgeW) / 2;
          const badgeY = isTop ? ly - 15 : ly + LABEL_H + 3;

          return (
            <g key={i}>
              {/* Branch line */}
              <line
                x1={jx} y1={SY} x2={endX} y2={lineTargetY}
                stroke={col} strokeWidth={isPrimary ? 2.5 : 1.5}
              />
              <circle cx={jx} cy={SY} r={3.5} fill={col} />

              {/* Label card */}
              <rect
                x={lx} y={ly} width={LABEL_W} height={LABEL_H}
                rx={6} fill="white" stroke={col}
                strokeWidth={isPrimary ? 1.5 : 1}
                filter="url(#card-shadow)"
              />
              <foreignObject x={lx} y={ly} width={LABEL_W} height={LABEL_H}>
                <div
                  style={{
                    width: LABEL_W, height: LABEL_H,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    padding: '3px 8px', textAlign: 'center',
                    fontSize: isPrimary ? 10.5 : 9.5,
                    fontWeight: isPrimary ? 600 : 500,
                    color: isPrimary ? '#0f172a' : '#475569',
                    lineHeight: '1.3', overflow: 'hidden',
                  }}
                >
                  {trunc(cause.title, 60)}
                </div>
              </foreignObject>

              {/* Type badge */}
              <rect x={badgeX} y={badgeY} width={badgeW} height={14} rx={3} fill={badgeCol} />
              <text
                x={badgeX + badgeW / 2} y={badgeY + 10.5}
                textAnchor="middle" fontSize={7.5} fontWeight={700} fill="white"
                fontFamily="Inter, system-ui, sans-serif"
              >
                {badgeLabel}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── Cause Detail Cards ───────────────────────────────────────────────

function CauseDetailCards({ causes }: { causes: CauseItem[] }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const toggle = (i: number) => setExpanded(prev => (prev === i ? null : i));

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-6">
      {causes.map((c, i) => {
        const open = expanded === i;
        const pri = c.type === 'primary';

        return (
          <div
            key={i}
            className={`rounded-lg border overflow-hidden transition-shadow
              ${pri ? 'border-brand-red/40' : 'border-slate-200'}
              ${open ? 'shadow-md' : 'shadow-sm'} bg-white`}
          >
            <button
              onClick={() => toggle(i)}
              className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 transition-colors"
            >
              {pri
                ? <AlertTriangle className="w-4 h-4 text-brand-red flex-shrink-0" />
                : <Info className="w-4 h-4 text-slate-400 flex-shrink-0" />}
              <div className="flex-1 min-w-0">
                <span
                  className={`inline-block text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded
                    ${pri ? 'bg-brand-red/10 text-brand-red' : 'bg-slate-100 text-slate-500'}`}
                >
                  {pri ? 'Primary Cause' : 'Contributing Factor'}
                </span>
                <p className="text-sm font-medium text-slate-800 mt-1 leading-snug">{c.title}</p>
              </div>
              {c.details.length > 0 && (
                open
                  ? <ChevronDown className="w-4 h-4 text-slate-400 flex-shrink-0" />
                  : <ChevronRight className="w-4 h-4 text-slate-400 flex-shrink-0" />
              )}
            </button>

            {open && c.details.length > 0 && (
              <div className="px-4 pb-3 pt-0 border-t border-slate-100">
                <ul className="mt-2 space-y-1.5">
                  {c.details.map((d, j) => (
                    <li key={j} className="flex items-start gap-2 text-xs text-slate-600 leading-relaxed">
                      <span className="text-brand-red mt-0.5 text-[10px]">●</span>
                      <span>{d}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Event Timeline (vertical) ────────────────────────────────────────

function EventTimeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) return null;

  let lastPhase = '';

  return (
    <div className="overflow-x-auto">
      <div className="relative pl-8 min-w-max">
        {/* Vertical track */}
        <div className="absolute left-[13px] top-2 bottom-2 w-0.5 bg-brand-red/25 rounded-full" />

        {events.map((ev, i) => {
          const newPhase = ev.phase && ev.phase !== lastPhase;
          if (ev.phase) lastPhase = ev.phase;

          return (
            <div key={i}>
              {newPhase && (
                <div className="mb-1.5 mt-5 first:mt-0">
                  <span className="text-[11px] font-semibold text-slate-700 bg-slate-100 px-2.5 py-1 rounded-md whitespace-nowrap">
                    {ev.phase}
                  </span>
                </div>
              )}
              <div className="relative flex items-center gap-4 py-1.5 group whitespace-nowrap">
                <div
                  className="absolute -left-[18.5px] top-1/2 -translate-y-1/2 w-[9px] h-[9px] rounded-full bg-brand-red
                    border-2 border-white shadow-sm ring-2 ring-brand-red/20 group-hover:ring-brand-red/40
                    transition-all"
                />
                <div className="text-[11px] font-semibold text-brand-red min-w-[110px] flex-shrink-0">
                  {ev.timestamp}
                </div>
                <div className="text-[11px] text-slate-600">{ev.description}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main Export ───────────────────────────────────────────────────────

export default function RCADiagrams({
  rcaText,
  allTexts,
  activeStageIdx,
}: {
  rcaText: string;
  allTexts?: string[];
  activeStageIdx?: number;
}) {
  const stageTexts = (allTexts && allTexts.length > 0) ? allTexts : [rcaText];
  const safeIdx = Math.max(0, Math.min(activeStageIdx ?? (stageTexts.length - 1), stageTexts.length - 1));

  const data = useMemo(() => {
    // Base diagram from active stage; if missing, fall back to High.
    const base = parseRCAForDiagram(stageTexts[safeIdx]) || parseRCAForDiagram(stageTexts[0]);
    if (!base) return null;

    // Merge causes + chronology cumulatively from High up to selected stage.
    if (stageTexts.length > 1) {
      const mergedCauses = [...base.causes];
      const seenCause = new Set(mergedCauses.map(c => `${c.type}|${c.title.toLowerCase()}`));

      for (const text of stageTexts.slice(0, safeIdx + 1)) {
        const parsed = parseRCAForDiagram(text);
        if (!parsed) continue;
        for (const c of parsed.causes) {
          const key = `${c.type}|${c.title.toLowerCase()}`;
          if (!seenCause.has(key)) {
            seenCause.add(key);
            mergedCauses.push(c);
          }
        }
      }

      const seen = new Set(base.timeline.map(e => `${e.timestamp}|${e.description}`));
      const merged = [...base.timeline];
      for (const text of stageTexts.slice(0, safeIdx + 1)) {
        const sections = (() => {
          const result: Record<string, string> = {};
          const lines = text.split('\n');
          let current = '';
          let buffer: string[] = [];
          for (const line of lines) {
            const h = line.match(/^##\s+(.+)/);
            if (h) { if (current) result[current] = buffer.join('\n').trim(); current = h[1].trim(); buffer = []; }
            else buffer.push(line);
          }
          if (current) result[current] = buffer.join('\n').trim();
          return result;
        })();
        const extra = parseChronology(sections['Chronology of Events'] || sections['Chronology'] || '');
        for (const ev of extra) {
          const key = `${ev.timestamp}|${ev.description}`;
          if (!seen.has(key)) { seen.add(key); merged.push(ev); }
        }
      }
      merged.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
      return { ...base, causes: mergedCauses, timeline: merged };
    }

    return base;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rcaText, allTexts, activeStageIdx]);

  if (!data) return null;

  return (
    <div className="space-y-6">
      {/* Fishbone / Ishikawa */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
        <div className="flex items-center gap-2 mb-1">
          <GitBranch className="w-5 h-5 text-brand-red" />
          <h2 className="text-lg font-semibold text-slate-800">Cause-Effect Diagram</h2>
        </div>
        <p className="text-xs text-slate-400 mb-5">
          Primary root causes and contributing factors mapped to the reported issue.
          Click a card below for evidence details.
        </p>

        <FishboneSVG data={data} />
        <CauseDetailCards causes={data.causes} />
      </div>

      {/* Event Timeline */}
      {data.timeline.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
          <div className="flex items-center gap-2 mb-1">
            <Clock className="w-5 h-5 text-brand-red" />
            <h2 className="text-lg font-semibold text-slate-800">Event Timeline</h2>
          </div>
          <p className="text-xs text-slate-400 mb-5">
            Chronological sequence of events extracted from the analysis.
          </p>

          <EventTimeline events={data.timeline} />
        </div>
      )}
    </div>
  );
}
