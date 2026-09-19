# Offline generator harness

Run the Grafana generator (topology → SVG + Grafana dashboard) **without a cluster**, against
static CR JSON. Useful for validating layout changes on any fabric shape.

```bash
# 1. build the synthetic fabric fixtures (also writes the ns1/ns2/ns3 *.json here)
python3 synth_topos.py

# 2. run the full pipeline for a fixture and validate + emit <ns>.svg / topo-<ns>.json
python3 run_local.py ns2          # or: test1 | ns1 | ns3

# 3. (optional) render the generated SVG to PNG — needs playwright + chromium
python3 render.py ns2.svg ns2.png
```

`run_local.py` imports the real controller modules from `../../grafana/build/controller/`, so what
you see here is exactly what the running app produces.

Fixtures (EDA `TopoNode` / `TopoLink` / `Fabric` / `Interface` CRs, in the shape EDA emits):

| Fixture | Fabric | Also exercises |
|---|---|---|
| `test1` | 2 spine / 4 leaf, dual-homed edge | |
| `ns1`   | 2 border-leaf / 2 spine / 4 leaf, 1 host per leaf | border-leaf **side** edge grids + **server LAGs** (see below) |
| `ns2`   | 4 spine / 8 leaf, 1 host per leaf | large-fabric scaling |
| `ns3`   | 1 border-leaf / 2 spine / 5 leaf, 2 hosts per leaf | multiple hosts per leaf |

**LAG coverage (`ns1`).** `<ns>-interfaces.json` holds server-facing MC-LAGs as EDA `Interface` CRs
(`spec.type: lag`, dual-homed on the first two leaves, **no TopoLink and no role label** — exactly how
a real fabric defines them). `ns1` ships 12: ten active (two of them `degraded`) and two `down`. Use it
to check the discovery rules in the main README: down LAGs must be **hidden**, active ones listed under
*both* member leaves as `lagN`, and telemetry keyed on the physical member port. Namespaces without an
`-interfaces.json` simply render as before, which is the no-LAG regression case.

`run_local.py` prints each card as `label(iface)` so you can see the LAG-name/physical-port split, and
fails loudly if any panelConfig cell has no matching `cell-*` id in the SVG.
