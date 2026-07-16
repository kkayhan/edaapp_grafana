#!/usr/bin/env python3
"""Local generator test: feed the live test1 CRs through the pipeline and validate."""
import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CTRL = os.path.join(HERE, "..", "..", "grafana", "build", "controller")
sys.path.insert(0, CTRL)

import topology
import layout as layout_mod
import svggen
import dashboard

NS = sys.argv[1] if len(sys.argv) > 1 else "test1"

tn = json.load(open(os.path.join(HERE, f"{NS}-toponodes.json")))["items"]
tl = json.load(open(os.path.join(HERE, f"{NS}-topolinks.json")))["items"]
try:
    fab = json.load(open(os.path.join(HERE, f"{NS}-fabrics.json")))["items"]
except FileNotFoundError:
    fab = []

model = topology.build_topology(tn, tl, fabrics=fab)
lay = layout_mod.compute_layout(model)
svg, pc = svggen.generate(model, lay, NS, fabric_names=model["fabrics"])
dash = dashboard.build_topology_dashboard(svg, pc, NS, fabric_names=model["fabrics"])
menu = dashboard.build_menu_dashboard()

print("edge_ifaces:", {k: [e["iface"] for e in v] for k, v in model["edge_ifaces"].items()})

print("=== nodes (name: role/tier -> row @ cx,cy) ===")
for name, n in sorted(model["nodes"].items(), key=lambda kv: (lay["nodes"][kv[0]]["row"], lay["nodes"][kv[0]]["cx"])):
    p = lay["nodes"][name]
    print(f"  row{p['row']} {name:14s} role={n['role']:11s} tier={n['tier']} metric={n['metric']} "
          f"cx={p['cx']:.0f} cy={p['cy']:.0f}")

print(f"\n=== edges ({len(model['edges'])}) ===")
for e in model["edges"]:
    print(f"  {e['kind']:6s} {e['a']}:{e['aif']} <-> {e['b']}:{e['bif']}   fwd={e['fwd']} rev={e['rev']}")

print(f"\ncanvas W={lay['W']} H={lay['H']}")
print("topology signature:", topology.topology_signature(model)[:16])

# --- validation ---
print("\n=== validation ===")
try:
    ET.fromstring(svg)
    print("  SVG XML: OK")
except Exception as ex:
    print("  SVG XML: FAIL ->", ex)
    sys.exit(1)

# every panelConfig cell key must exist as a cell-<key> id in the SVG
import re
pc_keys = re.findall(r"^  ([^ :][^:]*:[^\n]*?):\n", pc, re.M)
pc_keys = [k for k in pc_keys if not k.startswith("thresholds") and not k.startswith("label")]
missing = [k for k in pc_keys if f'id="cell-{k}"' not in svg]
print(f"  panelConfig cells: {len(pc_keys)}  missing-in-SVG: {len(missing)}")
if missing:
    print("   e.g.", missing[:5])
    sys.exit(1)

svg_cell_ids = set(re.findall(r'id="cell-([^"]+)"', svg))
print(f"  SVG cell ids: {len(svg_cell_ids)}")

# JSON round-trips
json.dumps(dash); json.dumps(menu)
print("  dashboard JSON: OK  size=%d bytes" % len(json.dumps(dash)))
print("  menu JSON: OK")

open(os.path.join(HERE, f"{NS}.svg"), "w").write(svg)
open(os.path.join(HERE, f"topo-{NS}.json"), "w").write(json.dumps(dash, indent=2))
open(os.path.join(HERE, f"panelConfig-{NS}.yaml"), "w").write(pc)
print(f"\nwrote {NS}.svg / topo-{NS}.json / panelConfig-{NS}.yaml")
