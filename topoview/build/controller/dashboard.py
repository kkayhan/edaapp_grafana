"""
Assemble Grafana dashboards.

  build_topology_dashboard: wrap the flow-panel reference (assets/flowpanel.ref.json)
    into a full dashboard, inline the generated SVG + panelConfig, pin $namespace to
    the target namespace. One dashboard per fabric namespace, tag 'topoview'.
  build_menu_dashboard: the read-only landing/home dashboard -- a 'dashlist' panel
    that auto-lists every topoview-tagged dashboard, so the user picks a namespace.

Datasource uid defaults to Grafana's deterministic provisioning uid for a
datasource named 'Prometheus' (PBFA97CFB590B2093); overridable via cfg.
"""
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FLOW_REF = json.load(open(os.path.join(HERE, "assets", "flowpanel.ref.json")))

TAG = "topoview"
DEFAULT_PROM_UID = "PBFA97CFB590B2093"


def _set_ds_uid(panel, uid):
    if isinstance(panel.get("datasource"), dict):
        panel["datasource"]["uid"] = uid
    for t in panel.get("targets", []):
        if isinstance(t.get("datasource"), dict):
            t["datasource"]["uid"] = uid


def build_topology_dashboard(svg, panelconfig, namespace, fabric_names=None,
                             uid=None, refresh="5s", prom_uid=None):
    prom_uid = prom_uid or DEFAULT_PROM_UID
    uid = uid or f"topo-{namespace}"
    fab = ", ".join(fabric_names or [])
    title = f"Namespace: {namespace}" + (f"   Fabric: {fab}" if fab else "")

    flow = copy.deepcopy(FLOW_REF)
    flow["id"] = 2
    flow["gridPos"] = {"x": 0, "y": 0, "w": 24, "h": 22}
    flow["title"] = ""                 # title lives inside the SVG; keep the panel chrome clean
    o = flow["options"]
    o["svg"] = svg
    o["panelConfig"] = panelconfig
    o["panZoomEnabled"] = True
    _set_ds_uid(flow, prom_uid)

    ns_var = {
        "name": "namespace", "type": "constant", "query": namespace,
        "current": {"text": namespace, "value": namespace, "selected": True},
        "options": [{"text": namespace, "value": namespace, "selected": True}],
        "hide": 2, "skipUrlSync": False,
    }

    return {
        "uid": uid,
        "title": title,
        "tags": [TAG],
        "editable": False,
        "schemaVersion": 41,
        "version": 0,
        "refresh": refresh,
        "time": {"from": "now-15m", "to": "now"},
        "timepicker": {},
        "templating": {"list": [ns_var]},
        "annotations": {"list": []},
        "panels": [flow],
    }


def build_menu_dashboard(uid="topoview-menu", title="TopoView — fabric dashboards"):
    header = {
        "id": 1, "type": "text", "title": "",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 3},
        "options": {"mode": "markdown", "content":
                    "# EDA Fabric Topology\nSelect a namespace below to open its live "
                    "topology dashboard. Link colours and throughput update automatically."},
        "transparent": True,
    }
    dashlist = {
        "id": 2, "type": "dashlist", "title": "Fabric namespaces",
        "gridPos": {"x": 0, "y": 3, "w": 24, "h": 20},
        "options": {
            "showHeadings": False, "showSearch": True, "showRecentlyViewed": False,
            "showStarred": False, "showFolderNames": False,
            "tags": [TAG], "maxItems": 100, "query": "",
        },
    }
    return {
        "uid": uid,
        "title": title,
        "tags": ["topoview-menu"],
        "editable": False,
        "schemaVersion": 41,
        "version": 0,
        "refresh": "",
        "time": {"from": "now-15m", "to": "now"},
        "templating": {"list": []},
        "annotations": {"list": []},
        "panels": [header, dashlist],
    }
