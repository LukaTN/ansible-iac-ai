{{/*
Expand the name of the chart.
*/}}
{{- define "ansibleai.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ansibleai.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "ansibleai.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ansibleai.labels" -}}
helm.sh/chart: {{ include "ansibleai.chart" . }}
{{ include "ansibleai.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: ansibleai
{{- end }}

{{- define "ansibleai.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ansibleai.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "ansibleai.componentLabels" -}}
{{ include "ansibleai.labels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "ansibleai.image" -}}
{{- $tag := .tag | default .root.Chart.AppVersion }}
{{- if or (eq $tag "latest") (eq $tag "") }}
{{- fail "image tag must be a pinned version or git SHA, never empty or latest" }}
{{- end }}
{{- printf "%s:%s" .repository $tag }}
{{- end }}

{{- define "ansibleai.secretName" -}}
{{- if .Values.secrets.existingSecret }}
{{- .Values.secrets.existingSecret }}
{{- else }}
{{- printf "%s-app" (include "ansibleai.fullname" .) }}
{{- end }}
{{- end }}

{{- define "ansibleai.postgresHost" -}}
{{- if .Values.postgres.enabled }}
{{- printf "%s-postgres" (include "ansibleai.fullname" .) }}
{{- else }}
{{- required "external.postgres.host is required when postgres.enabled=false" .Values.external.postgres.host }}
{{- end }}
{{- end }}

{{- define "ansibleai.redisHost" -}}
{{- if .Values.redis.enabled }}
{{- printf "%s-redis" (include "ansibleai.fullname" .) }}
{{- else }}
{{- required "external.redis.host is required when redis.enabled=false" .Values.external.redis.host }}
{{- end }}
{{- end }}

{{- define "ansibleai.minioHost" -}}
{{- if .Values.minio.enabled }}
{{- printf "%s-minio" (include "ansibleai.fullname" .) }}
{{- else }}
{{- required "external.minio.host is required when minio.enabled=false" .Values.external.minio.host }}
{{- end }}
{{- end }}

{{- define "ansibleai.ollamaHost" -}}
{{- printf "%s-ollama" (include "ansibleai.fullname" .) }}
{{- end }}

{{- define "ansibleai.ollamaBaseUrl" -}}
{{- printf "http://%s:%v" .Values.ollama.endpoint.ip .Values.ollama.port }}
{{- end }}

{{- define "ansibleai.corsOrigins" -}}
{{- if .Values.app.corsOrigins }}
{{- .Values.app.corsOrigins }}
{{- else }}
{{- $p := .Values.lab.ingressNodePort }}
{{- printf "http://%s:%v,http://%s:%v,http://%s:%v,http://localhost:%v" .Values.ingress.host $p .Values.lab.masterIp $p .Values.lab.workerIp $p $p }}
{{- end }}
{{- end }}

{{- define "ansibleai.memberUrl" -}}
{{- printf "http://%s:%v" .Values.lab.masterIp .Values.lab.ingressNodePort }}
{{- end }}

{{- define "ansibleai.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .Values.security.runAsUser }}
runAsGroup: {{ .Values.security.runAsGroup }}
fsGroup: {{ .Values.security.fsGroup }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "ansibleai.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop:
    - ALL
{{- end }}

{{- define "ansibleai.tmpVolumeMounts" -}}
- name: tmp
  mountPath: /tmp
- name: home
  mountPath: /home/app
{{- end }}

{{- define "ansibleai.tmpVolumes" -}}
- name: tmp
  emptyDir:
    medium: Memory
    sizeLimit: {{ .Values.tmpfs.tmpSize | quote }}
- name: home
  emptyDir:
    medium: Memory
    sizeLimit: {{ .Values.tmpfs.homeSize | quote }}
{{- end }}

{{- define "ansibleai.appImage" -}}
{{- include "ansibleai.image" (dict "repository" .Values.image.repository "tag" .Values.image.tag "root" .) }}
{{- end }}

{{- define "ansibleai.pinnedImage" -}}
{{- if or (eq .tag "latest") (eq .tag "") }}
{{- fail (printf "%s image tag must be a pinned version, never empty or latest" .name) }}
{{- end }}
{{- printf "%s:%s" .repository .tag }}
{{- end }}

{{- define "ansibleai.configMapName" -}}
{{- printf "%s-config" (include "ansibleai.fullname" .) }}
{{- end }}

{{- define "ansibleai.postgresName" -}}
{{- printf "%s-postgres" (include "ansibleai.fullname" .) }}
{{- end }}

{{- define "ansibleai.redisName" -}}
{{- printf "%s-redis" (include "ansibleai.fullname" .) }}
{{- end }}

{{- define "ansibleai.minioName" -}}
{{- printf "%s-minio" (include "ansibleai.fullname" .) }}
{{- end }}

{{- define "ansibleai.saApi" -}}
{{- printf "%s-api" (include "ansibleai.fullname" .) }}
{{- end }}

{{- define "ansibleai.saWorker" -}}
{{- printf "%s-worker" (include "ansibleai.fullname" .) }}
{{- end }}

{{- define "ansibleai.saMigrate" -}}
{{- printf "%s-migrate" (include "ansibleai.fullname" .) }}
{{- end }}

{{- define "ansibleai.envFrom" -}}
envFrom:
  - configMapRef:
      name: {{ include "ansibleai.configMapName" . }}
  - secretRef:
      name: {{ include "ansibleai.secretName" . }}
{{- end }}

{{- define "ansibleai.waitPostgres" -}}
- name: wait-postgres
  image: {{ include "ansibleai.appImage" . }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  args:
    - exec
    - python
    - -c
    - |
      import os, socket, sys, time
      from urllib.parse import urlparse
      raw = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://", 1)
      parsed = urlparse(raw)
      host, port = parsed.hostname, parsed.port or 5432
      for _ in range(90):
          try:
              socket.create_connection((host, port), 3).close()
              sys.exit(0)
          except OSError:
              time.sleep(2)
      sys.exit(1)
  env:
    - name: DATABASE_URL
      valueFrom:
        secretKeyRef:
          name: {{ include "ansibleai.secretName" . }}
          key: DATABASE_URL
  securityContext:
    {{- include "ansibleai.containerSecurityContext" . | nindent 4 }}
  resources:
    requests:
      cpu: 10m
      memory: 32Mi
    limits:
      cpu: 100m
      memory: 64Mi
  volumeMounts:
    {{- include "ansibleai.tmpVolumeMounts" . | nindent 4 }}
{{- end }}

{{- define "ansibleai.waitSchema" -}}
- name: wait-schema
  image: {{ include "ansibleai.appImage" . }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  args:
    - exec
    - python
    - -c
    - |
      import os, sys, time
      from sqlalchemy import create_engine, inspect
      url = os.environ["DATABASE_URL"]
      for _ in range(90):
          try:
              engine = create_engine(url)
              tables = set(inspect(engine).get_table_names())
              engine.dispose()
              if {"users", "chat_threads", "chat_messages"} <= tables:
                  sys.exit(0)
          except Exception:
              pass
          time.sleep(2)
      sys.exit(1)
  env:
    - name: DATABASE_URL
      valueFrom:
        secretKeyRef:
          name: {{ include "ansibleai.secretName" . }}
          key: DATABASE_URL
  securityContext:
    {{- include "ansibleai.containerSecurityContext" . | nindent 4 }}
  resources:
    requests:
      cpu: 10m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 128Mi
  volumeMounts:
    {{- include "ansibleai.tmpVolumeMounts" . | nindent 4 }}
{{- end }}

