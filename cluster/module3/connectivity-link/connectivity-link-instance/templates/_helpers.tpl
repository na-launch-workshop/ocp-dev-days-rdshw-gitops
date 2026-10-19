{{/*
Expand the name of the chart.
*/}}
{{- define "connectivity-link.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "connectivity-link.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "connectivity-link.labels" -}}
helm.sh/chart: {{ include "connectivity-link.chart" . }}
app.kubernetes.io/name: {{ include "connectivity-link.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
ArgoCD sync-wave for Namespace
*/}}
{{- define "connectivity-link.namespace-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.namespace }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for config resources (Roles, Services)
*/}}
{{- define "connectivity-link.config-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.config }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for GatewayClass
*/}}
{{- define "connectivity-link.gatewayclass-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.gatewayclass }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for workloads (Gateway)
*/}}
{{- define "connectivity-link.workloads-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.workloads }}"
{{- end }}
{{- end }}
