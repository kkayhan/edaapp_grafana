/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
*/

package v1alpha1

// TopoViewConfigSpec defines optional global tuning for TopoView. A single
// cluster-scoped instance named "default" is auto-created by the controller.
// The user does not normally edit it -- fabric dashboards are auto-discovered
// per namespace and rendered with sane defaults.
type TopoViewConfigSpec struct {
	// NamespaceExclude lists namespaces to skip (no dashboard generated).
	// +eda:ui:title="Namespaces to exclude"
	// +eda:ui:orderpriority=100
	NamespaceExclude []string `json:"namespaceExclude,omitempty"`

	// Refresh is the Grafana auto-refresh interval for generated dashboards.
	// +kubebuilder:default="5s"
	// +eda:ui:title="Dashboard refresh interval"
	// +eda:ui:orderpriority=200
	Refresh string `json:"refresh,omitempty"`

	// RoleTiers maps an eda.nokia.com/role value (borderleaf, spine, leaf, ...)
	// to a row index (0 = top). Default: border-leaf top, spine middle, leaf
	// bottom, edge/host below leaves.
	// +eda:ui:title="Role to row mapping"
	// +eda:ui:orderpriority=300
	RoleTiers map[string]int32 `json:"roleTiers,omitempty"`

	// PrometheusDatasourceUid is the Grafana datasource uid referenced by the
	// generated panels. Defaults to the provisioned "Prometheus" datasource uid.
	// +kubebuilder:default="PBFA97CFB590B2093"
	// +eda:ui:title="Prometheus datasource uid (advanced)"
	// +eda:ui:orderpriority=400
	PrometheusDatasourceUid string `json:"prometheusDatasourceUid,omitempty"`

	// Thresholds overrides the traffic and oper-state colour thresholds.
	// +eda:ui:title="Colour thresholds (advanced)"
	// +eda:ui:orderpriority=500
	Thresholds *Thresholds `json:"thresholds,omitempty"`
}

// Threshold is one colour band: apply Color at or above Level.
type Threshold struct {
	Color string `json:"color"`
	Level int64  `json:"level"`
}

// Thresholds carries the traffic (bps) and oper-state colour bands.
type Thresholds struct {
	Traffic   []Threshold `json:"traffic,omitempty"`
	OperState []Threshold `json:"operState,omitempty"`
}

// TopoViewConfigStatus is the observed state (controller is the sole writer).
type TopoViewConfigStatus struct {
	// Health is ok, degraded, or error.
	Health string `json:"health,omitempty"`
	// Message is a human-readable explanation of the current health state.
	Message string `json:"message,omitempty"`
	// LastReconcileTime is the timestamp of the last completed reconcile.
	LastReconcileTime string `json:"lastReconcileTime,omitempty"`
	// DiscoveredNamespaces are the namespaces that have a fabric.
	DiscoveredNamespaces []string `json:"discoveredNamespaces,omitempty"`
	// Dashboards is the per-namespace dashboard inventory.
	Dashboards []DashboardStatus `json:"dashboards,omitempty"`
	// MenuDashboardUid is the read-only landing/home dashboard uid.
	MenuDashboardUid string `json:"menuDashboardUid,omitempty"`
	// Version is the controller version string.
	Version string `json:"version,omitempty"`
}

// DashboardStatus is one generated per-namespace topology dashboard.
type DashboardStatus struct {
	Namespace      string `json:"namespace"`
	Uid            string `json:"uid"`
	NodeCount      int    `json:"nodeCount,omitempty"`
	LinkCount      int    `json:"linkCount,omitempty"`
	GenerationHash string `json:"generationHash,omitempty"`
}
