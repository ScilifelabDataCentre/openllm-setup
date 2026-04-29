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

{{- define "openwebui-kth-cluster-helm.qdrantLabels" -}}
app.kubernetes.io/name: qdrant
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.qdrantSelectorLabels" -}}
app.kubernetes.io/name: qdrant
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

{{- define "openwebui-kth-cluster-helm.postgresqlName" -}}
{{- if .Values.postgresql.fullnameOverride -}}
{{- .Values.postgresql.fullnameOverride -}}
{{- else -}}
{{- printf "%s-postgresql" .Release.Name -}}
{{- end -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.postgresqlSecretName" -}}
{{- if .Values.postgresql.auth.existingSecret -}}
{{- .Values.postgresql.auth.existingSecret -}}
{{- else -}}
{{- include "openwebui-kth-cluster-helm.postgresqlName" . -}}
{{- end -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.qdrantSecretName" -}}
{{- if .Values.vectorDatabase.qdrant.auth.existingSecret -}}
{{- .Values.vectorDatabase.qdrant.auth.existingSecret -}}
{{- else if .Values.vectorDatabase.qdrant.auth.secretName -}}
{{- .Values.vectorDatabase.qdrant.auth.secretName -}}
{{- else -}}
{{- printf "%s-qdrant" (include "openwebui-kth-cluster-helm.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.qdrantSecretKey" -}}
{{- default "api-key" .Values.vectorDatabase.qdrant.auth.secretKey -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.qdrantApiKeyValue" -}}
{{- $secretName := include "openwebui-kth-cluster-helm.qdrantSecretName" . -}}
{{- $secretKey := include "openwebui-kth-cluster-helm.qdrantSecretKey" . -}}
{{- $existing := lookup "v1" "Secret" (include "openwebui-kth-cluster-helm.namespace" .) $secretName -}}
{{- if .Values.vectorDatabase.qdrant.auth.value -}}
{{- .Values.vectorDatabase.qdrant.auth.value -}}
{{- else if and $existing (hasKey $existing.data $secretKey) -}}
{{- index $existing.data $secretKey | b64dec -}}
{{- else -}}
{{- randAlphaNum 48 -}}
{{- end -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.qdrantNamespace" -}}
{{- default (include "openwebui-kth-cluster-helm.namespace" .) .Values.vectorDatabase.qdrant.namespace -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.qdrantServiceName" -}}
{{- if .Values.vectorDatabase.qdrant.serviceName -}}
{{- .Values.vectorDatabase.qdrant.serviceName -}}
{{- else -}}
{{- printf "%s-qdrant" (include "openwebui-kth-cluster-helm.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.qdrantPvcName" -}}
{{- if .Values.vectorDatabase.qdrant.persistence.existingClaim -}}
{{- .Values.vectorDatabase.qdrant.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-qdrant-pvc" (include "openwebui-kth-cluster-helm.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "openwebui-kth-cluster-helm.qdrantUri" -}}
{{- if .Values.vectorDatabase.qdrant.uri -}}
{{- .Values.vectorDatabase.qdrant.uri -}}
{{- else -}}
{{- printf "%s://%s.%s.svc.cluster.local:%s" .Values.vectorDatabase.qdrant.scheme (include "openwebui-kth-cluster-helm.qdrantServiceName" .) (include "openwebui-kth-cluster-helm.qdrantNamespace" .) .Values.vectorDatabase.qdrant.port -}}
{{- end -}}
{{- end -}}
