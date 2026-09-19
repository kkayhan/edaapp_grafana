#!/usr/bin/env python3
"""Synthesize EDA CRs (TopoNode/TopoLink/Fabric) for 3 test fabrics matching the
exact live-CR shape, so the real generator pipeline can be validated on them.

  ns1: 2 border-leaf + 2 spine + 4 leaf, 1 host per leaf
  ns2: 4 spine + 8 leaf,               1 host/edge per leaf
  ns3: 1 border-leaf + 2 spine + 5 leaf, 2 hosts per leaf

Writes <ns>-toponodes.json / <ns>-topolinks.json / <ns>-fabrics.json (items-wrapped)
into this dir — consumed verbatim by run_local.py.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def node(ns, name, role, platform="7220 IXR-D2L"):
    return {
        "apiVersion": "core.eda.nokia.com/v1", "kind": "TopoNode",
        "metadata": {"name": name, "namespace": ns,
                     "labels": {"eda.nokia.com/role": role, "role": role,
                                "eda.nokia.com/security-profile": "managed"}},
        "spec": {"operatingSystem": "srl", "platform": platform,
                 "onBoarded": True, "version": "26.3.2",
                 "nodeProfile": "srlinux-ghcr-26.3.2"},
        "status": {"node-state": "Synced", "npp-state": "Connected", "simulate": True},
    }


def isl(ns, a, aif, b, bif):
    return {
        "apiVersion": "core.eda.nokia.com/v1", "kind": "TopoLink",
        "metadata": {"name": f"{a}-{b}", "namespace": ns, "labels": {"role": "interSwitch"}},
        "spec": {"links": [{"local": {"interface": aif, "node": a,
                                      "interfaceResource": f"{a}-{aif}"},
                            "remote": {"interface": bif, "node": b,
                                       "interfaceResource": f"{b}-{bif}"},
                            "type": "interSwitch"}]},
        "status": {"operationalState": "up"},
    }


def edge(ns, leaf, iface, tag):
    return {
        "apiVersion": "core.eda.nokia.com/v1", "kind": "TopoLink",
        "metadata": {"name": f"{leaf}-{tag}", "namespace": ns, "labels": {"role": "edge"}},
        "spec": {"links": [{"local": {"interface": iface, "node": leaf,
                                      "interfaceResource": f"{leaf}-{iface}"},
                            "remote": {"interface": "", "node": "", "interfaceResource": ""},
                            "type": "edge"}]},
        "status": {"operationalState": "up"},
    }


def fabric(ns, name):
    # role labels are the primary path; include a Fabric CR so the title shows Fabric:<name>
    return {
        "apiVersion": "fabrics.eda.nokia.com/v1", "kind": "Fabric",
        "metadata": {"name": name, "namespace": ns},
        "spec": {"leafs": {"leafNodeSelectors": ["eda.nokia.com/role=leaf"]},
                 "spines": {"spineNodeSelectors": ["eda.nokia.com/role=spine"]},
                 "borderLeafs": {"borderLeafNodeSelectors": ["eda.nokia.com/role=borderleaf"]}},
    }


def eth(n):
    return f"ethernet-1-{n}"


def lag_iface(ns, idx, node_a, node_b, port, oper):
    """A server-facing MC-LAG modelled as an EDA Interface CR (spec.type lag),
    dual-homed on node_a+node_b via the same physical port, with NO TopoLink and no
    edge role label -- exactly how the customer's leaf access LAGs are defined."""
    ifn = eth(port)
    return {
        "apiVersion": "interfaces.eda.nokia.com/v1", "kind": "Interface",
        "metadata": {"name": f"lag{idx}-{node_a}-{node_b}", "namespace": ns},
        "spec": {"type": "lag", "enabled": True,
                 "members": [{"node": node_a, "interface": ifn, "enabled": True},
                             {"node": node_b, "interface": ifn, "enabled": True}]},
        "status": {"operationalState": oper},
    }


def build(ns, fab_name, n_bl, n_spine, n_leaf, hosts_per_leaf, hosts_per_bl=0,
          n_lag=0, n_lag_down=0):
    nodes, links = [], []
    bls = [f"{ns}-bl{i}" for i in range(1, n_bl + 1)]
    spines = [f"{ns}-spine{i}" for i in range(1, n_spine + 1)]
    leafs = [f"{ns}-leaf{i}" for i in range(1, n_leaf + 1)]

    for b in bls:
        nodes.append(node(ns, b, "borderleaf", "7250 IXR-X1B"))
    for s in spines:
        nodes.append(node(ns, s, "spine", "7250 IXR-X1B"))
    for lf in leafs:
        nodes.append(node(ns, lf, "leaf"))

    # spine downlink port map: leaves first, then border-leaves
    spine_port = {s: 1 for s in spines}

    # leaf uplinks: ethernet-1-1..n_spine -> spine1..N
    for lf in leafs:
        for si, s in enumerate(spines, start=1):
            links.append(isl(ns, lf, eth(si), s, eth(spine_port[s])))
            spine_port[s] += 1
    # border-leaf uplinks into each spine (bl sits above spine, connects down to spine)
    for b in bls:
        for si, s in enumerate(spines, start=1):
            links.append(isl(ns, b, eth(si), s, eth(spine_port[s])))
            spine_port[s] += 1

    # host-facing edge ifaces: after the spine uplinks on each leaf
    for lf in leafs:
        base = n_spine  # uplinks occupy 1..n_spine
        for h in range(1, hosts_per_leaf + 1):
            links.append(edge(ns, lf, eth(base + h), f"host{h}"))
    # border-leaves also carry host-facing ports (access edge) -> exercises the SIDE
    # edge-interface card (grid beside the switch), distinct from the leaf below-card.
    for b in bls:
        for h in range(1, hosts_per_bl + 1):
            links.append(edge(ns, b, eth(h), f"host{h}"))

    # server-facing MC-LAGs (Interface CRs, no TopoLink) dual-homed on the first two
    # leaves -- mirrors the customer's leaf-1/leaf-2 lag pair. Ports 11.. avoid the
    # uplink/host-edge ports. The last n_lag_down are `down` (hidden by the active
    # filter); a couple are `degraded` (still shown).
    ifaces = []
    if n_lag and len(leafs) >= 2:
        la, lb = leafs[0], leafs[1]
        for i in range(1, n_lag + 1):
            if i > n_lag - n_lag_down:
                oper = "down"
            elif i % 5 == 0:
                oper = "degraded"
            else:
                oper = "up"
            ifaces.append(lag_iface(ns, i, la, lb, 10 + i, oper))

    wrap = lambda items: {"apiVersion": "v1", "kind": "List", "items": items}
    json.dump(wrap(nodes), open(os.path.join(HERE, f"{ns}-toponodes.json"), "w"), indent=2)
    json.dump(wrap(links), open(os.path.join(HERE, f"{ns}-topolinks.json"), "w"), indent=2)
    json.dump(wrap([fabric(ns, fab_name)]), open(os.path.join(HERE, f"{ns}-fabrics.json"), "w"), indent=2)
    json.dump(wrap(ifaces), open(os.path.join(HERE, f"{ns}-interfaces.json"), "w"), indent=2)
    print(f"{ns}: {len(nodes)} nodes, {len(links)} topolinks, {len(ifaces)} lag-ifaces "
          f"-> fabric {fab_name}")


build("ns1", "ns1-fabric", n_bl=2, n_spine=2, n_leaf=4, hosts_per_leaf=1, hosts_per_bl=3,
      n_lag=12, n_lag_down=2)
build("ns2", "ns2-fabric", n_bl=0, n_spine=4, n_leaf=8, hosts_per_leaf=1)
build("ns3", "ns3-fabric", n_bl=1, n_spine=2, n_leaf=5, hosts_per_leaf=2, hosts_per_bl=5)
