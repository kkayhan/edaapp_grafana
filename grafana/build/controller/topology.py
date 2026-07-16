"""
Build a vendor-neutral topology model from EDA CRs (TopoNode + TopoLink, with an
optional Fabric cross-check for role resolution, and optional Interface CRs to
surface server-facing LAGs that have no TopoLink).

Pure functions, no Kubernetes imports -- unit-testable against static CR JSON.

Output model:
  {
    "nodes": { name: {"role","tier","metric","icon":"switch"} },   # SWITCHES ONLY
    "edges": [ {"a","aif","b","bif","kind":"switch","fwd","rev","a_oper","b_oper"} ],  # inter-switch only
    "edge_ifaces": { node: [ {"iface","label","in","out","oper"} ] },  # host-facing ports, LISTED per switch
    "fabrics": [ fabric_name, ... ],
  }

Hosts are NOT modelled as nodes. Every host-facing (edge) port becomes an entry under
its switch's edge_ifaces, to be listed (name + in/out bps) beneath the switch -- no
host icon, no link, no arrow. Two sources feed edge_ifaces:
  * edge TopoLinks   -- a link endpoint whose remote has no node (border-leaf uplinks
                        to external gear are modelled this way by EDA).
  * LAG Interface CRs -- EDA models server-facing (access) LAGs as `interfaces` of
                        spec.type `lag`, NOT as TopoLinks, so they never appear as a
                        link. Each ACTIVE lag's member ports are listed under their
                        node (label = lag name; telemetry keys on the physical member
                        port, which is already in the EQL feed). Down lags are hidden.

DataRefs match the flow panel's legendFormat contract:
  oper-state:<node>:<if> | <node>:<if>:out | <node>:<if>:in   (<if> = ethernet-1/X slash form)
Interface names in CRs are hyphenated (ethernet-1-4) -> converted to slash (ethernet-1/4).
"""
import re

ROLE_LABEL = "eda.nokia.com/role"

# role (normalized) -> row index. Lower = higher on canvas.
# border-leaf TOP, spine MIDDLE, leaf BOTTOM.
DEFAULT_ROLE_TIERS = {
    "borderleaf": 0,
    "superspine": 0,
    "spine": 1,
    "leaf": 2,
}


def if_to_slash(name):
    """ethernet-1-4 -> ethernet-1/4 (last hyphen -> slash)."""
    if not name:
        return name
    m = re.match(r"^(.*)-(\d+)$", name)
    return f"{m.group(1)}/{m.group(2)}" if m else name


def normalize_role(role):
    return re.sub(r"[^a-z0-9]", "", (role or "").lower())


def natkey(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s or "")]


# normalized spec.type values (see normalize_role) treated as a server-facing LAG
LAG_TYPES = {"lag"}


def _lag_label(name):
    """Friendly display name for a LAG Interface CR: 'lag1-leaf1-2' -> 'lag1'.
    Falls back to the full resource name when there is no leading lag<N>."""
    m = re.match(r"(?i)^lag[-_]?(\d+)", name or "")
    return f"lag{m.group(1)}" if m else (name or "")


def _fabric_role_map(fabrics):
    out = {}
    field_role = [
        ("leafs", "leafNodeSelectors", "leaf"), ("leafs", "leafNodeSelector", "leaf"),
        ("spines", "spineNodeSelectors", "spine"), ("spines", "spineNodeSelector", "spine"),
        ("borderLeafs", "borderLeafNodeSelectors", "borderleaf"),
        ("borderLeafs", "borderLeafNodeSelector", "borderleaf"),
    ]
    for fab in fabrics or []:
        spec = fab.get("spec", {}) or {}
        for parent, key, role in field_role:
            sels = ((spec.get(parent) or {}).get(key)) or []
            if isinstance(sels, str):
                sels = [sels]
            for sel in sels:
                out[sel] = role
    return out


def _resolve_role(node, fabric_map):
    labels = (node.get("metadata", {}) or {}).get("labels", {}) or {}
    role = labels.get(ROLE_LABEL)
    if role:
        return role
    for sel, frole in fabric_map.items():
        if "=" in sel:
            k, v = sel.split("=", 1)
            if labels.get(k.strip()) == v.strip():
                return frole
    return ""


def build_topology(toponodes, topolinks, fabrics=None, role_tiers=None, interfaces=None):
    role_tiers = {normalize_role(k): v for k, v in (role_tiers or DEFAULT_ROLE_TIERS).items()}
    fabric_map = _fabric_role_map(fabrics)
    fabric_names = [f["metadata"]["name"] for f in (fabrics or [])]

    nodes = {}
    for tn in toponodes or []:
        name = tn["metadata"]["name"]
        role = _resolve_role(tn, fabric_map)
        os_type = (tn.get("spec", {}) or {}).get("operatingSystem", "srl")
        nodes[name] = {
            "role": role or "leaf",
            "tier": role_tiers.get(normalize_role(role)),
            "metric": os_type == "srl",
            "icon": "switch",
        }
    # unknown/absent role -> lowest switch tier so it still renders
    known = [n["tier"] for n in nodes.values() if n["tier"] is not None]
    max_tier = max(known + [role_tiers.get("leaf", 2)])
    for n in nodes.values():
        if n["tier"] is None:
            n["tier"] = max_tier

    edges = []
    edge_ifaces = {}   # node -> [ {iface,label,in,out,oper} ]  (dedup by iface)
    seen_edge = set()
    isl_ports = set()  # (node, iface) endpoints used by an inter-switch link

    def add_switch_edge(a, aif, b, bif):
        edges.append({
            "a": a, "aif": aif, "b": b, "bif": bif, "kind": "switch",
            "fwd": f"{a}:{aif}:out", "rev": f"{b}:{bif}:out",
            "a_oper": f"oper-state:{a}:{aif}", "b_oper": f"oper-state:{b}:{bif}",
        })
        isl_ports.add((a, aif))
        isl_ports.add((b, bif))

    def add_edge_iface(node, iface, label=None):
        key = (node, iface)
        if key in seen_edge or not node or not iface:
            return
        seen_edge.add(key)
        edge_ifaces.setdefault(node, []).append({
            "iface": iface,
            "label": label or iface,
            "in": f"{node}:{iface}:in",
            "out": f"{node}:{iface}:out",
            "oper": f"oper-state:{node}:{iface}",
        })

    for tl in topolinks or []:
        for ln in (tl.get("spec", {}) or {}).get("links", []) or []:
            local = ln.get("local", {}) or {}
            remote = ln.get("remote", {}) or {}
            lnode, lif = local.get("node"), if_to_slash(local.get("interface"))
            rnode, rif = remote.get("node"), if_to_slash(remote.get("interface"))
            if not lnode:
                continue
            if rnode:
                if lnode in nodes and rnode in nodes:
                    add_switch_edge(lnode, lif, rnode, rif)
                elif lnode in nodes:
                    # Remote endpoint has no TopoNode in this fabric (stale, external,
                    # or not-yet-onboarded, e.g. "leaf-3" when only leaf-1/leaf-2 exist).
                    # We can't draw a switch-switch link to a node we don't render, so
                    # surface the local port as a host-facing edge port instead of
                    # emitting an unrenderable edge. Keeping such an edge in model[edges]
                    # desynced it from layout[endpoints] downstream -> KeyError in svggen.
                    add_edge_iface(lnode, lif)
                # else: neither endpoint is a known node -> ignore entirely
            else:
                add_edge_iface(lnode, lif)   # host-facing port -> listed under the switch

    # Server-facing LAGs (access ports) are Interface CRs of spec.type `lag`, NOT edge
    # TopoLinks, so the loop above never sees them. List each ACTIVE lag's member ports
    # under their node, labeled with the lag's friendly name; the physical member port
    # keys telemetry (already in the EQL feed). Down lags are hidden; ports that are an
    # ISL endpoint or already listed are skipped (dedup).
    for it in interfaces or []:
        spec = it.get("spec", {}) or {}
        if normalize_role(spec.get("type")) not in LAG_TYPES:
            continue
        oper = str(((it.get("status") or {}).get("operationalState") or "")).lower()
        if oper == "down":
            continue
        label = _lag_label((it.get("metadata") or {}).get("name") or "")
        for m in spec.get("members", []) or []:
            mnode = m.get("node")
            mif = if_to_slash(m.get("interface"))
            if mnode in nodes and (mnode, mif) not in isl_ports:
                add_edge_iface(mnode, mif, label=label)

    for node in edge_ifaces:
        edge_ifaces[node].sort(key=lambda e: natkey(e.get("label") or e["iface"]))

    return {"nodes": nodes, "edges": edges, "edge_ifaces": edge_ifaces,
            "fabrics": fabric_names}


def topology_signature(model):
    """Structural fingerprint: nodes+roles+tiers, inter-switch edges, edge-iface lists."""
    import hashlib
    import json
    struct = {
        "nodes": {k: {"role": v["role"], "tier": v["tier"]}
                  for k, v in sorted(model["nodes"].items())},
        "edges": sorted(f'{e["a"]}:{e["aif"]}|{e["b"]}:{e["bif"]}' for e in model["edges"]),
        "edge_ifaces": {k: sorted(f'{e["iface"]}|{e.get("label", "")}' for e in v)
                        for k, v in sorted(model["edge_ifaces"].items())},
    }
    return hashlib.sha256(
        json.dumps(struct, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
