# Offline generator harness

Run the TopoView generator (topology → SVG + Grafana dashboard) **without a cluster**, against
static CR JSON. Useful for validating layout changes on any fabric shape.

```bash
# 1. build the synthetic fabric fixtures (also writes the ns1/ns2/ns3 *.json here)
python3 synth_topos.py

# 2. run the full pipeline for a fixture and validate + emit <ns>.svg / topo-<ns>.json
python3 run_local.py ns2          # or: test1 | ns1 | ns3

# 3. (optional) render the generated SVG to PNG — needs playwright + chromium
python3 render.py ns2.svg ns2.png
```

`run_local.py` imports the real controller modules from `../../topoview/build/controller/`, so what
you see here is exactly what the running app produces.

Fixtures (EDA `TopoNode` / `TopoLink` / `Fabric` CRs, in the shape EDA emits):

| Fixture | Fabric |
|---|---|
| `test1` | 2 spine / 4 leaf, dual-homed edge |
| `ns1`   | 2 border-leaf / 2 spine / 4 leaf, 1 host per leaf |
| `ns2`   | 4 spine / 8 leaf, 1 host per leaf |
| `ns3`   | 1 border-leaf / 2 spine / 5 leaf, 2 hosts per leaf |
