{{/*
Expand the name of the chart.
*/}}
{{- define "shared-database.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "shared-database.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "shared-database.labels" -}}
helm.sh/chart: {{ include "shared-database.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
ArgoCD sync-wave for Namespace
*/}}
{{- define "shared-database.namespace-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.namespace }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for NetworkPolicies
*/}}
{{- define "shared-database.networkpolicies-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.networkpolicies }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for RBAC
*/}}
{{- define "shared-database.rbac-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.rbac }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for Database resources
*/}}
{{- define "shared-database.database-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.database }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for Skupper resources
*/}}
{{- define "shared-database.skupper-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.skupper }}"
{{- end }}
{{- end }}
