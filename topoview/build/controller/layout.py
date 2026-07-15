"""
Auto-layout for switches only (hosts are not drawn). Layered by role tier
(border-leaf top -> spine -> leaf bottom); horizontal order minimises crossings
(barycenter sweeps); coordinates centered + evenly spaced; inter-switch link
endpoints get port fan-out.

Edge (host-facing) interface lists differ by role:
  * leaf   -> single column BELOW the switch (grows down, clear of its upward links).
             Vertical space is reserved below the bottom row for it.
  * others -> a grid to the SIDE of the switch (border-leaf/spine), max ROWS_MAX rows,
             growing into columns, so it never overlaps the switch's downward links.
             Horizontal space is reserved by shifting/widening the canvas.

Returns:
  { "W","H",
    "nodes": { name: {"cx","cy","size","row"} },
    "endpoints": { edge_index: (x1,y1,x2,y2) },
    "card": {"top_gap","row_h"},
    "cards_meta": { name: {"orient":"below"} |
                          {"orient":"side","side","cols","rows","rows_max",
                           "box_x","card_top","col_w","row_h"} } }
"""
import re

from topology import normalize_role

MARGIN_X = 90
MARGIN_TOP = 120
MARGIN_BOTTOM = 40
ROW_GAP = 210
COL_PITCH = 190
SIZE_SWITCH = 70
MIN_W = 1200
PORT_PITCH_MAX = 16

# edge-interface list (drawn below a leaf switch by svggen)
EDGE_CARD_TOP_GAP = 30      # gap below the switch name label to the first row
EDGE_CARD_ROW_H = 34        # per interface: name line + in/out line
EDGE_CARD_BOTTOM_PAD = 14

# side edge-interface grid (border-leaf/spine) — drawn beside the switch by svggen
ROWS_MAX = 3                # at most this many rows per column before adding a column
SIDE_COL_W = 156            # per-column width (matches svggen CARD_W)
SIDE_ROW_H = 34             # per-row height (matches EDGE_CARD_ROW_H)
SIDE_GAP = 20              # gap from the switch icon edge to its side card
SIDE_MARGIN = 24           # keep side cards at least this far inside the canvas edges


def _natkey(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def compute_layout(model):
    nodes = model["nodes"]
    edges = model["edges"]
    edge_ifaces = model.get("edge_ifaces", {})
    # NB: model[edges] is already renderable-only (build_topology drops edges whose
    # endpoints have no TopoNode). Do NOT filter it here -- layout[endpoints] is keyed
    # by index into this same list and svggen re-enumerates it, so any divergence
    # (filter here, full list there) desyncs the indices -> KeyError in svggen.

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

    # ---- edge-interface card placement ----
    # leaves list edge ports in a single column BELOW; border-leaves/spines list them
    # in a max-ROWS_MAX-row grid to the SIDE (outer left/right half) so the card never
    # overlaps the switch's downward inter-switch links.
    center_x = W / 2.0
    cards_meta = {}
    for name, n in nodes.items():
        ifaces = edge_ifaces.get(name, [])
        if not ifaces:
            continue
        if normalize_role(n["role"]) == "leaf":
            cards_meta[name] = {"orient": "below"}
            continue
        n_if = len(ifaces)
        cols = (n_if + ROWS_MAX - 1) // ROWS_MAX
        rows = min(n_if, ROWS_MAX)
        p = pos[name]
        half = p["size"] / 2.0
        side = "left" if p["cx"] < center_x else "right"
        w = cols * SIDE_COL_W
        box_x = (p["cx"] - half - SIDE_GAP - w) if side == "left" else (p["cx"] + half + SIDE_GAP)
        cards_meta[name] = {
            "orient": "side", "side": side, "cols": cols, "rows": rows, "rows_max": ROWS_MAX,
            "box_x": box_x, "card_top": p["cy"] + 16 - (rows * SIDE_ROW_H + 18) / 2.0,
            "col_w": SIDE_COL_W, "row_h": SIDE_ROW_H,
        }

    # reserve horizontal room for the side cards: shift everything right if a left card
    # would spill past the margin; widen the canvas for any right spill.
    gmin = gmax = None
    for m in cards_meta.values():
        if m.get("orient") != "side":
            continue
        x0, x1 = m["box_x"], m["box_x"] + m["cols"] * SIDE_COL_W
        gmin = x0 if gmin is None else min(gmin, x0)
        gmax = x1 if gmax is None else max(gmax, x1)
    if gmin is not None:
        shift = max(0.0, SIDE_MARGIN - gmin)
        if shift:
            for p in pos.values():
                p["cx"] += shift
            for m in cards_meta.values():
                if m.get("orient") == "side":
                    m["box_x"] += shift
            gmax += shift
        W = int(max(W + shift, gmax + SIDE_MARGIN))

    # canvas height: bottom row + tallest BELOW edge-interface card (side cards sit
    # beside upper rows, already bounded by the row gaps, so they don't add height).
    bottom_row_cy = MARGIN_TOP + (R - 1) * ROW_GAP
    max_card = 0
    for name in nodes:
        if cards_meta.get(name, {}).get("orient") != "below":
            continue
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
            "card": {"top_gap": EDGE_CARD_TOP_GAP, "row_h": EDGE_CARD_ROW_H},
            "cards_meta": cards_meta}
