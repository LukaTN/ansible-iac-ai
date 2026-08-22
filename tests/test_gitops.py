"""Guards for Phase 7 GitHub Actions + Argo CD manifests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
GITOPS = ROOT / "deploy" / "gitops"
WORKFLOWS = ROOT / ".github" / "workflows"
CHART = ROOT / "deploy" / "helm" / "ansibleai"


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_gitops_layout_exists() -> None:
    required = [
        GITOPS / "README.md",
        GITOPS / "applications" / "staging.yaml",
        GITOPS / "applications" / "production.yaml",
        CHART / "values-gitops-image.yaml",
        WORKFLOWS / "ci.yml",
        WORKFLOWS / "image.yml",
        WORKFLOWS / "eval-gate.yml",
        ROOT / "scripts" / "set_gitops_image.py",
        ROOT / "scripts" / "lab_eval_gate.py",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    assert missing == []


def test_staging_is_automated_prod_is_manual() -> None:
    staging = _yaml(GITOPS / "applications" / "staging.yaml")
    prod = _yaml(GITOPS / "applications" / "production.yaml")

    assert staging["kind"] == "Application"
    assert staging["metadata"]["namespace"] == "argocd"
    assert staging["spec"]["source"]["path"] == "deploy/helm/ansibleai"
    assert staging["spec"]["source"]["helm"]["releaseName"] == "ansibleai"
    assert staging["spec"]["source"]["helm"]["valueFiles"] == [
        "values-staging.yaml",
        "values-gitops-image.yaml",
    ]
    automated = staging["spec"]["syncPolicy"]["automated"]
    assert automated["prune"] is True
    assert automated["selfHeal"] is True
    assert staging["spec"]["destination"]["namespace"] == "ansibleai"

    assert prod["kind"] == "Application"
    assert "automated" not in (prod["spec"].get("syncPolicy") or {})
    assert prod["spec"]["destination"]["namespace"] == "ansibleai-prod"
    assert "values-prod.yaml" in prod["spec"]["source"]["helm"]["valueFiles"]


def test_gitops_repo_and_no_latest() -> None:
    image = _yaml(CHART / "values-gitops-image.yaml")
    assert image["image"]["tag"] != "latest"
    assert image["image"]["tag"]
    for name in ("staging.yaml", "production.yaml"):
        text = (GITOPS / "applications" / name).read_text(encoding="utf-8")
        assert ":latest" not in text
        assert "LukaTN/ansible-iac-ai" in text


def test_workflows_never_tag_latest() -> None:
    image = (WORKFLOWS / "image.yml").read_text(encoding="utf-8")
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    code_lines = [line.split("#", 1)[0] for line in image.splitlines()]
    assert "latest=false" in image
    assert "type=raw,value=latest" not in image
    assert not any(":latest" in line for line in code_lines)
    assert "github.sha" in image
    assert "trivy-action@" in image
    assert "0.31.0" not in image
    assert "sbom-action" in image
    assert "ruff check" in ci
    assert "pytest tests/" in ci
    assert "helm template" in ci
    assert "eval_gate.py" in ci


def test_eval_gate_workflow_is_dispatch_only() -> None:
    # PyYAML 1.1 treats the key `on` as boolean True.
    data = _yaml(WORKFLOWS / "eval-gate.yml")
    trigger = data.get("on", data.get(True))
    assert trigger is not None
    assert "workflow_dispatch" in trigger
    assert "push" not in trigger
    text = (WORKFLOWS / "eval-gate.yml").read_text(encoding="utf-8")
    assert "lab_eval_gate.py" in text
    assert "self-hosted" in text


def test_set_gitops_image_refuses_latest(tmp_path: Path) -> None:
    mod = _load_script("set_gitops_image.py")
    with pytest.raises(ValueError, match="latest"):
        mod.validate_tag("latest")
    with pytest.raises(ValueError, match="latest"):
        mod.validate_tag(":LATEST")
    assert mod.validate_tag("abc123def") == "abc123def"
    dest = tmp_path / "values-gitops-image.yaml"
    written = mod.render(
        repository="ghcr.io/lukatn/ansible-iac-ai",
        tag="deadbeef",
        pull_policy="IfNotPresent",
    )
    dest.write_text(written, encoding="utf-8")
    parsed = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert parsed["image"]["tag"] == "deadbeef"
    assert parsed["image"]["repository"] == "ghcr.io/lukatn/ansible-iac-ai"


def test_set_gitops_image_cli_refuses_latest(monkeypatch, capsys) -> None:
    mod = _load_script("set_gitops_image.py")
    monkeypatch.setattr("sys.argv", ["set_gitops_image.py", "--tag", "latest"])
    assert mod.main() == 2
    assert "latest" in capsys.readouterr().err


def test_lab_eval_gate_missing_args_is_not_a_pass(monkeypatch) -> None:
    mod = _load_script("lab_eval_gate.py")
    monkeypatch.setattr("sys.argv", ["lab_eval_gate.py"])
    assert mod.main() == 2
