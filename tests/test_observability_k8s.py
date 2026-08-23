"""Guards for Phase 6c cluster observability (GitOps + lab values)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OBS = ROOT / "deploy" / "observability" / "k8s"
GITOPS = ROOT / "deploy" / "gitops" / "applications"
CHART = ROOT / "deploy" / "helm" / "ansibleai"


def _yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_phase6c_layout_exists() -> None:
    required = [
        OBS / "README.md",
        OBS / "images.txt",
        OBS / "values" / "kube-prometheus-lab.yaml",
        OBS / "values" / "loki-lab.yaml",
        OBS / "values" / "tempo-lab.yaml",
        OBS / "values" / "alloy-lab.yaml",
        OBS / "values" / "langfuse-lab.yaml",
        GITOPS / "kube-prometheus.yaml",
        GITOPS / "loki.yaml",
        GITOPS / "tempo.yaml",
        GITOPS / "alloy.yaml",
        GITOPS / "langfuse.yaml",
        ROOT / "scripts" / "lab_install_observability.sh",
        CHART / "templates" / "servicemonitor.yaml",
        CHART / "templates" / "deployment-celery-exporter.yaml",
    ]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file()]
    assert missing == []


def test_obs_values_never_use_latest() -> None:
    for path in (OBS / "values").glob("*.yaml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#", 1)[0]
            assert ":latest" not in stripped, f"{path.name} mentions :latest"


def test_obs_images_txt_is_pinned() -> None:
    text = (OBS / "images.txt").read_text(encoding="utf-8")
    assert "danihodovic/celery-exporter:0.12.2" in text
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        assert ":latest" not in stripped
        assert ":" in stripped


def test_kube_prometheus_app_is_multisource_and_automated() -> None:
    app = _yaml(GITOPS / "kube-prometheus.yaml")
    assert app["kind"] == "Application"
    sources = app["spec"]["sources"]
    assert sources[0]["chart"] == "kube-prometheus-stack"
    assert sources[0]["targetRevision"] == "88.5.2"
    assert "automated" in app["spec"]["syncPolicy"]
    assert app["spec"]["destination"]["namespace"] == "observability"


def test_langfuse_app_is_manual() -> None:
    app = _yaml(GITOPS / "langfuse.yaml")
    policy = app["spec"].get("syncPolicy") or {}
    assert "automated" not in policy
    assert app["spec"]["destination"]["namespace"] == "langfuse"


def test_alloy_disables_quay_config_reloader() -> None:
    values = _yaml(OBS / "values" / "alloy-lab.yaml")
    assert values["configReloader"]["enabled"] is False


def test_loki_is_single_binary() -> None:
    values = _yaml(OBS / "values" / "loki-lab.yaml")
    assert values["deploymentMode"] == "SingleBinary"
    assert values["singleBinary"]["replicas"] == 1
    assert values["loki"]["storage"]["type"] == "filesystem"


def test_staging_enables_cluster_scrape() -> None:
    staging = _yaml(CHART / "values-staging.yaml")
    assert staging["observability"]["serviceMonitor"]["enabled"] is True
    assert staging["observability"]["prometheusRule"]["enabled"] is True
    assert staging["celeryExporter"]["enabled"] is True
    defaults = _yaml(CHART / "values.yaml")
    assert defaults["celeryExporter"]["image"]["tag"] != "latest"
    assert defaults["observability"]["releaseLabel"] == "kube-prometheus"


def test_servicemonitor_and_rules_are_operator_shaped() -> None:
    sm = (CHART / "templates" / "servicemonitor.yaml").read_text(encoding="utf-8")
    rules = (CHART / "templates" / "prometheusrule.yaml").read_text(encoding="utf-8")
    assert "monitoring.coreos.com/v1" in sm
    assert "/metrics" in sm
    assert "release:" in sm
    assert "AnsibleAICeleryQueueBacklog" in rules
    assert "celery_active_worker_count" in rules


def test_install_script_pins_chart_versions() -> None:
    script = (ROOT / "scripts" / "lab_install_observability.sh").read_text(encoding="utf-8")
    assert "--version 88.5.2" in script
    assert "--version 6.29.0" in script
    assert "--version 1.23.2" in script
    assert "ansibleai-overview.json" in script
    assert "--with-langfuse" in script
    assert "pod-security.kubernetes.io/enforce=privileged" in script
    assert "pending-install" in script
    assert "prometheusOperator.tls.enabled=false" in script
    assert "configReloader.enabled=false" in script
    assert ":latest" not in script


def test_langfuse_values_match_chart_1_5_1() -> None:
    values = _yaml(OBS / "values" / "langfuse-lab.yaml")
    resources = values["langfuse"].get("resources") or {}
    assert "web" not in resources
    assert "worker" not in resources
    web = values["langfuse"]["web"]
    assert web["service"]["type"] == "NodePort"
    assert web["service"]["nodePort"] == 30301
    assert "requests" in web["resources"]
    assert "requests" in values["langfuse"]["worker"]["resources"]
    assert values["clickhouse"]["replicaCount"] == 1
    assert values["clickhouse"]["clusterEnabled"] is False


def test_observability_namespace_is_privileged() -> None:
    text = (CHART / "templates" / "namespaces.yaml").read_text(encoding="utf-8")
    assert "namespaces.observability" in text
    assert text.count("enforce: privileged") >= 1
    values = _yaml(OBS / "values" / "kube-prometheus-lab.yaml")
    exporter = values["prometheus-node-exporter"]
    assert exporter["hostNetwork"] is False
    assert exporter["hostPID"] is False
    assert values["prometheusOperator"]["admissionWebhooks"]["enabled"] is False
    assert values["prometheusOperator"]["tls"]["enabled"] is False
