{{/*
Expand the name of the chart.
*/}}
{{- define "filestash.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "filestash.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "filestash.labels" -}}
helm.sh/chart: {{ include "filestash.chart" . }}
app.kubernetes.io/name: {{ include "filestash.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "filestash.selectorLabels" -}}
app: filestash
{{- end }}

{{/*
ArgoCD sync-wave for Namespace
*/}}
{{- define "filestash.namespace-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.namespace }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for ConfigMaps and RBAC
*/}}
{{- define "filestash.config-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.config }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for workloads
*/}}
{{- define "filestash.workloads-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.workloads }}"
{{- end }}
{{- end }}
