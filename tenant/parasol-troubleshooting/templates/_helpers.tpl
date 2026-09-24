{{- define "parasol-troubleshooting.username" -}}
{{ .Values.tenant.username }}
{{- end -}}

{{- define "parasol-troubleshooting.prefix" -}}
{{ .Values.tenant.username }}
{{- end -}}

{{- define "parasol-troubleshooting.web-ns" -}}
{{ .Values.tenant.username }}-parasol-web
{{- end -}}

{{- define "parasol-troubleshooting.payments-ns" -}}
{{ .Values.tenant.username }}-parasol-payments
{{- end -}}
