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

{{/* Service the Ingress/IngressRoute/HTTPRoute sends traffic to: the frontend (nginx, which
     proxies the API to the backend) when enabled, else the backend. Both use service.port. */}}
{{- define "cw.routeService" -}}
{{ include "cw.fullname" . }}{{ if (.Values.frontend | default dict).enabled }}-frontend{{ end }}
{{- end -}}

{{/* IngressRoute/HTTPRoute: at least one host, only Prefix/Exact paths, and no backticks (they
     would break out of the Traefik rule, e.g. Host(`a`) || PathPrefix(`/`)). */}}
{{- define "cw.checkRouteHosts" -}}
{{- if not .Values.ingress.hosts }}{{ fail "ingress.hosts must not be empty" }}{{ end }}
{{- range .Values.ingress.hosts }}
{{- if contains "`" (toString .host) }}{{ fail "ingress.hosts: host must not contain backticks" }}{{ end }}
{{- if not .paths }}{{ fail "ingress.hosts: each host needs paths" }}{{ end }}
{{- range .paths }}
{{- if contains "`" (toString .path) }}{{ fail "ingress.hosts: path must not contain backticks" }}{{ end }}
{{- if not (has .pathType (list "Prefix" "Exact")) }}{{ fail (printf "ingress.hosts: pathType must be Prefix or Exact for %s (got %q)" $.Values.ingress.type .pathType) }}{{ end }}
{{- end }}
{{- end }}
{{- end -}}
