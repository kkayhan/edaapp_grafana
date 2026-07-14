"""
Auto-layout for switches only (hosts are not drawn). Layered by role tier
(border-leaf top -> spine -> leaf bottom); horizontal order minimises crossings
(barycenter sweeps); coordinates centered + evenly spaced; inter-switch link
endpoints get port fan-out. Below each switch that has edge (host-facing) ports,
vertical space is reserved for its edge-interface list (rendered by svggen).

Returns:
  { "W","H",
    "nodes": { name: {"cx","cy","size","row"} },
    "endpoints": { edge_index: (x1,y1,x2,y2) },
    "card": {"top_gap","row_h"} }
"""
import re

MARGIN_X = 90
MARGIN_TOP = 120
MARGIN_BOTTOM = 40
ROW_GAP = 210
COL_PITCH = 190
SIZE_SWITCH = 70
MIN_W = 1200
PORT_PITCH_MAX = 16

# edge-interface list (drawn below a switch by svggen)
EDGE_CARD_TOP_GAP = 30      # gap below the switch name label to the first row
EDGE_CARD_ROW_H = 34        # per interface: name line + in/out line
EDGE_CARD_BOTTOM_PAD = 14


def _natkey(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def compute_layout(model):
    nodes = model["nodes"]
    edges = model["edges"]
    edge_ifaces = model.get("edge_ifaces", {})

    tiers = sorted({n["tier"] for n in nodes.values()})
    tier_to_row = {t: r for r, t in enumerate(tiers)}
    rows = {}
    for name, n in nodes.items():
        rows.setdefault(tier_to_row[n["tier"]], []).append(name)
    R = len(rows)

    adj = {name: [] for name in nodes}
    for e in edges:
        adj[e["a"]].append(e["b"])
        adj[e["b"]].append(e["a"])

    order = {r: sorted(rows[r], key=_natkey) for r in rows}

    def barycenter(row, ref_row):
        ref_pos = {name: i for i, name in enumerate(order[ref_row])}
        cur = {name: i for i, name in enumerate(order[row])}

        def key(name):
            refs = [ref_pos[m] for m in adj[name] if m in ref_pos]
            return sum(refs) / len(refs) if refs else cur[name]
        order[row] = sorted(order[row], key=key)

    for _ in range(4):
        for r in range(1, R):
            barycenter(r, r - 1)
        for r in range(R - 2, -1, -1):
            barycenter(r, r + 1)

    maxN = max((len(v) for v in order.values()), default=1)
    content_w = maxN * COL_PITCH
    W = max(MIN_W, 2 * MARGIN_X + content_w)
    centerpad = (W - 2 * MARGIN_X - content_w) / 2.0

    pos = {}
    for r in range(R):
        row_nodes = order[r]
        spacing = content_w / len(row_nodes)
        cy = MARGIN_TOP + r * ROW_GAP
        for i, name in enumerate(row_nodes):
            cx = MARGIN_X + centerpad + spacing * (i + 0.5)
            pos[name] = {"cx": cx, "cy": cy, "size": SIZE_SWITCH, "row": r}

    # canvas height: bottom row + tallest edge-interface card below it
    bottom_row_cy = MARGIN_TOP + (R - 1) * ROW_GAP
    max_card = 0
    for name in nodes:
        n_if = len(edge_ifaces.get(name, []))
        if n_if:
            max_card = max(max_card, EDGE_CARD_TOP_GAP + n_if * EDGE_CARD_ROW_H + EDGE_CARD_BOTTOM_PAD)
    H = int(bottom_row_cy + SIZE_SWITCH / 2 + max_card + MARGIN_BOTTOM)

    # port fan-out for inter-switch links
    incident = {name: [] for name in nodes}
    for idx, e in enumerate(edges):
        incident[e["a"]].append((idx, e["b"]))
        incident[e["b"]].append((idx, e["a"]))
    attach = {}
    for name, inc in incident.items():
        me = pos[name]
        inc_sorted = sorted(inc, key=lambda t: pos[t[1]]["cx"])
        deg = len(inc_sorted)
        span = min(PORT_PITCH_MAX, (me["size"] - 10) / max(1, deg - 1)) if deg > 1 else 0
        for j, (idx, other) in enumerate(inc_sorted):
            dy = pos[other]["cy"] - me["cy"]
            sign = 1 if dy > 0 else (-1 if dy < 0 else 0)
            ax = me["cx"] + (j - (deg - 1) / 2.0) * span
            ay = me["cy"] + sign * me["size"] / 2.0
            attach[(name, idx)] = (ax, ay)

    endpoints = {}
    for idx, e in enumerate(edges):
        x1, y1 = attach[(e["a"], idx)]
        x2, y2 = attach[(e["b"], idx)]
        endpoints[idx] = (x1, y1, x2, y2)

    return {"W": W, "H": H, "nodes": pos, "endpoints": endpoints,
            "card": {"top_gap": EDGE_CARD_TOP_GAP, "row_h": EDGE_CARD_ROW_H}}
