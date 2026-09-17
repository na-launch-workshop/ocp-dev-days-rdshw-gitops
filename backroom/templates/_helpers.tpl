{{/*
Common sync policy for backroom child Applications.
*/}}
{{- define "backroom.syncPolicy" -}}
syncPolicy:
  automated:
    prune: true
    selfHeal: true
  retry:
    backoff:
      duration: 5s
      factor: 2
      maxDuration: 2m0s
    limit: 30
  syncOptions:
    - CreateNamespace=true
    - RespectIgnoreDifferences=true
    - SkipDryRunOnMissingResource=true
{{- end }}

{{/*
Common Application metadata with foreground finalizer.
Usage: {{ include "backroom.metadata" (dict "name" "my-app" "namespace" .Values.argocd.namespace "syncWave" "0") }}
*/}}
{{- define "backroom.metadata" -}}
metadata:
  name: {{ .name }}
  namespace: {{ .namespace }}
  labels:
    app.kubernetes.io/part-of: backroom
  annotations:
    argocd.argoproj.io/sync-wave: "{{ .syncWave }}"
  finalizers:
    - resources-finalizer.argocd.argoproj.io/foreground
{{- end }}
