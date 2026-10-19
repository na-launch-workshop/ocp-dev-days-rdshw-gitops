{{/*
Expand the name of the chart.
*/}}
{{- define "admin.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "admin.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "admin.labels" -}}
helm.sh/chart: {{ include "admin.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
