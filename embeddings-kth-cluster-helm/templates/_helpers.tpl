{{- define "embeddings.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "embeddings.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- default "embeddings" .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "embeddings.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "embeddings.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: embeddings
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "embeddings.selectorLabels" -}}
app.kubernetes.io/name: {{ include "embeddings.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
