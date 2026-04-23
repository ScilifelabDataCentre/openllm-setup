{{- define "openwebui-kth-cluster-helm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- include "openwebui-kth-cluster-helm.name" . -}}
{{- end -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.namespace" -}}
{{- default .Release.Namespace .Values.namespace.name -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.labels" -}}
app.kubernetes.io/name: {{ include "openwebui-kth-cluster-helm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.selectorLabels" -}}
app.kubernetes.io/name: {{ include "openwebui-kth-cluster-helm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (printf "%s-tailscale" (include "openwebui-kth-cluster-helm.fullname" .)) .Values.serviceAccount.name -}}
{{- else -}}
{{- .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.pvcName" -}}
{{- if .Values.persistence.existingClaim -}}
{{- .Values.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-pvc" (include "openwebui-kth-cluster-helm.fullname" .) -}}
{{- end -}}
{{- end -}}
