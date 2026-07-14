/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
*/

package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// TopoViewConfig is the Schema for the topoviewconfigs API
// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:path=topoviewconfigs,scope=Cluster
type TopoViewConfig struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   TopoViewConfigSpec   `json:"spec,omitempty"`
	Status TopoViewConfigStatus `json:"status,omitempty"`
}

// TopoViewConfigList contains a list of TopoViewConfig
// +kubebuilder:object:root=true
type TopoViewConfigList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []TopoViewConfig `json:"items"`
}

func init() {
	SchemeBuilder.Register(&TopoViewConfig{}, &TopoViewConfigList{})
}
