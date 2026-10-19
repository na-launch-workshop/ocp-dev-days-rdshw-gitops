{{/*
Expand the name of the chart.
*/}}
{{- define "matrix.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "matrix.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "matrix.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "matrix.labels" -}}
helm.sh/chart: {{ include "matrix.chart" . }}
{{ include "matrix.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "matrix.selectorLabels" -}}
app.kubernetes.io/name: {{ include "matrix.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ArgoCD sync-wave for Namespace
*/}}
{{- define "matrix.namespace-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.namespace }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for ConfigMaps and PVC
*/}}
{{- define "matrix.config-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.configAndStorage }}"
{{- end }}
{{- end }}

{{/*
ArgoCD sync-wave for Deployments, Services, Routes
*/}}
{{- define "matrix.workloads-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.workloads }}"
{{- end }}
{{- end }}
