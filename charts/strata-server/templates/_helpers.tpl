{{- define "strata-server.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "strata-server.fullname" -}}
{{- .Release.Name -}}
{{- end -}}

{{- define "strata-server.labels" -}}
app.kubernetes.io/name: {{ include "strata-server.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "strata-server.postgresFullname" -}}
{{- printf "%s-postgres" (include "strata-server.fullname" .) -}}
{{- end -}}

{{- define "strata-server.tlsSecretName" -}}
{{- if .Values.tls.secretName -}}
{{ .Values.tls.secretName }}
{{- else -}}
{{ printf "%s-tls" (include "strata-server.fullname" .) }}
{{- end -}}
{{- end -}}

{{/*
Event-store connection env vars, shared by the server Deployment and the
migrate Job. Exactly one of three sources is used, in priority order:
  1. db.existingSecret        - an existing Secret holding the full URL
  2. db.url                   - a literal URL (dev/testing only)
  3. postgresql.enabled       - composed from the bundled postgres Secret via
                                 Kubernetes' own $(VAR) env-value substitution,
                                 so the password never needs to be known at
                                 Helm template time.
*/}}
{{- define "strata-server.dbEnvVars" -}}
{{- if .Values.db.existingSecret }}
- name: STRATA_SERVE_DB_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.db.existingSecret }}
      key: {{ .Values.db.existingSecretKey | default "db-url" }}
{{- else if .Values.db.url }}
- name: STRATA_SERVE_DB_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "strata-server.fullname" . }}-db
      key: db-url
{{- else if .Values.postgresql.enabled }}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ if .Values.postgresql.auth.existingSecret }}{{ .Values.postgresql.auth.existingSecret }}{{ else }}{{ include "strata-server.postgresFullname" . }}{{ end }}
      key: postgres-password
- name: STRATA_SERVE_DB_URL
  value: {{ printf "postgresql+psycopg://%s:$(POSTGRES_PASSWORD)@%s:%v/%s" .Values.postgresql.auth.username (include "strata-server.postgresFullname" .) .Values.postgresql.service.port .Values.postgresql.auth.database | quote }}
{{- else }}
{{- fail "Set db.existingSecret, db.url, or enable postgresql.enabled" }}
{{- end }}
{{- end -}}
