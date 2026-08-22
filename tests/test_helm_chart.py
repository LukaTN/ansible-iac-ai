"""Guards for the Phase 4b Helm chart."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "deploy" / "helm" / "ansibleai"


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_helm_chart_layout_exists() -> None:
    required = [
        CHART / "Chart.yaml",
        CHART / "values.yaml",
        CHART / "values-staging.yaml",
        CHART / "values-prod.yaml",
        CHART / "values-gitops-image.yaml",
        CHART / "values.schema.json",
        CHART / ".helmignore",
        CHART / "README.md",
        CHART / "templates" / "_helpers.tpl",
        CHART / "templates" / "NOTES.txt",
        CHART / "templates" / "serviceaccount.yaml",
        CHART / "templates" / "configmap.yaml",
        CHART / "templates" / "secret.yaml",
        CHART / "templates" / "deployment-api.yaml",
        CHART / "templates" / "deployment-worker.yaml",
        CHART / "templates" / "ingress.yaml",
        CHART / "templates" / "pdb.yaml",
        CHART / "templates" / "job-migrate.yaml",
        CHART / "templates" / "cronjob-reindex.yaml",
        CHART / "templates" / "networkpolicy.yaml",
        CHART / "templates" / "ollama.yaml",
        CHART / "templates" / "statefulset-postgres.yaml",
        CHART / "templates" / "keda-scaledobject.yaml",
        CHART / "templates" / "tests" / "test-connection.yaml",
        CHART / "templates" / "tests" / "test-ollama.yaml",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    assert missing == []


def test_chart_metadata() -> None:
    chart = _load_yaml(CHART / "Chart.yaml")
    assert chart["name"] == "ansibleai"
    assert chart["type"] == "application"
    assert str(chart["version"])
    assert "latest" not in str(chart["appVersion"]).lower()


def test_values_never_use_latest_tag() -> None:
    for name in (
        "values.yaml",
        "values-staging.yaml",
        "values-prod.yaml",
        "values-gitops-image.yaml",
    ):
        text = (CHART / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.split("#", 1)[0]
            if "tag:" not in stripped:
                continue
            value = stripped.split("tag:", 1)[1].strip().strip("\"'")
            assert value != "latest", f"{name} pins latest"
            assert value, f"{name} has an empty tag"


def test_staging_pins_lab_image_and_ollama() -> None:
    values = _load_yaml(CHART / "values-staging.yaml")
    assert values["image"]["tag"] == "dev"
    assert values["image"]["pullPolicy"] == "IfNotPresent"
    assert values["ollama"]["endpoint"]["ip"] == "192.168.1.14"
    assert values["lab"]["masterIp"] == "192.168.1.18"
    assert values["lab"]["workerIp"] == "192.168.1.12"
    defaults = _load_yaml(CHART / "values.yaml")
    assert defaults["ollama"]["endpoint"]["ip"] == "192.168.1.14"
    assert values["app"]["authMode"] == "local"
    assert values["identity"]["enabled"] is False
    assert values["keda"]["enabled"] is False
    assert values["networkPolicy"]["enabled"] is True
    assert values["localPathProvisioner"]["enabled"] is True
    assert values["ingress"]["defaultBackendToApi"] is True
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert ".Values.ingress.host" in helpers
    assert "corsOrigins" in helpers
    provisioner = (CHART / "templates" / "local-path-provisioner.yaml").read_text(encoding="utf-8")
    assert "kind: Role" in provisioner
    assert '"create"' in provisioner
    assert "namespace: local-path-storage" in provisioner


def test_security_defaults() -> None:
    values = _load_yaml(CHART / "values.yaml")
    assert values["security"]["runAsUser"] == 10001
    assert values["serviceAccounts"]["automountServiceAccountToken"] is False
    api = (CHART / "templates" / "deployment-api.yaml").read_text(encoding="utf-8")
    worker = (CHART / "templates" / "deployment-worker.yaml").read_text(encoding="utf-8")
    sa = (CHART / "templates" / "serviceaccount.yaml").read_text(encoding="utf-8")
    assert "ansibleai.saApi" in api
    assert "ansibleai.saWorker" in worker
    assert "default" not in sa
    assert "/healthz" in api
    assert "/readyz" in api
    assert "healthcheck" in worker
    assert "runAsNonRoot" in (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")


def test_ingress_sticky_and_no_oauth2_proxy() -> None:
    values = _load_yaml(CHART / "values.yaml")
    annotations = values["ingress"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/affinity"] == "cookie"
    assert annotations["nginx.ingress.kubernetes.io/session-cookie-name"] == "ansibleai-upstream"
    templates = (CHART / "templates").rglob("*")
    template_text = "".join(
        path.read_text(encoding="utf-8")
        for path in templates
        if path.is_file() and path.suffix in {".yaml", ".yml", ".tpl"}
    )
    assert "oauth2-proxy" not in template_text.lower()


def test_migrate_job_is_revisioned_not_preinstall_hook() -> None:
    text = (CHART / "templates" / "job-migrate.yaml").read_text(encoding="utf-8")
    assert "helm.sh/hook" not in text
    assert ".Release.Revision" in text
    assert 'args: ["migrate"]' in text


def test_rollback_documented() -> None:
    notes = (CHART / "templates" / "NOTES.txt").read_text(encoding="utf-8")
    readme = (CHART / "README.md").read_text(encoding="utf-8")
    assert "helm rollback" in notes
    assert "kubectl rollout undo" in notes
    assert "argocd app rollback" in notes
    assert "helm rollback" in readme


def test_vendor_images_are_pinned() -> None:
    values = _load_yaml(CHART / "values.yaml")
    assert values["postgres"]["image"]["tag"] != "latest"
    assert values["redis"]["image"]["tag"] != "latest"
    assert "RELEASE." in values["minio"]["image"]["tag"]
    assert values["minioBucketJob"]["enabled"] is True
    bucket_job = (CHART / "templates" / "job-minio-bucket.yaml").read_text(encoding="utf-8")
    assert "minio/mc" not in bucket_job
    assert "boto3" in bucket_job
    assert "ansibleai.appImage" in bucket_job
    ollama = (CHART / "templates" / "ollama.yaml").read_text(encoding="utf-8")
    assert "clusterIP: None" not in ollama
    assert "type: ClusterIP" in ollama
    configmap = (CHART / "templates" / "configmap.yaml").read_text(encoding="utf-8")
    assert "ansibleai.ollamaBaseUrl" in configmap
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert "ollama.endpoint.ip" in helpers
    assert "RAG_PARSED_DIR" in configmap
    reindex = (CHART / "templates" / "cronjob-reindex.yaml").read_text(encoding="utf-8")
    assert "restartPolicy: Never" in reindex
    assert "ansibleai.appScratchVolumeMounts" in reindex
    ingress = (CHART / "templates" / "ingress.yaml").read_text(encoding="utf-8")
    assert "defaultBackendToApi" in ingress
    assert "defaultBackend" in ingress
    ollama_test = (CHART / "templates" / "tests" / "test-ollama.yaml").read_text(encoding="utf-8")
    assert "ollamaBaseUrl" in ollama_test
    prod = _load_yaml(CHART / "values-prod.yaml")
    assert prod["ingress"]["defaultBackendToApi"] is False
    rule = (CHART / "templates" / "prometheusrule.yaml").read_text(encoding="utf-8")
    assert "AnsibleAIGatePassRateDrop" in rule
    assert "ansibleai_ready" in rule
    assert str(values["postgres"]["image"]["tag"]).startswith("0.")
