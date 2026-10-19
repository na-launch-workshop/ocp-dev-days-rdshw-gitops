{{/*
Tenant Dev Spaces namespace — same convention as Module 3 / CheCluster.
*/}}
{{- define "module5.namespace" -}}
{{ .Values.tenant.username }}-devspaces
{{- end }}

{{/*
Tenant MTA lab project — OpenShift namespace for analysis / deployment (e.g. user1-ai-mta).
*/}}
{{- define "module5.mtaNamespace" -}}
{{ .Values.tenant.username }}-ai-mta
{{- end }}

{{- define "module5.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "module5.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "module5.labels" -}}
helm.sh/chart: {{ include "module5.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: module5
{{- end }}

{{- define "module5.namespace-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.namespace }}"
{{- end }}
{{- end }}

{{- define "module5.workspace-syncwave" -}}
{{- if and (.Values.argocd) (.Values.argocd.syncwave) (.Values.argocd.syncwave.enabled) -}}
argocd.argoproj.io/sync-wave: "{{ .Values.argocd.syncwave.workspace }}"
{{- end }}
{{- end }}

{{/*
Git remote for the EAP lab project. Defaults to the tenant GitLab mirror.
*/}}
{{- define "module5.gitRepo" -}}
{{- if .Values.devworkspace.gitRepo -}}
{{- .Values.devworkspace.gitRepo -}}
{{- else -}}
https://{{ .Values.gitlab.host }}/{{ .Values.tenant.username }}/parasol-insurance-eap.git
{{- end -}}
{{- end }}
