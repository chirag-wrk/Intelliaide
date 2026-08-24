"""Render RCA causal-DAG JSON as PNG for bundle export.

Draws directly with Pillow at the target pixel size so boxes, edges, and text
scale together (matches the React diagram layout coordinates).
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont

NODE_W = 190
NODE_H = 96
GAP_X = 70
GAP_Y = 20
PADDING = 24

_CATEGORY_STYLE = {
    "primary": {"face": "#fffbeb", "edge": "#fbbf24", "text": "#78350f"},
    "symptom": {"face": "#fff1f2", "edge": "#fb7185", "text": "#881337"},
    "secondary": {"face": "#f0f9ff", "edge": "#38bdf8", "text": "#0c4a6e"},
}

_EDGE_COLOR = {
    "primary": "#f59e0b",
    "secondary": "#94a3b8",
}

_FONT_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)
_FONT_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


def _compute_layout(dag: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], float, float]:
    nodes = dag.get("nodes") or []
    edges = dag.get("edges") or []
    if not nodes:
        return [], 0.0, 0.0

    id_to_index = {n["id"]: i for i, n in enumerate(nodes)}
    outgoing: List[List[int]] = [[] for _ in nodes]
    in_degree = [0] * len(nodes)
    for edge in edges:
        src = id_to_index.get(edge.get("from"))
        dst = id_to_index.get(edge.get("to"))
        if src is None or dst is None or src == dst:
            continue
        outgoing[src].append(dst)
        in_degree[dst] += 1

    rank = [0] * len(nodes)
    remaining_in = list(in_degree)
    queue = [i for i, deg in enumerate(remaining_in) if deg == 0]
    processed = [False] * len(nodes)
    head = 0
    while head < len(queue):
        cur = queue[head]
        head += 1
        processed[cur] = True
        for nxt in outgoing[cur]:
            rank[nxt] = max(rank[nxt], rank[cur] + 1)
            remaining_in[nxt] -= 1
            if remaining_in[nxt] == 0:
                queue.append(nxt)

    max_resolved = max((rank[i] for i, done in enumerate(processed) if done), default=0)
    for i, done in enumerate(processed):
        if not done:
            rank[i] = max_resolved + 1

    by_rank: Dict[int, List[int]] = {}
    for i, r in enumerate(rank):
        by_rank.setdefault(r, []).append(i)

    sorted_ranks = sorted(by_rank)
    max_rows = max(len(by_rank[r]) for r in sorted_ranks)
    total_height = max_rows * (NODE_H + GAP_Y) - GAP_Y + PADDING * 2
    total_width = PADDING * 2 + len(sorted_ranks) * NODE_W + max(0, len(sorted_ranks) - 1) * GAP_X

    positioned: List[Dict[str, Any] | None] = [None] * len(nodes)
    for rank_pos, r in enumerate(sorted_ranks):
        idxs = by_rank[r]
        rank_height = len(idxs) * (NODE_H + GAP_Y) - GAP_Y
        y_offset = PADDING + (total_height - PADDING * 2 - rank_height) / 2
        for row, node_idx in enumerate(idxs):
            positioned[node_idx] = {
                **nodes[node_idx],
                "x": PADDING + rank_pos * (NODE_W + GAP_X),
                "y": y_offset + row * (NODE_H + GAP_Y),
            }

    return [n for n in positioned if n is not None], total_width, total_height


def _wrap_text(text: str, max_chars: int, max_lines: int) -> List[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break
    if len(lines) < max_lines:
        if len(current) > max_chars:
            current = current[: max_chars - 1] + "…"
        lines.append(current)
    return lines[:max_lines]


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(7, size)
    for path in (_FONT_BOLD if bold else _FONT_REGULAR):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_edge(
    draw: ImageDraw.ImageDraw,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: str,
    width: int,
) -> None:
    mid_x = (x1 + x2) / 2
    draw.line([(x1, y1), (mid_x, y1), (mid_x, y2), (x2, y2)], fill=color, width=width)
    # Arrow head
    draw.polygon(
        [(x2, y2), (x2 - 8, y2 - 4), (x2 - 8, y2 + 4)],
        fill=color,
    )


def _draw_node(
    draw: ImageDraw.ImageDraw,
    node: Dict[str, Any],
    scale: float,
) -> None:
    category = node.get("category", "secondary")
    style = _CATEGORY_STYLE.get(category, _CATEGORY_STYLE["secondary"])
    x = node["x"] * scale
    y = node["y"] * scale
    w = NODE_W * scale
    h = NODE_H * scale
    r = max(4, int(8 * scale))

    draw.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=r,
        fill=style["face"],
        outline=style["edge"],
        width=max(1, int(2 * scale)),
    )

    pad = 8 * scale
    ty = y + 12 * scale
    title_font = _load_font(int(11 * scale), bold=True)
    body_font = _load_font(int(9 * scale))
    small_font = _load_font(int(8 * scale))

    for line in _wrap_text(str(node.get("title", "")), 28, 2):
        draw.text((x + pad, ty), line, fill=style["text"], font=title_font)
        ty += 12 * scale

    date = str(node.get("date", "")).strip()
    if date and ty < y + h - 16 * scale:
        draw.text((x + pad, ty), date[:28], fill="#64748b", font=small_font)
        ty += 10 * scale

    for line in _wrap_text(str(node.get("short", "")), 32, 2):
        if ty >= y + h - 10 * scale:
            break
        draw.text((x + pad, ty), line, fill="#475569", font=body_font)
        ty += 9 * scale

    badge = str(node.get("badge", "")).strip()
    if badge:
        bx = x + w - 12 * scale
        by = y + 8 * scale
        br = max(7, int(9 * scale))
        draw.ellipse([bx - br, by - br, bx + br, by + br], fill=style["edge"])
        badge_font = _load_font(int(8 * scale), bold=True)
        bbox = draw.textbbox((0, 0), badge, font=badge_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((bx - tw / 2, by - th / 2 - 1), badge, fill="white", font=badge_font)


def render_causal_dag_png(
    dag: Dict[str, Any],
    *,
    max_width_in: float = 6.5,
    max_height_in: float = 8.0,
) -> bytes | None:
    """Return PNG bytes for a causal DAG, or None if unusable."""
    positioned, width, height = _compute_layout(dag)
    if not positioned or width <= 0 or height <= 0:
        return None

    dpi = 150
    max_w = int(max_width_in * dpi)
    max_h = int(max_height_in * dpi)
    scale = min(max_w / width, max_h / height, 1.0)
    out_w = max(1, int(round(width * scale)))
    out_h = max(1, int(round(height * scale)))

    img = Image.new("RGB", (out_w, out_h), "white")
    draw = ImageDraw.Draw(img)
    id_to_node = {n["id"]: n for n in positioned}

    for edge in dag.get("edges") or []:
        src = id_to_node.get(edge.get("from"))
        dst = id_to_node.get(edge.get("to"))
        if not src or not dst:
            continue
        x1 = (src["x"] + NODE_W) * scale
        y1 = (src["y"] + NODE_H / 2) * scale
        x2 = dst["x"] * scale
        y2 = (dst["y"] + NODE_H / 2) * scale
        kind = edge.get("kind", "secondary")
        color = _EDGE_COLOR.get(kind, _EDGE_COLOR["secondary"])
        lw = max(1, int((2 if kind == "primary" else 1) * scale))
        _draw_edge(draw, x1, y1, x2, y2, color, lw)

    for node in positioned:
        _draw_node(draw, node, scale)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
