"""
EDA TopoView controller — entry point.

Long-running controller that, every RECONCILE_INTERVAL:
  * discovers every EDA namespace that has a fabric (>=1 TopoNode),
  * builds a topology model from TopoNode + TopoLink (+ Fabric role cross-check),
  * auto-lays it out (border-leaf top / spine middle / leaf bottom) and renders a
    light-theme andrewbmchugh-flow-panel dashboard,
  * pushes one dashboard per namespace (uid topo-<ns>, tag 'topoview') to the
    bundled Grafana via its HTTP API (admin basic-auth),
  * (re)creates the read-only 'topoview-menu' home dashboard that lists them.

Re-push happens only when a namespace's STRUCTURE changes (topology hash) or its
dashboard is missing from Grafana (self-heal). Live link colour/throughput is
served by the controller's own /eql/<ns>.json endpoint (Grafana Infinity polls
it), so an interface flap needs no re-push.

Topology reads use the pod ServiceAccount token (k8s.py). Live telemetry is read
straight from EQL via eda-api (auth.py password grant); NO Prometheus.
"""
import hashlib
import json
import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dashboard as dash_mod
import eql as eql_mod
import grafana
import k8s
import layout as layout_mod
import svggen
import topology as topo_mod

VERSION = "v26.4.3-1"

RECONCILE_INTERVAL = int(os.environ.get("RECONCILE_INTERVAL", "30"))
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://grafana.eda-system.svc.cluster.local:3000")
GRAFANA_USER = os.environ.get("GRAFANA_ADMIN_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_ADMIN_PASSWORD", "")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
POD_NAMESPACE = os.environ.get("POD_NAMESPACE", "eda-system")
# Live EQL responses are cached per-namespace for this many seconds: one eda-api
# poll per interval serves every Grafana viewer (mirrors Prometheus buffering).
DATA_TTL = float(os.environ.get("DATA_TTL", "4"))

# Grafana is fronted by the EDA HttpProxy named 'grafana'. Grafana's root URL must
# match the cluster's external address, which we auto-detect from EngineConfig
# (never hardcode -- every cluster differs).
GRAFANA_DEPLOY = "grafana"
GRAFANA_PROXY_PATH = "/core/httpproxy/v1/grafana/"

CRD_GROUP = "topoview.eda.edacommunity.com"
CRD_VERSION = "v1alpha1"
CRD_PLURAL = "topoviewconfigs"
CRD_KIND = "TopoViewConfig"
CRD_NAME = "default"

# never draw dashboards for infrastructure namespaces
SYSTEM_NS = {"eda-system", "eda-topoview", "kube-system"}

DEFAULTS = {
    "namespaceExclude": [],
    "refresh": "5s",
    "roleTiers": None,          # None -> topology.DEFAULT_ROLE_TIERS
    "thresholds": None,         # None -> svggen.DEFAULT_THRESHOLDS
}

logger = logging.getLogger("main")
shutdown_event = threading.Event()

_health = {"state": "starting", "time": None, "message": ""}
_last_status_hash = [None]
_gen_cache = {}   # namespace -> generation hash last pushed

# EQL response cache: namespace -> (epoch, wide_json_bytes). Guards eda-api from
# per-viewer query storms; last-good is served if a refresh errors.
_data_cache = {}
_data_lock = threading.Lock()


def _eql_json(ns):
    """Serve the reshaped live EQL series for a namespace (bytes), TTL-cached.
    Single-flight under a lock; on error, fall back to the last-good payload."""
    now = time.time()
    with _data_lock:
        hit = _data_cache.get(ns)
        if hit and now - hit[0] < DATA_TTL:
            return hit[1]
        try:
            body = json.dumps(eql_mod.fetch_wide(ns)).encode()
            _data_cache[ns] = (now, body)
            return body
        except Exception as e:
            logger.warning("EQL fetch for ns %s failed: %s", ns, e)
            return hit[1] if hit else b"[{}]"


def _setup_logging():
    fmt = logging.Formatter(fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                            datefmt="%Y-%m-%dT%H:%M:%SZ")
    fmt.converter = time.gmtime
    h = logging.StreamHandler()
    h.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(h)


def _signal_handler(signum, frame):
    logger.info("Received signal %d, shutting down", signum)
    shutdown_event.set()


# ---------------------------------------------------------------- healthz -----
class _HealthHandler(BaseHTTPRequestHandler):
    def _send(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            self._send(json.dumps(_health).encode())
            return
        # /eql/<ns>.json -> live per-interface series for Grafana's Infinity datasource
        if path.startswith("/eql/") and path.endswith(".json"):
            ns = path[len("/eql/"):-len(".json")]
            if ns:
                self._send(_eql_json(ns))
                return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


def _start_health_server():
    srv = ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    logger.info("Health server on :%d/healthz", HEALTH_PORT)


# ------------------------------------------------------------- config/CR ------
def _read_config():
    cfg = dict(DEFAULTS)
    try:
        cr = k8s.read_cr(CRD_GROUP, CRD_VERSION, CRD_PLURAL, CRD_NAME)
        if cr:
            spec = cr.get("spec", {}) or {}
            for key in DEFAULTS:
                if spec.get(key) not in (None, "", []):
                    cfg[key] = spec[key]
    except Exception as e:
        logger.warning("Failed to read %s/%s: %s", CRD_KIND, CRD_NAME, e)
    return cfg


def _ensure_default_cr():
    try:
        if k8s.read_cr(CRD_GROUP, CRD_VERSION, CRD_PLURAL, CRD_NAME):
            return
        body = {"apiVersion": f"{CRD_GROUP}/{CRD_VERSION}", "kind": CRD_KIND,
                "metadata": {"name": CRD_NAME}, "spec": {}}
        k8s.create_cr(CRD_GROUP, CRD_VERSION, CRD_PLURAL, body)
        logger.info("Created default %s CR", CRD_KIND)
    except Exception as e:
        logger.warning("Failed to ensure default %s: %s", CRD_KIND, e)


def _detect_root_url():
    """Build Grafana's external root URL from the cluster's EngineConfig
    (spec.cluster.external.domainName + httpsPort). Never hardcoded."""
    try:
        ec = k8s.read_namespaced_cr("core.eda.nokia.com", "v1", "eda-system",
                                    "engineconfigs", "engine-config")
        ext = ((((ec or {}).get("spec") or {}).get("cluster") or {}).get("external")) or {}
        host = ext.get("domainName") or ext.get("ipv4Address")
        if not host:
            return None
        port = str(ext.get("httpsPort", 443) or 443)
        hostport = host if port in ("443", "") else f"{host}:{port}"
        return f"https://{hostport}{GRAFANA_PROXY_PATH}"
    except Exception as e:
        logger.warning("Failed to read EngineConfig for host detection: %s", e)
        return None


def _ensure_grafana_root_url():
    """Point Grafana's GF_SERVER_ROOT_URL at the auto-detected external host.
    Patches (and rolls) the Grafana Deployment only when the value differs."""
    url = _detect_root_url()
    if not url:
        return
    try:
        dep = k8s.read_deployment(GRAFANA_DEPLOY, POD_NAMESPACE)
        if not dep:
            return
        containers = ((((dep.get("spec") or {}).get("template") or {}).get("spec")) or {}).get("containers") or []
        cur = None
        for c in containers:
            for e in (c.get("env") or []):
                if e.get("name") == "GF_SERVER_ROOT_URL":
                    cur = e.get("value")
        if cur == url:
            return
        patch = {"spec": {"template": {"spec": {"containers": [
            {"name": GRAFANA_DEPLOY, "env": [{"name": "GF_SERVER_ROOT_URL", "value": url}]}]}}}}
        k8s.patch_deployment(GRAFANA_DEPLOY, POD_NAMESPACE, patch)
        logger.info("Set Grafana root URL to %s (was %s)", url, cur)
    except Exception as e:
        logger.warning("Failed to set Grafana root URL: %s", e)


def _update_status(health, message, discovered, dashboards, menu_uid):
    try:
        cr = k8s.read_cr(CRD_GROUP, CRD_VERSION, CRD_PLURAL, CRD_NAME)
        if not cr:
            return
        status = {
            "health": health, "message": message,
            "discoveredNamespaces": sorted(discovered),
            "dashboards": dashboards,
            "menuDashboardUid": menu_uid,
            "version": VERSION,
        }
        sig = hashlib.sha256(json.dumps(status, sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()
        if sig == _last_status_hash[0]:
            return
        status["lastReconcileTime"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cr["status"] = status
        k8s.update_cr_status(CRD_GROUP, CRD_VERSION, CRD_PLURAL, CRD_NAME, cr)
        _last_status_hash[0] = sig
    except Exception as e:
        logger.warning("Failed to update status: %s", e)


# ------------------------------------------------------------- reconcile ------
def _by_ns(items):
    d = {}
    for it in items:
        d.setdefault(it["metadata"]["namespace"], []).append(it)
    return d


def _gen_hash(topo_sig, cfg):
    payload = json.dumps({
        "t": topo_sig, "refresh": cfg["refresh"],
        "roleTiers": cfg["roleTiers"], "thresholds": cfg["thresholds"],
        "v": "eql",   # data path marker: forces one re-push on the Prometheus->EQL cutover
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def reconcile(cfg):
    _ensure_grafana_root_url()   # auto-detect + apply the external host each cycle
    if not GRAFANA_PASSWORD:
        return "degraded", "GRAFANA_ADMIN_PASSWORD not set", set(), [], None
    if not grafana.health(GRAFANA_URL):
        return "degraded", f"Grafana not reachable at {GRAFANA_URL}", set(), [], None

    toponodes = k8s.list_cr_all_namespaces("core.eda.nokia.com", "v1", "toponodes")
    topolinks = k8s.list_cr_all_namespaces("core.eda.nokia.com", "v1", "topolinks")
    try:
        fabrics = k8s.list_cr_all_namespaces("fabrics.eda.nokia.com", "v1", "fabrics")
    except Exception:
        fabrics = []

    nodes_by = _by_ns(toponodes)
    links_by = _by_ns(topolinks)
    fabs_by = _by_ns(fabrics)

    exclude = set(cfg["namespaceExclude"]) | SYSTEM_NS
    discovered = [ns for ns in nodes_by if ns not in exclude and nodes_by[ns]]

    dashboards, errors = [], []
    for ns in sorted(discovered):
        try:
            model = topo_mod.build_topology(nodes_by.get(ns, []), links_by.get(ns, []),
                                            fabrics=fabs_by.get(ns, []),
                                            role_tiers=cfg["roleTiers"])
            if not model["nodes"]:
                continue
            uid = f"topo-{ns}"
            gh = _gen_hash(topo_mod.topology_signature(model), cfg)
            need = (_gen_cache.get(ns) != gh) or not grafana.dashboard_exists(
                GRAFANA_URL, uid, GRAFANA_USER, GRAFANA_PASSWORD)
            if need:
                lay = layout_mod.compute_layout(model)
                svg, pc = svggen.generate(model, lay, ns, thresholds=cfg["thresholds"],
                                          fabric_names=model["fabrics"])
                d = dash_mod.build_topology_dashboard(
                    svg, pc, ns, fabric_names=model["fabrics"], uid=uid,
                    refresh=cfg["refresh"])
                grafana.push_dashboard(GRAFANA_URL, d, GRAFANA_USER, GRAFANA_PASSWORD)
                _gen_cache[ns] = gh
                logger.info("Pushed dashboard %s (%d nodes, %d links)", uid,
                            len(model["nodes"]), len(model["edges"]))
            dashboards.append({"namespace": ns, "uid": uid,
                               "nodeCount": len(model["nodes"]),
                               "linkCount": len(model["edges"]),
                               "generationHash": gh})
        except Exception as e:
            logger.warning("Reconcile ns %s failed: %s", ns, e)
            errors.append(ns)

    # menu (self-heal only; dashlist auto-updates its contents by tag)
    menu = dash_mod.build_menu_dashboard()
    menu_uid = menu["uid"]
    try:
        if not grafana.dashboard_exists(GRAFANA_URL, menu_uid, GRAFANA_USER, GRAFANA_PASSWORD):
            grafana.push_dashboard(GRAFANA_URL, menu, GRAFANA_USER, GRAFANA_PASSWORD, "topoview menu")
            logger.info("Pushed menu dashboard %s", menu_uid)
    except Exception as e:
        logger.warning("Menu push failed: %s", e)

    if errors:
        return "degraded", f"{len(errors)} namespace(s) failed: {','.join(errors)}", \
            set(discovered), dashboards, menu_uid
    return "ok", f"{len(dashboards)} fabric dashboard(s)", set(discovered), dashboards, menu_uid


def main():
    _setup_logging()
    logger.info("TopoView controller started (version %s); grafana=%s interval=%ds",
                VERSION, GRAFANA_URL, RECONCILE_INTERVAL)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    _start_health_server()
    _health.update(state="ok", message="started")
    _ensure_default_cr()

    while not shutdown_event.is_set():
        cycle_start = time.time()
        cfg = _read_config()
        try:
            health, message, discovered, dashboards, menu_uid = reconcile(cfg)
        except Exception as e:
            health, message, discovered, dashboards, menu_uid = "degraded", f"reconcile error: {e}", set(), [], None
            logger.warning("Reconcile failed: %s", e)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _health.update(state=health, time=now, message=message)
        _update_status(health, message, discovered, dashboards, menu_uid)
        logger.info("Reconcile done: %d dashboard(s), health=%s (%dms)",
                    len(dashboards), health, int((time.time() - cycle_start) * 1000))
        shutdown_event.wait(timeout=RECONCILE_INTERVAL)

    logger.info("Controller shutting down")


if __name__ == "__main__":
    main()
