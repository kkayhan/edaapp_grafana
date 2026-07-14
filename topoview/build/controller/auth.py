"""
EDA API auth for the TopoView controller (EQL-direct data path).

Acquires an EDA API bearer token via the proven password-grant chain (same as
edaapp_UserAudit), then exposes eda_api_get() to call eda-api in-cluster:

  1. KC admin token  -- master realm, admin-cli, password grant, from
     keycloak-admin-secret.
  2. 'eda' client secret -- fetched via the KC admin API (cached until 401).
  3. EDA API token   -- eda realm, password grant with eda-realm-auth-secret +
     the fetched client secret.

All three secrets pre-exist in eda-system (shipped by EDA core); the controller
only READS them (no secret is bundled). TLS trusts the eda-api-ca secret (+ the
internal trust bundle if mounted), falling back to unverified TLS.
"""
import base64
import json
import logging
import os
import ssl
import time
import urllib.error
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import k8s

logger = logging.getLogger("auth")

_NAMESPACE = os.environ.get("POD_NAMESPACE", "eda-system")
_TRUST_BUNDLE = "/var/run/eda/tls/internal/trust/trust-bundle.pem"
_TIMEOUT = 30

_EDA_API_BASE = os.environ.get("EDA_API_BASE", "https://eda-api.eda-system.svc")
_KC_BASE = _EDA_API_BASE + "/core/httpproxy/v1/keycloak"

_kc_admin_token_cache = [None, 0]
_eda_api_token_cache = [None, 0]
_eda_client_secret_cache = [None]
_ssl_context = [None]


def _secret_data(name):
    """Return a Secret's .data base64-decoded to {key: str}, or {} if absent."""
    raw = k8s.read_secret(name, _NAMESPACE) or {}
    out = {}
    for k, v in (raw.get("data") or {}).items():
        try:
            out[k] = base64.b64decode(v).decode("utf-8")
        except Exception:
            out[k] = ""
    return out


def _build_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    loaded = False
    try:
        if os.path.exists(_TRUST_BUNDLE):
            ctx.load_verify_locations(_TRUST_BUNDLE)
            loaded = True
    except Exception as e:
        logger.warning("Failed to load trust bundle: %s", e)
    try:
        ca_crt = _secret_data("eda-api-ca").get("ca.crt", "")
        if ca_crt:
            import tempfile
            with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
                f.write(ca_crt)
                f.flush()
                ctx.load_verify_locations(f.name)
                loaded = True
            os.unlink(f.name)
    except Exception as e:
        logger.warning("Failed to load eda-api-ca: %s", e)
    if not loaded:
        logger.warning("No CA certificates loaded; falling back to unverified TLS")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def get_ssl_context():
    if _ssl_context[0] is None:
        _ssl_context[0] = _build_ssl_context()
    return _ssl_context[0]


def _http_post_form(url, fields):
    data = urlencode(fields).encode("utf-8")
    req = Request(url=url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req, context=get_ssl_context(), timeout=_TIMEOUT) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def _http_json(method, url, headers):
    req = Request(url=url, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    with urlopen(req, context=get_ssl_context(), timeout=_TIMEOUT) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def _kc_token_url(realm):
    return f"{_KC_BASE}/realms/{realm}/protocol/openid-connect/token"


def get_kc_admin_token(force=False):
    now = time.time()
    if not force and _kc_admin_token_cache[0] and now < _kc_admin_token_cache[1] - 30:
        return _kc_admin_token_cache[0]
    s = _secret_data("keycloak-admin-secret")
    if not s.get("username") or not s.get("password"):
        raise RuntimeError("keycloak-admin-secret missing username or password")
    resp = _http_post_form(_kc_token_url("master"), {
        "grant_type": "password", "client_id": "admin-cli",
        "username": s["username"], "password": s["password"],
    })
    if not resp or "access_token" not in resp:
        raise RuntimeError("KC admin auth failed: no access_token")
    _kc_admin_token_cache[0] = resp["access_token"]
    _kc_admin_token_cache[1] = now + resp.get("expires_in", 300)
    return _kc_admin_token_cache[0]


def _fetch_eda_client_secret(admin_token):
    if _eda_client_secret_cache[0]:
        return _eda_client_secret_cache[0]
    hdr = {"Authorization": f"Bearer {admin_token}", "Accept": "application/json"}
    clients = _http_json("GET", f"{_KC_BASE}/admin/realms/eda/clients?clientId=eda", hdr) or []
    kc_id = next((c.get("id") for c in clients if c.get("clientId") == "eda"), None)
    if not kc_id:
        raise RuntimeError("Client 'eda' not found in realm 'eda'")
    sj = _http_json("GET", f"{_KC_BASE}/admin/realms/eda/clients/{kc_id}/client-secret", hdr) or {}
    val = sj.get("value") or sj.get("secret")
    if not val:
        raise RuntimeError("Failed to fetch eda client secret")
    _eda_client_secret_cache[0] = val
    return val


def get_eda_api_token(force=False):
    now = time.time()
    if not force and _eda_api_token_cache[0] and now < _eda_api_token_cache[1] - 30:
        return _eda_api_token_cache[0]
    admin_token = get_kc_admin_token()
    client_secret = _fetch_eda_client_secret(admin_token)
    s = _secret_data("eda-realm-auth-secret")
    if not s.get("username") or not s.get("password"):
        raise RuntimeError("eda-realm-auth-secret missing username or password")
    resp = _http_post_form(_kc_token_url("eda"), {
        "grant_type": "password", "client_id": "eda", "client_secret": client_secret,
        "scope": "openid", "username": s["username"], "password": s["password"],
    })
    if not resp or "access_token" not in resp:
        raise RuntimeError("EDA API auth failed: no access_token")
    _eda_api_token_cache[0] = resp["access_token"]
    _eda_api_token_cache[1] = now + resp.get("expires_in", 300)
    logger.info("EDA API token acquired (expires in %ds)", resp.get("expires_in", 300))
    return _eda_api_token_cache[0]


def _invalidate():
    _eda_api_token_cache[0] = None
    _eda_api_token_cache[1] = 0
    _eda_client_secret_cache[0] = None


def eda_api_get(path_qs):
    """GET against eda-api (in-cluster) with a bearer token; auto-refresh on 401."""
    url = _EDA_API_BASE.rstrip("/") + "/" + path_qs.lstrip("/")
    token = get_eda_api_token()
    try:
        return _http_json("GET", url, {"Accept": "application/json",
                                       "Authorization": f"Bearer {token}"})
    except urllib.error.HTTPError as e:
        if e.code == 401:
            logger.warning("eda-api 401 -- refreshing token and retrying")
            _invalidate()
            token = get_eda_api_token(force=True)
            return _http_json("GET", url, {"Accept": "application/json",
                                           "Authorization": f"Bearer {token}"})
        raise
