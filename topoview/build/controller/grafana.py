"""
Push dashboards to the bundled Grafana over its HTTP API.

Humans reach Grafana anonymously as read-only Viewers; the controller writes with
admin basic-auth (GF_SECURITY_ADMIN_USER/PASSWORD). In-cluster this is plain HTTP
to grafana.eda-topoview.svc -- no Keycloak, no HttpProxy.
"""
import base64
import json
import logging
import ssl
import urllib.error
from urllib.request import Request, urlopen

logger = logging.getLogger("grafana")
_TIMEOUT = 20
# Grafana is plain HTTP in-cluster; this ctx is only used if a caller passes https.
_INSECURE = ssl.create_default_context()
_INSECURE.check_hostname = False
_INSECURE.verify_mode = ssl.CERT_NONE


def _req(method, url, user, password, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url=url, data=data, method=method)
    tok = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {tok}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    ctx = _INSECURE if url.startswith("https") else None
    with urlopen(req, timeout=_TIMEOUT, context=ctx) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw.decode()) if raw else None)


def dashboard_exists(base_url, uid, user, password):
    url = f"{base_url.rstrip('/')}/api/dashboards/uid/{uid}"
    try:
        status, _ = _req("GET", url, user, password)
        return status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def push_dashboard(base_url, dashboard, user, password, message="topoview reconcile"):
    """POST the dashboard (overwrite by uid). Returns the new version int."""
    url = f"{base_url.rstrip('/')}/api/dashboards/db"
    d = dict(dashboard)
    d["id"] = None  # let Grafana match/overwrite by uid
    body = {"dashboard": d, "overwrite": True, "folderId": 0, "message": message}
    status, resp = _req("POST", url, user, password, body)
    return (resp or {}).get("version")


def health(base_url):
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False
