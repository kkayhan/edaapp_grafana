"""
EDA API auth for the Grafana controller (EQL-direct data path).

Acquires an EDA API bearer token WITHOUT assuming any human user's password, then
exposes eda_api_get() so eql.py can query eda-api in-cluster.

Two hardening measures (both learned from edaapp_UserAudit, verified on the customer):

  1. Keycloak base is PROBED, not hardcoded. The EDA API base is stable, but the
     Keycloak proxy path differs by EDA release/deployment: some clusters expose it
     under the generic HttpProxy at /core/httpproxy/v1/keycloak, others at the native
     /core/proxy/v1/identity. Hardcoding one 404s on the other and every eda_api_get
     then fails (the symptom: blank throughput). _ensure_kc_base() probes the
     candidates once and pins the first that answers.

  2. EDA API token via a self-provisioned SERVICE ACCOUNT (client_credentials), not a
     password grant. The old path read eda-realm-auth-secret (an EDA bootstrap seed =
     admin/admin, never re-synced); the moment an operator changes the EDA admin
     password it goes stale and the grant 401s. Instead the controller uses the KC
     master admin (keycloak-admin-secret, EDA-managed, stays valid) to create a
     dedicated confidential client `eda-grafana`, grant its service account the
     `edarole_system-administrator` realm role, and mint tokens via client_credentials.
     Password-free, self-healing, revocable (delete the KC client to revoke).

All secrets it reads (keycloak-admin-secret, eda-api-ca) pre-exist in eda-system
(shipped by EDA core); the controller only READS them. TLS trusts the internal trust
bundle + eda-api-ca, falling back to unverified TLS.
"""
import base64
import json
import logging
import os
import ssl
import time
import urllib.error
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

import k8s

logger = logging.getLogger("auth")

_NAMESPACE = os.environ.get("POD_NAMESPACE", "eda-system")
_TRUST_BUNDLE = "/var/run/eda/tls/internal/trust/trust-bundle.pem"
_TIMEOUT = 30

# In-cluster base URLs. The EDA API base is stable; the Keycloak base differs by
# EDA release/deployment, so probe the candidates and pin the working one.
_EDA_API_BASE = os.environ.get("EDA_API_BASE", "https://eda-api.eda-system.svc")
_KC_BASE_CANDIDATES = [
    _EDA_API_BASE + "/core/httpproxy/v1/keycloak",                       # 26.4.3 + Talos 26.4.1 lab
    _EDA_API_BASE + "/core/proxy/v1/identity",                           # 26.4.1 native identity route (customer)
    "https://eda-keycloak.eda-system.svc:9443/core/proxy/v1/identity",   # direct to keycloak, last resort
]
_KC_BASE = _KC_BASE_CANDIDATES[0]  # pinned by _ensure_kc_base() at first token call

# Dedicated service-account client the controller self-provisions in the `eda` realm.
# client_credentials on it yields a token carrying _EDA_ROLE, which the EDA API
# (query/v1, ...) authorizes on. No human user, no password.
_SVC_CLIENT_ID = "eda-grafana"
_EDA_ROLE = "edarole_system-administrator"  # the EDA realm role the `admin` user also holds

_kc_admin_token_cache = [None, 0]
_eda_api_token_cache = [None, 0]
_svc_client_secret_cache = [None]
_kc_base_cache = [None]
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


def _ensure_kc_base():
    """Probe the candidate Keycloak bases once (unauthenticated .well-known GET on the
    master realm) and pin _KC_BASE to the first that answers 2xx. EDA deployments route
    Keycloak under different relative paths; a hardcoded base 404s on the others and
    fails every downstream token acquisition."""
    global _KC_BASE
    if _kc_base_cache[0]:
        return
    ctx = get_ssl_context()
    for base in _KC_BASE_CANDIDATES:
        url = f"{base}/realms/master/.well-known/openid-configuration"
        try:
            with urlopen(Request(url=url, method="GET"), context=ctx, timeout=_TIMEOUT) as resp:
                if 200 <= resp.status < 300:
                    _KC_BASE = base
                    _kc_base_cache[0] = base
                    logger.info("Keycloak base discovered: %s", base)
                    return
                logger.info("Keycloak base probe %s -> HTTP %s", url, resp.status)
        except urllib.error.HTTPError as e:
            logger.info("Keycloak base probe %s -> HTTP %s", url, e.code)
        except Exception as e:
            logger.info("Keycloak base probe %s -> %s", url, e)
    logger.warning("No Keycloak base probe succeeded; using default %s", _KC_BASE)
    _kc_base_cache[0] = _KC_BASE


def _http_post_form(url, fields):
    data = urlencode(fields).encode("utf-8")
    req = Request(url=url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(req, context=get_ssl_context(), timeout=_TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as e:
        logger.warning("POST %s -> HTTP %s", url, e.code)
        raise


def _http_json(method, url, headers, data=None):
    req = Request(url=url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    with urlopen(req, context=get_ssl_context(), timeout=_TIMEOUT) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def _kc_token_url(realm):
    return f"{_KC_BASE}/realms/{realm}/protocol/openid-connect/token"


def get_kc_admin_token(force=False):
    """KC master-realm admin token from keycloak-admin-secret (admin-cli password grant).
    This is the EDA-managed Keycloak master admin, which stays valid even when the EDA
    `admin` user's password is changed."""
    now = time.time()
    if not force and _kc_admin_token_cache[0] and now < _kc_admin_token_cache[1] - 30:
        return _kc_admin_token_cache[0]
    _ensure_kc_base()  # pin the working Keycloak base before building any token URL
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
    logger.info("KC admin token acquired (expires in %ds)", resp.get("expires_in", 300))
    return _kc_admin_token_cache[0]


def _kc_admin_json(method, path, admin_token, body=None):
    """method+path against the KC admin API (under the pinned _KC_BASE) with an optional
    JSON body. Returns parsed JSON (or None for empty 2xx). Raises HTTPError on non-2xx."""
    url = _KC_BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {admin_token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    return _http_json(method, url, headers, data)


def _ensure_service_client(admin_token):
    """Idempotently ensure the dedicated `eda-grafana` confidential service-account
    client exists in the `eda` realm and its service account holds _EDA_ROLE, then return
    its client secret. Self-healing: recreates the client / re-grants the role / re-fetches
    the secret on demand, so deleting or rotating any of them re-provisions on next call."""
    if _svc_client_secret_cache[0]:
        return _svc_client_secret_cache[0]

    # 1. Find the client, creating it if absent.
    clients = _kc_admin_json("GET", f"/admin/realms/eda/clients?clientId={_SVC_CLIENT_ID}",
                             admin_token) or []
    kc_id = next((c.get("id") for c in clients if c.get("clientId") == _SVC_CLIENT_ID), None)
    if not kc_id:
        body = {
            "clientId": _SVC_CLIENT_ID,
            "name": "EDA Grafana (service account)",
            "description": ("Dedicated service-account client for the EDA Grafana app. "
                            "Auth via client_credentials; no human password. Self-managed by "
                            "the eda-grafana controller — safe to delete to revoke access."),
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": False,
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": False,
            "directAccessGrantsEnabled": False,
        }
        try:
            _kc_admin_json("POST", "/admin/realms/eda/clients", admin_token, body)
        except urllib.error.HTTPError as e:
            if e.code != 409:  # 409 = concurrent create won the race; fall through to re-GET
                raise
        clients = _kc_admin_json("GET", f"/admin/realms/eda/clients?clientId={_SVC_CLIENT_ID}",
                                 admin_token) or []
        kc_id = next((c.get("id") for c in clients if c.get("clientId") == _SVC_CLIENT_ID), None)
        if not kc_id:
            raise RuntimeError(f"Failed to create/find service client {_SVC_CLIENT_ID}")
        logger.info("Provisioned dedicated service client %s", _SVC_CLIENT_ID)

    # 2. Ensure the service account holds the EDA realm role the API authorizes on.
    sa = _kc_admin_json("GET", f"/admin/realms/eda/clients/{kc_id}/service-account-user",
                        admin_token) or {}
    sa_id = sa.get("id")
    if not sa_id:
        raise RuntimeError(f"Service client {_SVC_CLIENT_ID} has no service-account user")
    have = _kc_admin_json("GET", f"/admin/realms/eda/users/{sa_id}/role-mappings/realm",
                          admin_token) or []
    if not any(r.get("name") == _EDA_ROLE for r in have):
        role = _kc_admin_json("GET", f"/admin/realms/eda/roles/{quote(_EDA_ROLE)}", admin_token)
        if not role or "id" not in role:
            raise RuntimeError(f"Realm role {_EDA_ROLE} not found in realm 'eda'")
        _kc_admin_json("POST", f"/admin/realms/eda/users/{sa_id}/role-mappings/realm",
                       admin_token, [{"id": role["id"], "name": role["name"]}])
        logger.info("Granted %s to service account of %s", _EDA_ROLE, _SVC_CLIENT_ID)

    # 3. Fetch the (Keycloak-generated) client secret.
    sec = _kc_admin_json("GET", f"/admin/realms/eda/clients/{kc_id}/client-secret",
                         admin_token) or {}
    val = sec.get("value") or sec.get("secret")
    if not val:
        raise RuntimeError(f"Failed to fetch client secret for {_SVC_CLIENT_ID}")
    _svc_client_secret_cache[0] = val
    return val


def get_eda_api_token(force=False):
    """EDA API token via client_credentials on the self-provisioned service account
    (no password, no eda-realm-auth-secret)."""
    now = time.time()
    if not force and _eda_api_token_cache[0] and now < _eda_api_token_cache[1] - 30:
        return _eda_api_token_cache[0]
    admin_token = get_kc_admin_token()

    def _grant():
        secret = _ensure_service_client(admin_token)
        return _http_post_form(_kc_token_url("eda"), {
            "grant_type": "client_credentials",
            "client_id": _SVC_CLIENT_ID,
            "client_secret": secret,
        })

    try:
        resp = _grant()
    except urllib.error.HTTPError as e:
        # 400/401 from the token endpoint = stale/rotated/deleted client secret.
        # Drop the cached secret and re-provision the client once.
        if e.code in (400, 401):
            logger.warning("client_credentials grant HTTP %s — re-provisioning %s",
                           e.code, _SVC_CLIENT_ID)
            _svc_client_secret_cache[0] = None
            resp = _grant()
        else:
            raise
    if not resp or "access_token" not in resp:
        raise RuntimeError("EDA API auth failed: no access_token")
    _eda_api_token_cache[0] = resp["access_token"]
    _eda_api_token_cache[1] = now + resp.get("expires_in", 300)
    logger.info("EDA API token acquired via service account %s (expires in %ds)",
                _SVC_CLIENT_ID, resp.get("expires_in", 300))
    return _eda_api_token_cache[0]


def _invalidate():
    _eda_api_token_cache[0] = None
    _eda_api_token_cache[1] = 0
    _svc_client_secret_cache[0] = None


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
