# EDA Grafana — auto-generated Grafana topology dashboards for Nokia EDA

**Grafana** is a [Nokia EDA](https://docs.eda.dev/) application that turns any EDA-managed
data-center fabric into a live Grafana **topology map** — automatically. Install it once, and it
discovers every namespace that contains a fabric, draws the switch topology, wires up live
per-interface telemetry, and keeps the dashboards in sync as the topology changes. No dashboards to
hand-build, no SVG to hand-edit, and — since **v26.4.3-1** — **no Prometheus and no external
dependencies**: telemetry is read straight from EDA over EQL.

![Live topology dashboard](docs/images/topology.png)

---

## What it does

- **Auto-discovers fabrics.** Every ~30 s it lists `TopoNode` / `TopoLink` (and cross-checks the
  `Fabric` CR) across all namespaces and builds one dashboard per fabric.
- **Draws the fabric by role.** Border-leaf switches on the **top** row, spines in the **middle**,
  leaf switches on the **bottom** — roles come from the `eda.nokia.com/role` node label (or the
  Fabric CR's selectors). Rows that are empty collapse; the canvas auto-scales to any fabric size.
- **Live link telemetry, straight from EQL.** Every inter-switch link is a dual-direction arrow
  coloured by throughput, with a live bits/sec pill per direction, plus a port dot coloured by
  oper-state (green up / red down). Values refresh every ~5 s directly from EDA's database — no
  time-series backend in the middle.
- **Edge interfaces, not host clutter.** Host-facing ports aren't drawn as nodes — instead each leaf
  lists its edge interfaces (`ethernet-1/3`, `ethernet-1/4`, …) with live in/out bps, the interface
  name coloured by rate.
- **Read-only, no login.** Users land straight on a menu, pick a fabric, and view it. Grafana is
  anonymous read-only behind EDA's own authenticated proxy.
- **Self-healing & change-aware.** Dashboards are re-generated only when a fabric's *structure*
  changes (add/remove/re-cable a node), and re-pushed automatically if Grafana restarts.

| Menu (pick a fabric) | Scales to large fabrics (4 spine × 8 leaf) |
|---|---|
| ![Menu](docs/images/menu.png) | ![Large fabric](docs/images/large-fabric.png) |

---

## How it works

Grafana bundles just a **custom Grafana** (no Prometheus, no Loki — EDA has native logging) plus a
small Python controller. Everything installs into the `eda-system` namespace. The Grafana image has
the two required plugins **baked in** — so it installs on an **air-gapped cluster** with no
grafana.com access — and its chrome is **hidden**, so users only see the fabric menu and maps.

```
                              eda-system namespace
  ┌──────────────────────────────────────────────────────────────────┐
  │  Grafana controller (Deployment + Service :8080)                 │
  │    every 30s: read TopoNode/TopoLink/Fabric  →  build model       │
  │               →  auto-layout  →  render SVG + flow-panel config    │
  │               →  push dashboard to Grafana (admin API)            │
  │                                                                    │
  │    on demand: GET /eql/<ns>.json  →  run 2 EQL queries on eda-api  │
  │               →  reshape to the panel's series  (TTL-cached)       │
  │       ▲                                                            │
  │       │ Infinity datasource (JSON/URL)                            │
  │  Grafana (anonymous read-only, andrewbmchugh-flow-panel plugin)    │
  └───────────────────────────────┬──────────────────────────────────┘
              │ EQL (bearer token)  │  EDA HttpProxy
              ▼                     ▼
        eda-api  ───────►  https://<your-eda-host>/core/httpproxy/v1/grafana/
     /core/query/v1/eql
```

- The controller reads the **topology** with its Kubernetes ServiceAccount token, and reads **live
  telemetry** from EDA over EQL (`/core/query/v1/eql`) using an EDA API bearer token.
- **Authentication is automatic.** The controller obtains its token via the standard EDA password
  grant, reading the pre-existing `keycloak-admin-secret`, `eda-api-ca`, and `eda-realm-auth-secret`
  in `eda-system` — the app ships no credentials of its own.
- Grafana's [`yesoreyeram-infinity-datasource`](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/)
  polls the controller's `/eql/<ns>.json` endpoint; the controller runs two EQL queries per namespace
  (`interface.traffic-rate` and `interface` oper-state) and reshapes them into exactly the series the
  [`andrewbmchugh-flow-panel`](https://github.com/andrewbmchugh/flow) panel needs. A short TTL cache
  means eda-api is queried once per refresh interval no matter how many people are watching.

---

## What's in this repo

```
PROJECT                         edabuilder project descriptor
grafana/
  manifest.yaml                 EDA app manifest (CRD + ordered cr: components)
  api/v1alpha1/                 Go types for the GrafanaConfig CRD
  crds/  openapiv3/             generated CRD + OpenAPI schema
  build-grafana/                custom Grafana image: flow-panel + infinity baked in
    Dockerfile                  (air-gap ready) and chrome-hiding CSS injected
    grafana-kiosk.css          CSS that hides Grafana's nav / controls
  build/
    Dockerfile                  controller image
    controller/                 the controller (Python, stdlib only)
      main.py                   reconcile loop, namespace discovery, /eql endpoint + cache
      auth.py                   EDA API token (password grant) + TLS to eda-api
      eql.py                    EQL queries + reshape to flow-panel series
      topology.py               CRs  → vendor-neutral topology model
      layout.py                 model → auto-layout (role rows, crossing-min, port fan-out)
      svggen.py                 layout → SVG + flow-panel panelConfig
      dashboard.py              flow-panel dashboard (Infinity target) + menu dashboard JSON
      grafana.py  k8s.py        Grafana API + Kubernetes API clients
  docs/                         Store app page (index.md, README, CHANGELOG, …)
  manifests/                    bundled stack, deployed into eda-system
    01-grafana-secret 20/21-grafana 40-grafana-httpproxy app_deployment rbac
examples/localtest/             offline generator harness + synthetic fabric fixtures
```

---

## Install

Prerequisite: a Nokia EDA cluster (tested on EDA **26.4.1** / **26.4.3**). No exporter app, no Prometheus — Grafana is
self-contained. Everything installs into the `eda-system` namespace.

### From the EDA Store (recommended)

Published in the [kkayhan/eda-catalog](https://github.com/kkayhan/eda-catalog) catalog. Register the
catalog, then install:

```yaml
# catalog.yaml
apiVersion: appstore.eda.nokia.com/v1
kind: Catalog
metadata: { name: kkayhan-catalog, namespace: eda-system }
spec:
  enabled: true
  remoteType: git
  remoteURL: https://github.com/kkayhan/eda-catalog.git
  refreshInterval: 180
  title: kkayhan community catalog
```

```bash
kubectl apply -f catalog.yaml
```

Grafana then appears in the EDA UI **Store** (or install headlessly with an `AppInstaller` CR for
`appId: grafana.eda.edacommunity.com`, `catalog: kkayhan-catalog`, `version: v26.4.1-5`).

### Air-gapped cluster (offline)

Each release ships an **air-gap bundle** attached to the catalog repo's GitHub Release
(`apps/grafana.eda.edacommunity.com/<version>`). It contains OCI archives of all three images
(bundle, controller, and the custom Grafana with its plugins baked in), a git bundle of the catalog,
pre-filled `Registry` + `Catalog` manifests, and an `INSTALL-GUIDE.md`. On the Assets VM you `skopeo
copy` the images into a local registry, push the catalog git bundle into a local Gitea, `kubectl
apply` the manifests, then install from the Store — no internet needed. Because the plugins are baked
into the Grafana image, nothing is fetched from grafana.com at runtime. Full steps are in the
bundle's `INSTALL-GUIDE.md`.

### Manual deploy (dev)

```bash
kubectl apply -f grafana/crds/
kubectl apply -f grafana/manifests/          # all land in eda-system
```

The controller does the rest automatically:

- **EDA host is auto-detected.** The controller reads the cluster's external address from
  `EngineConfig` and writes it into the `grafana-rooturl` ConfigMap, which Grafana's
  `GF_SERVER_ROOT_URL` references (`configMapKeyRef`). Because that value is owned by the controller
  — not baked into the Deployment — the EDA app-loader reasserting the bundle never rolls Grafana;
  the controller restarts it exactly once, only when the detected host actually changes. Nothing to
  hardcode, works on any cluster.
- **Live telemetry needs no setup** — the controller authenticates to eda-api itself using the
  cluster's own EDA secrets and serves the flow panel over its `/eql/<ns>.json` endpoint.

For a manual deploy you may optionally set a real `admin-password` in
`grafana/manifests/01-grafana-secret.yaml` (placeholder shipped; it only protects the controller's
write path — humans are anonymous read-only).

### Open it

`https://<your-eda-host>/core/httpproxy/v1/grafana/` — land on the menu, pick a fabric, done.
Grafana's chrome (top bar, side menu, time picker, share/export) is hidden by the custom image's
baked-in CSS, so users only ever see the fabric menu and the maps — no `?kiosk` needed.

---

## Configuration — `GrafanaConfig` (cluster-scoped singleton)

You normally don't need to touch it — auto-discovery drives everything. It exists for global tuning
and status. All spec fields are optional:

| Field | Default | Purpose |
|---|---|---|
| `namespaceExclude` | `[]` | namespaces to skip |
| `refresh` | `5s` | dashboard refresh interval |
| `roleTiers` | `{borderleaf:0, superspine:0, spine:1, leaf:2}` | role → row |
| `thresholds` | built-in | traffic + oper-state colour thresholds |

`status` reports discovered namespaces, per-fabric dashboard uids + structure hashes, and health.

---

## Notes

- **Live values only.** Grafana reads current throughput + oper-state straight from EDA — there is
  no time-series history (that's the deliberate trade for dropping Prometheus). It's a live map, not
  a trends dashboard.
- **Metrics are SR Linux (`.namespace.node.srl.interface.*`).** All-SRL fabrics today; SR OS /
  multi-vendor is future work.
- **Bundled Grafana is ephemeral** (no PVC). API-pushed dashboards are re-pushed on restart by the
  controller (self-heal, ≤30 s); the menu is provisioned from a file.
- Roles resolve from the `eda.nokia.com/role` label first, then the `Fabric` CR selectors.

## License

MIT — see [LICENSE](LICENSE).
