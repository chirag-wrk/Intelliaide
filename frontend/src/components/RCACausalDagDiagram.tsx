import { useMemo } from 'react';
import { GitBranch } from 'lucide-react';
import type { CausalDag, CausalDagNode } from '../types';

// ── Layout constants ─────────────────────────────────────────────────

const NODE_W = 190;
const NODE_H = 96;
const GAP_X = 70;
const GAP_Y = 20;
const PADDING = 24;

const CATEGORY_STYLES: Record<
  CausalDagNode['category'],
  { border: string; bg: string; dot: string; text: string; label: string }
> = {
  primary: {
    border: 'border-amber-400',
    bg: 'bg-amber-50',
    dot: 'bg-amber-500',
    text: 'text-amber-900',
    label: 'Primary Root Cause',
  },
  symptom: {
    border: 'border-rose-400',
    bg: 'bg-rose-50',
    dot: 'bg-rose-500',
    text: 'text-rose-900',
    label: 'Reported Symptom',
  },
  secondary: {
    border: 'border-sky-400',
    bg: 'bg-sky-50',
    dot: 'bg-sky-500',
    text: 'text-sky-900',
    label: 'Contributing Factor',
  },
};

const EDGE_COLOR: Record<'primary' | 'secondary', string> = {
  primary: '#f59e0b',
  secondary: '#94a3b8',
};

// ── Layout ───────────────────────────────────────────────────────────

interface LayoutNode extends CausalDagNode {
  x: number;
  y: number;
}

interface Layout {
  nodes: LayoutNode[];
  width: number;
  height: number;
}

/**
 * Rank-based layered layout: rank = longest path from a root (node with no
 * incoming edges), columns = ranks, rows = stable order within a rank.
 * Cycle-safe — any node the topological pass can't reach (malformed LLM
 * output) is placed one column past the deepest resolved rank so the
 * diagram still renders instead of breaking.
 */
function computeLayout(dag: CausalDag): Layout {
  const { nodes, edges } = dag;
  const idToIndex = new Map(nodes.map((n, i) => [n.id, i]));

  const outgoing: number[][] = nodes.map(() => []);
  const inDegree = new Array(nodes.length).fill(0);
  for (const e of edges) {
    const from = idToIndex.get(e.from);
    const to = idToIndex.get(e.to);
    if (from === undefined || to === undefined || from === to) continue;
    outgoing[from].push(to);
    inDegree[to] += 1;
  }

  const rank = new Array(nodes.length).fill(0);
  const remainingIn = [...inDegree];
  const queue: number[] = [];
  nodes.forEach((_, i) => { if (remainingIn[i] === 0) queue.push(i); });

  const processed = new Array(nodes.length).fill(false);
  let head = 0;
  while (head < queue.length) {
    const cur = queue[head++];
    processed[cur] = true;
    for (const next of outgoing[cur]) {
      rank[next] = Math.max(rank[next], rank[cur] + 1);
      remainingIn[next] -= 1;
      if (remainingIn[next] === 0) queue.push(next);
    }
  }
  const maxResolvedRank = processed.some(Boolean) ? Math.max(...rank.filter((_, i) => processed[i])) : 0;
  nodes.forEach((_, i) => { if (!processed[i]) rank[i] = maxResolvedRank + 1; });

  const byRank = new Map<number, number[]>();
  nodes.forEach((_, i) => {
    const r = rank[i];
    if (!byRank.has(r)) byRank.set(r, []);
    byRank.get(r)!.push(i);
  });

  const sortedRanks = Array.from(byRank.keys()).sort((a, b) => a - b);
  const maxRows = Math.max(...sortedRanks.map(r => byRank.get(r)!.length));
  const totalHeight = maxRows * (NODE_H + GAP_Y) - GAP_Y + PADDING * 2;
  const totalWidth = PADDING * 2 + sortedRanks.length * NODE_W + (sortedRanks.length - 1) * GAP_X;

  const positioned: LayoutNode[] = new Array(nodes.length);
  sortedRanks.forEach((r, rankPos) => {
    const idxs = byRank.get(r)!;
    const rankHeight = idxs.length * (NODE_H + GAP_Y) - GAP_Y;
    const yOffset = PADDING + (totalHeight - PADDING * 2 - rankHeight) / 2;
    idxs.forEach((nodeIdx, i) => {
      positioned[nodeIdx] = {
        ...nodes[nodeIdx],
        x: PADDING + rankPos * (NODE_W + GAP_X),
        y: yOffset + i * (NODE_H + GAP_Y),
      };
    });
  });

  return { nodes: positioned, width: totalWidth, height: totalHeight };
}

// ── Component ────────────────────────────────────────────────────────

/**
 * Renders ONLY the causal-DAG diagram (color-coded nodes + directed edges)
 * and its color-key legend — no chronology, payload, or evidence tables.
 * Renders nothing if the DAG isn't available (older sessions, or best-effort
 * generation failure), so it never breaks the RCA Detailed page.
 */
export default function RCACausalDagDiagram({ dag }: { dag: CausalDag | null | undefined }) {
  const layout = useMemo(
    () => (dag && dag.nodes.length > 0 ? computeLayout(dag) : null),
    [dag],
  );

  if (!layout || !dag) return null;

  const idToNode = new Map(layout.nodes.map(n => [n.id, n]));
  const categoriesPresent = Array.from(new Set(dag.nodes.map(n => n.category)));

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 my-6">
      <div className="flex items-center gap-2 mb-1">
        <GitBranch className="w-5 h-5 text-brand-red" />
        <h2 className="text-lg font-semibold text-slate-800">Causal Chain</h2>
      </div>
      <p className="text-xs text-slate-400 mb-4">
        Visual map of how the identified causes led to the reported symptom(s).
      </p>

      <div className="flex flex-wrap gap-4 mb-4 text-xs text-slate-500">
        {categoriesPresent.map(cat => (
          <span key={cat} className="inline-flex items-center gap-1.5">
            <span className={`w-2.5 h-2.5 rounded-sm ${CATEGORY_STYLES[cat].dot}`} />
            {CATEGORY_STYLES[cat].label}
          </span>
        ))}
      </div>

      <div className="overflow-x-auto pb-2">
        <div className="relative" style={{ width: layout.width, height: layout.height }}>
          <svg
            width={layout.width}
            height={layout.height}
            className="absolute top-0 left-0 pointer-events-none"
          >
            <defs>
              <marker id="dag-arrow-primary" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0 0 L10 5 L0 10 z" fill={EDGE_COLOR.primary} />
              </marker>
              <marker id="dag-arrow-secondary" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0 0 L10 5 L0 10 z" fill={EDGE_COLOR.secondary} />
              </marker>
            </defs>
            {dag.edges.map((edge, i) => {
              const from = idToNode.get(edge.from);
              const to = idToNode.get(edge.to);
              if (!from || !to) return null;
              const x1 = from.x + NODE_W;
              const y1 = from.y + NODE_H / 2;
              const x2 = to.x;
              const y2 = to.y + NODE_H / 2;
              const midX = (x1 + x2) / 2;
              return (
                <path
                  key={i}
                  d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke={EDGE_COLOR[edge.kind]}
                  strokeWidth={edge.kind === 'primary' ? 2 : 1.25}
                  opacity={edge.kind === 'primary' ? 0.9 : 0.55}
                  markerEnd={`url(#dag-arrow-${edge.kind})`}
                />
              );
            })}
          </svg>

          {layout.nodes.map(node => {
            const style = CATEGORY_STYLES[node.category];
            return (
              <div
                key={node.id}
                className={`absolute rounded-lg border-2 p-2.5 shadow-sm overflow-hidden ${style.border} ${style.bg}`}
                style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
              >
                {node.badge && (
                  <span
                    className={`absolute -top-2.5 -right-2.5 w-5 h-5 rounded-full flex items-center
                      justify-center text-[10px] font-bold text-white shadow ${style.dot}`}
                  >
                    {node.badge}
                  </span>
                )}
                <p className={`text-[11px] font-semibold leading-snug ${style.text}`}>
                  {node.title}
                </p>
                {node.date && <p className="text-[9px] text-slate-400 mt-1">{node.date}</p>}
                {node.short && (
                  <p className="text-[9.5px] text-slate-600 mt-1 leading-snug line-clamp-3">
                    {node.short}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
