{{- define "cw.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cw.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "cw.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "cw.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "cw.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cw.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Frontend: another name, so the backend's selectors (Service, NetworkPolicy, ServiceMonitor)
     never match its pods or Service. */}}
{{- define "cw.frontendSelectorLabels" -}}
app.kubernetes.io/name: {{ include "cw.name" . }}-frontend
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "cw.frontendLabels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{ include "cw.frontendSelectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.Version | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "cw.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "cw.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "cw.secretName" -}}
{{- default (printf "%s-secrets" (include "cw.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "cw.hasSeed" -}}
{{- if or .Values.seed.environments .Values.seed.projects .Values.seed.webhooks -}}true{{- end -}}
{{- end -}}

{{/* Language of the chart's texts (NOTES, alerts, dashboard): "pt-BR" or "en". */}}
{{- define "cw.lang" -}}
{{- $l := lower (toString (.Values.config.language | default "en")) -}}
{{- if or (eq $l "pt-br") (eq $l "pt_br") (eq $l "pt") -}}pt-BR{{- else -}}en{{- end -}}
{{- end }}
