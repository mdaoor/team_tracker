{{- define "tt.name" -}}{{ .root.Release.Name }}-{{ .component }}{{- end -}}
{{- define "tt.labels" -}}
app.kubernetes.io/name: team-tracker
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}
{{- define "tt.env" -}}
- {name: TT_ENV, value: {{ .Values.config.env | quote }}}
- {name: TT_LOG_JSON, value: {{ .Values.config.logJson | quote }}}
- {name: TT_WEB_ORIGIN, value: {{ .Values.config.webOrigin | quote }}}
- {name: TT_FEATURES__JIRA_SYNC, value: {{ .Values.config.features.jiraSync | quote }}}
- {name: TT_FEATURES__GOVERNANCE, value: {{ .Values.config.features.governance | quote }}}
- {name: TT_FEATURES__LLM_SUGGESTIONS, value: {{ .Values.config.features.llmSuggestions | quote }}}
- {name: TT_ATTACHMENT_DIR, value: /var/lib/team-tracker/attachments}
{{- if .Values.config.llmGatewayUrl }}
- {name: TT_LLM_GATEWAY_URL, value: {{ .Values.config.llmGatewayUrl | quote }}}
{{- end }}
{{- if not .Values.tls.secretName }}
- {name: TT_TRUST_FORWARDED_PROTO, value: "true"}
- {name: TT_FORCE_HTTPS, value: "true"}
{{- end }}
{{- end -}}
