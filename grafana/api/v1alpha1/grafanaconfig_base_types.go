/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
*/

package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// GrafanaConfig is the Schema for the grafanaconfigs API
// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:path=grafanaconfigs,scope=Cluster
type GrafanaConfig struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   GrafanaConfigSpec   `json:"spec,omitempty"`
	Status GrafanaConfigStatus `json:"status,omitempty"`
}

// GrafanaConfigList contains a list of GrafanaConfig
// +kubebuilder:object:root=true
type GrafanaConfigList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []GrafanaConfig `json:"items"`
}

func init() {
	SchemeBuilder.Register(&GrafanaConfig{}, &GrafanaConfigList{})
}
