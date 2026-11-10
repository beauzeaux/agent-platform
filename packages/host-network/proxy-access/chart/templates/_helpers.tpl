{{- define "agent-proxies.name" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "agent-proxies.selector" -}}
app.kubernetes.io/name: agent-proxies
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
{{- define "agent-proxies.labels" -}}
{{ include "agent-proxies.selector" . }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}
