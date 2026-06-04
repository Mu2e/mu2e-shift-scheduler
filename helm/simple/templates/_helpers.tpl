{{/*
Expand the name of the chart.
*/}}
{{- define "mu2e-shifts-simple.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "mu2e-shifts-simple.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "mu2e-shifts-simple.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels for the web deployment/service pair.
*/}}
{{- define "mu2e-shifts-simple.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mu2e-shifts-simple.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: web
{{- end }}
