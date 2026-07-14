"""
Render the switch topology + edge-interface lists into a light-theme SVG plus the
andrewbmchugh-flow-panel panelConfig YAML.

Switches (border-leaf/spine/leaf) and their inter-switch links are drawn with the
dual-half directional link (line + arrow + live rate pill). Hosts are NOT drawn;
instead each switch's host-facing (edge) interfaces are LISTED beneath it, each
showing the interface name (coloured by egress rate) plus live in/out bps -- no
arrows.

cell-id convention (cellIdPreamble 'cell-'):
  cell-link_id:A:AIF:B:BIF   inter-switch traffic  -> strokeColor + rate label
  cell-mid:A:AIF:B:BIF       midpoint anchor
  cell-A:AIF:B:BIF           inter-switch port dot -> fillColor (oper-state)
  cell-edgename:N:IF         edge interface name   -> fillColor (traffic), no label
  cell-edgein:N:IF           edge ingress value    -> fillColor (traffic) + bps label
  cell-edgeout:N:IF          edge egress value     -> fillColor (traffic) + bps label
"""
import math
import os
import re

from topology import normalize_role

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
ICON_SWITCH = open(os.path.join(ASSETS, "icon_switch.svg")).read()

ROLE_FILL = {
    "borderleaf": "#0D9488", "superspine": "#1E293B",
    "spine": "#334155", "leaf": "#475569",
}
DEFAULT_FILL = "#475569"

DEFAULT_THRESHOLDS = {
    "operState": [{"color": "#FF3154", "level": 0}, {"color": "#4BDD33", "level": 1}],
    "traffic": [
        {"color": "#94a3b8", "level": 0},
        {"color": "#4BDD33", "level": 1000},
        {"color": "#FFFF00", "level": 500000},
        {"color": "#FF8000", "level": 2000000},
        {"color": "#FF3154", "level": 8000000},
    ],
}

CARD_W = 156
CARD_FILL, CARD_STROKE = "#f8fafc", "#e2e8f0"

# rate-pill placement: anchor each direction's label a staggered distance from its
# OWN source port along the link, so many links leaving one switch don't stack their
# labels in a single band (dense Clos fabrics). k = source's running label index.
LABEL_BASE = 46
LABEL_STEP = 15


def _label_xy(sx, sy, mx, my, k):
    """Point k*STEP+BASE px from source (sx,sy) toward the link midpoint (mx,my),
    capped short of the midpoint so it stays on the source half. The per-source k
    stagger fans labels out in depth so many links leaving one switch don't overlap."""
    dx, dy = mx - sx, my - sy
    half = math.hypot(dx, dy) or 1.0
    d = min(LABEL_BASE + k * LABEL_STEP, 0.86 * half)
    return sx + dx / half * d, sy + dy / half * d

MID_ELLIPSE = ('<g id="cell-mid:{A}:{AIF}:{B}:{BIF}" data-label=""><ellipse cx="{MX:.1f}" cy="{MY:.1f}" '
               'rx="2.5" ry="2.5" fill="#94a3b8" stroke="none"/></g>')
HALF_LINK = ('<g id="cell-link_id:{A}:{AIF}:{B}:{BIF}" data-label="rate" data-source="{A}:{AIF}:{B}:{BIF}" '
             'data-target="mid:{MA}:{MAIF}:{MB}:{MBIF}">'
             '<path d="M {X1:.1f} {Y1:.1f} L {X2:.1f} {Y2:.1f}" fill="none" stroke="#cbd5e1" stroke-width="4" '
             'stroke-linecap="round" pointer-events="stroke"/>'
             '<path d="{ARROW}" fill="#cbd5e1" stroke="#cbd5e1" stroke-width="1"/>'
             '<g transform="translate(-0.5 -0.5)"><switch><foreignObject style="overflow: visible; text-align: left;" '
             'pointer-events="none" width="100%" height="100%" requiredFeatures="http://www.w3.org/TR/SVG11/feature#Extensibility">'
             '<div xmlns="http://www.w3.org/1999/xhtml" style="display: flex; align-items: unsafe center; justify-content: unsafe center; '
             'width: 1px; height: 1px; padding-top: {LY:.0f}px; margin-left: {LX:.0f}px;">'
             '<div style="box-sizing: border-box; font-size: 0; text-align: center;">'
             '<div style="display: inline-block; font-size: 10px; font-weight: 600; color: #0f172a; line-height: 1.2; '
             'pointer-events: all; background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 3px; '
             'padding: 0px 3px; white-space: nowrap;">rate</div></div></div></foreignObject></switch></g></g>')
PORT_FILL = ('<g id="cell-{A}:{AIF}:{B}:{BIF}"><ellipse cx="{X:.1f}" cy="{Y:.1f}" rx="6" ry="6" '
             'fill="#94a3b8" stroke="#475569" stroke-width="1"/></g>')


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _place_icon(template, uid, fill, cx, cy, size):
    icon = template
    old = re.search(r'id="(svg-image-[^"]+)"', icon).group(1)
    icon = icon.replace(old, uid)
    icon = re.sub(r'(\.st0 \{ fill: )[^;]+;', r'\g<1>%s;' % fill, icon, count=1)
    op = icon.find('>', icon.find('<svg version="1.1"'))
    head, rest = icon[:op + 1], icon[op + 1:]
    x, y = cx - size / 2, cy - size / 2
    head = re.sub(r' x="[^"]*"', f' x="{x:.1f}"', head, count=1)
    head = re.sub(r' y="[^"]*"', f' y="{y:.1f}"', head, count=1)
    head = re.sub(r' width="[^"]*"', f' width="{size:.1f}"', head, count=1)
    head = re.sub(r' height="[^"]*"', f' height="{size:.1f}"', head, count=1)
    return head + rest


def _text(x, y, s, size=13, fill="#0f172a", weight="600", anchor="middle"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="Inter,Segoe UI,Helvetica,Arial,sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{_esc(s)}</text>')


def _cell_text(cid, x, y, s, size=10, fill="#334155", weight="700", anchor="start"):
    return (f'<text id="cell-{cid}" x="{x:.1f}" y="{y:.1f}" '
            f'font-family="Inter,Segoe UI,Helvetica,Arial,sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{_esc(s)}</text>')


def _arrow_head(x1, y1, x2, y2, size=7):
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L == 0:
        return "", x2, y2
    ux, uy = dx / L, dy / L
    bx, by = x2 - ux * size, y2 - uy * size
    wx, wy = -uy, ux
    half = size * 0.55
    w1 = (bx + wx * half, by + wy * half)
    w2 = (bx - wx * half, by - wy * half)
    return (f"M {w1[0]:.1f} {w1[1]:.1f} L {x2:.1f} {y2:.1f} L {w2[0]:.1f} {w2[1]:.1f} Z", bx, by)


def _uid(name):
    return "svgimg-" + re.sub(r"[^A-Za-z0-9]", "-", name)


def generate(model, layout, namespace, thresholds=None, fabric_names=None):
    nodes = model["nodes"]
    edges = model["edges"]
    edge_ifaces = model.get("edge_ifaces", {})
    pos = layout["nodes"]
    endpoints = layout["endpoints"]
    W, H = layout["W"], layout["H"]
    card = layout["card"]
    th = thresholds or DEFAULT_THRESHOLDS

    links_svg, ports_svg, cards_svg, cells = [], [], [], []
    src_label_n = {}   # source node -> count of rate labels already anchored to it

    def make_link(a, aif, b, bif, x1, y1, x2, y2, fwd, rev, a_oper, b_oper):
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        links_svg.append(MID_ELLIPSE.format(A=a, AIF=aif, B=b, BIF=bif, MX=mx, MY=my))
        ka = src_label_n.get(a, 0); src_label_n[a] = ka + 1
        kb = src_label_n.get(b, 0); src_label_n[b] = kb + 1
        ar, ex, ey = _arrow_head(x1, y1, mx, my)
        lx, ly = _label_xy(x1, y1, mx, my, ka)
        links_svg.append(HALF_LINK.format(A=a, AIF=aif, B=b, BIF=bif, MA=a, MAIF=aif, MB=b, MBIF=bif,
                                          X1=x1, Y1=y1, X2=ex, Y2=ey, ARROW=ar, LX=lx, LY=ly))
        ar, ex, ey = _arrow_head(x2, y2, mx, my)
        lx, ly = _label_xy(x2, y2, mx, my, kb)
        links_svg.append(HALF_LINK.format(A=b, AIF=bif, B=a, BIF=aif, MA=a, MAIF=aif, MB=b, MBIF=bif,
                                          X1=x2, Y1=y2, X2=ex, Y2=ey, ARROW=ar, LX=lx, LY=ly))
        ports_svg.append(PORT_FILL.format(A=a, AIF=aif, B=b, BIF=bif, X=x1, Y=y1))
        ports_svg.append(PORT_FILL.format(A=b, AIF=bif, B=a, BIF=aif, X=x2, Y=y2))
        cells.append({"id": f"link_id:{a}:{aif}:{b}:{bif}", "dataRef": fwd, "kind": "link"})
        cells.append({"id": f"link_id:{b}:{bif}:{a}:{aif}", "dataRef": rev, "kind": "link"})
        cells.append({"id": f"{a}:{aif}:{b}:{bif}", "dataRef": a_oper, "kind": "oper"})
        cells.append({"id": f"{b}:{bif}:{a}:{aif}", "dataRef": b_oper, "kind": "oper"})

    for idx, e in enumerate(edges):
        x1, y1, x2, y2 = endpoints[idx]
        make_link(e["a"], e["aif"], e["b"], e["bif"], x1, y1, x2, y2,
                  e["fwd"], e["rev"], e["a_oper"], e["b_oper"])

    # edge-interface cards beneath each switch that has host-facing ports
    for name, ifaces in edge_ifaces.items():
        if name not in pos or not ifaces:
            continue
        p = pos[name]
        card_top = p["cy"] + p["size"] / 2 + card["top_gap"]
        box_h = len(ifaces) * card["row_h"] + 8
        cards_svg.append(f'<rect x="{p["cx"]-CARD_W/2:.1f}" y="{card_top-16:.1f}" width="{CARD_W}" '
                         f'height="{box_h+10:.1f}" rx="7" fill="{CARD_FILL}" stroke="{CARD_STROKE}" stroke-width="1"/>')
        for i, ei in enumerate(ifaces):
            yn = card_top + i * card["row_h"]
            yv = yn + 14
            n, iff = name, ei["iface"]
            key = f"{n}:{iff}"
            # interface name — colour by egress rate (no label -> text preserved)
            cards_svg.append(_cell_text(f"edgename:{key}", p["cx"], yn, iff, size=11,
                                        fill="#334155", weight="700", anchor="middle"))
            cells.append({"id": f"edgename:{key}", "dataRef": ei["out"], "kind": "edgename"})
            # in / out live values (label -> bps, colour by rate)
            cards_svg.append(_text(p["cx"] - CARD_W / 2 + 12, yv, "in", size=9, fill="#94a3b8",
                                   weight="600", anchor="start"))
            cards_svg.append(_cell_text(f"edgein:{key}", p["cx"] - CARD_W / 2 + 26, yv, "rate",
                                        size=10, anchor="start"))
            cards_svg.append(_text(p["cx"] + 8, yv, "out", size=9, fill="#94a3b8",
                                   weight="600", anchor="start"))
            cards_svg.append(_cell_text(f"edgeout:{key}", p["cx"] + 28, yv, "rate",
                                        size=10, anchor="start"))
            cells.append({"id": f"edgein:{key}", "dataRef": ei["in"], "kind": "edgeval"})
            cells.append({"id": f"edgeout:{key}", "dataRef": ei["out"], "kind": "edgeval"})

    # ---- assemble ----
    fab = ", ".join(fabric_names or model.get("fabrics", []) or [])
    title = f"Namespace: {namespace}" + (f"      Fabric: {fab}" if fab else "")

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="Inter,Segoe UI,Helvetica,Arial,sans-serif">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>',
           _text(W / 2, 38, title, size=20, fill="#0f172a", weight="800")]
    svg += links_svg
    for name, n in nodes.items():
        p = pos[name]
        fill = ROLE_FILL.get(normalize_role(n["role"]), DEFAULT_FILL)
        svg.append(_place_icon(ICON_SWITCH, _uid(name), fill, p["cx"], p["cy"], p["size"]))
        ly = p["cy"] - p["size"] / 2 - 8 if p["row"] == 0 else p["cy"] + p["size"] / 2 + 14
        svg.append(_text(p["cx"], ly, name, size=13, fill="#0f172a", weight="700"))
    svg += ports_svg
    svg += cards_svg
    # legend
    lx, ly = 40, H - 14
    svg.append(_text(lx, ly - 12, "rate:", size=11, fill="#475569", weight="700", anchor="start"))
    xx = lx + 34
    for col, lab in [("#94a3b8", "idle"), ("#4BDD33", "active"), ("#FFFF00", "busy"),
                     ("#FF8000", "high"), ("#FF3154", "hot / down")]:
        svg.append(f'<rect x="{xx}" y="{ly-9}" width="20" height="9" rx="2" fill="{col}" '
                   f'stroke="#475569" stroke-width="0.5"/>')
        svg.append(_text(xx + 24, ly - 1, lab, size=10, fill="#475569", weight="500", anchor="start"))
        xx += 24 + 12 + len(lab) * 6
    svg.append('</svg>')
    SVG = "\n".join(svg)

    # ---- panelConfig ----
    def yblk(items):
        return "\n".join(f'    - {{ color: "{i["color"]}", level: {i["level"]} }}' for i in items)

    pc = ["---", "anchors:",
          "  thresholds-operstate: &thresholds-operstate", yblk(th["operState"]),
          "  thresholds-traffic: &thresholds-traffic", yblk(th["traffic"]),
          "  label-config: &label-config",
          "    separator: replace", "    units: bps", "    decimalPoints: 0",
          "", "cellIdPreamble: cell-", "cells:"]
    seen = set()
    for c in cells:
        cid, dref, kind = c["id"], c["dataRef"], c["kind"]
        if cid in seen:
            continue
        seen.add(cid)
        if kind == "link":
            pc += [f"  {cid}:", f"    dataRef: {dref}", "    label: *label-config",
                   "    strokeColor:", "      thresholds: *thresholds-traffic"]
        elif kind == "oper":
            pc += [f"  {cid}:", f"    dataRef: {dref}", "    fillColor:",
                   "      thresholds: *thresholds-operstate"]
        elif kind == "edgename":
            pc += [f"  {cid}:", f"    dataRef: {dref}", "    fillColor:",
                   "      thresholds: *thresholds-traffic"]
        else:  # edgeval
            pc += [f"  {cid}:", f"    dataRef: {dref}", "    label: *label-config",
                   "    fillColor:", "      thresholds: *thresholds-traffic"]
    PANELCONFIG = "\n".join(pc) + "\n"
    return SVG, PANELCONFIG
