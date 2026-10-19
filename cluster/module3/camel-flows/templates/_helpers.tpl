{{/*
Expand the name of the chart.
*/}}
{{- define "camel-flows.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "camel-flows.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "camel-flows.labels" -}}
helm.sh/chart: {{ include "camel-flows.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
