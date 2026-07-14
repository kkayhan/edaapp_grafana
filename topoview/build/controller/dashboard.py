"""
Assemble Grafana dashboards.

  build_topology_dashboard: wrap the flow-panel reference (assets/flowpanel.ref.json)
    into a full dashboard, inline the generated SVG + panelConfig, pin $namespace to
    the target namespace. One dashboard per fabric namespace, tag 'topoview'.
  build_menu_dashboard: the read-only landing/home dashboard -- a 'dashlist' panel
    that auto-lists every topoview-tagged dashboard, so the user picks a namespace.

EQL-direct data path: the flow panel's single target is an Infinity (JSON/URL)
query against the TopoView controller's own /eql/<ns>.json endpoint, which serves
the live per-interface series (out/in bps + oper-state) straight from EQL. No
Prometheus, no transforms -- Infinity turns each JSON key into one series named
exactly the panelConfig dataRef. Datasource uid is the provisioned Infinity uid.
"""
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FLOW_REF = json.load(open(os.path.join(HERE, "assets", "flowpanel.ref.json")))

TAG = "topoview"
# uid of the provisioned Infinity datasource (20-grafana-config.yaml).
DS_UID = "eda-eql-infinity"
# The controller serves reshaped EQL here; Grafana resolves ${namespace} per dashboard.
DATA_URL = os.environ.get(
    "TOPOVIEW_DATA_URL",
    "http://eda-topoview.eda-system.svc.cluster.local:8080/eql/${namespace}.json")


def _infinity_target():
    return {
        "refId": "A",
        "datasource": {"type": "yesoreyeram-infinity-datasource", "uid": DS_UID},
        "type": "json", "source": "url", "format": "table", "parser": "backend",
        "root_selector": "", "columns": [],
        "url": DATA_URL, "url_options": {"method": "GET"},
    }


def build_topology_dashboard(svg, panelconfig, namespace, fabric_names=None,
                             uid=None, refresh="5s", prom_uid=None):
    uid = uid or f"topo-{namespace}"
    fab = ", ".join(fabric_names or [])
    title = f"Namespace: {namespace}" + (f"   Fabric: {fab}" if fab else "")

    flow = copy.deepcopy(FLOW_REF)
    flow["id"] = 2
    flow["gridPos"] = {"x": 0, "y": 0, "w": 24, "h": 22}
    flow["title"] = ""                 # title lives inside the SVG; keep the panel chrome clean
    flow["datasource"] = {"type": "yesoreyeram-infinity-datasource", "uid": DS_UID}
    flow["targets"] = [_infinity_target()]
    o = flow["options"]
    o["svg"] = svg
    o["panelConfig"] = panelconfig
    o["panZoomEnabled"] = True

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
