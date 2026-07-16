"""
EQL-direct telemetry for the flow panel (replaces the Prometheus exporter path).

Runs two EQL queries per namespace against eda-api and reshapes the flat result
tables into the exact series names the flow panel's panelConfig dataRefs expect:

  "<node>:<if>:out"          egress bps    (link stroke colour + rate pill)
  "<node>:<if>:in"           ingress bps   (edge-interface cards)
  "oper-state:<node>:<if>"   1 up / 0 down (port dot + edge-card colour)

Output shape = a single-row wide table: [ { "<series>": <value>, ... } ]. Served
as JSON to Grafana's Infinity datasource, which turns each key into one series
named exactly the dataRef -- no Grafana transforms needed.

GOTCHA: the two queries key the interface name differently --
  traffic-rate row -> ".namespace.node.srl.interface.name"
  interface row    -> "name"
"""
import logging
from urllib.parse import urlencode

import auth

logger = logging.getLogger("eql")

_TRAFFIC = ('.namespace.node.srl.interface.traffic-rate fields [in-bps, out-bps] '
            'where (.namespace.name = "{ns}")')
_OPER = ('.namespace.node.srl.interface fields [oper-state] '
         'where (.namespace.name = "{ns}")')

_NODE_KEY = ".namespace.node.name"
_IF_TRAFFIC_KEY = ".namespace.node.srl.interface.name"


def _query(expr, ns):
    qs = urlencode({"query": expr, "namespaces": ns})
    resp = auth.eda_api_get(f"/core/query/v1/eql?{qs}")
    return (resp or {}).get("data", []) or []


def fetch_wide(ns):
    """Return [wide_dict] for a namespace, or [{}] on empty. Raises on auth/HTTP
    error so the caller can surface it (served responses fall back to last-good)."""
    wide = {}
    for r in _query(_TRAFFIC.format(ns=ns), ns):
        node = r.get(_NODE_KEY)
        iface = r.get(_IF_TRAFFIC_KEY)
        if not node or not iface:
            continue
        wide[f"{node}:{iface}:out"] = int(r.get("out-bps") or 0)
        wide[f"{node}:{iface}:in"] = int(r.get("in-bps") or 0)
    for r in _query(_OPER.format(ns=ns), ns):
        node = r.get(_NODE_KEY)
        iface = r.get("name")
        if not node or not iface:
            continue
        wide[f"oper-state:{node}:{iface}"] = 1 if r.get("oper-state") == "up" else 0
    return [wide]
