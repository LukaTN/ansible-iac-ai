"""
Tests for the ansible-lint output parsing and the lint-gate environment.

Regression context: the runner used to count every stdout/stderr line as a
violation, so one real finding plus six cache-directory UserWarnings became
"ansible-lint reported 14 violation(s)", and the repair loop received
unfixable warnings as repairable issues.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from pipeline.ansible_lint_runner import _interpret_result

ROOT = Path(__file__).resolve().parent.parent


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ansible-lint"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestInterpretResult:
    def test_clean_run_passes(self):
        outcome = _interpret_result(_proc(0), "native")
        assert outcome.status == "passed"
        assert outcome.violations == []

    def test_real_findings_are_counted_without_warning_noise(self):
        stdout = (
            "/tmp/playbooks/pb.yml:6:5: syntax-check[unknown-module]: "
            "couldn't resolve module/action 'amazon.aws.kms_key'.\n"
        )
        stderr = (
            "/opt/venv/lib/python3.12/site-packages/ansiblelint/__main__.py:138: "
            "UserWarning: Project directory /.ansible cannot be used for caching "
            "as it is not writable.\n"
            "  options.cache_dir = get_cache_dir(pathlib.Path(options.project_dir))\n"
            "WARNING  Using unique temporary directory /tmp/.ansible-0aaa for caching.\n"
        )
        outcome = _interpret_result(_proc(2, stdout=stdout, stderr=stderr), "native")
        assert outcome.status == "violations"
        assert len(outcome.violations) == 1
        assert "kms_key" in outcome.violations[0]

    def test_findings_without_column_number_still_match(self):
        stdout = "pb.yml:12: name[missing]: All tasks should be named\n"
        outcome = _interpret_result(_proc(2, stdout=stdout), "native")
        assert outcome.status == "violations"
        assert len(outcome.violations) == 1

    def test_nonzero_exit_without_findings_is_tool_failure_not_violations(self):
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "/opt/venv/bin/ansible-lint", line 8, in <module>\n'
            "RuntimeError: boom\n"
        )
        outcome = _interpret_result(_proc(1, stderr=stderr), "native")
        assert outcome.status == "failed_to_run"

    def test_failed_to_run_is_environmental_for_the_gate(self):
        from agent.state import _LINT_ENV_STATUSES

        assert "failed_to_run" in _LINT_ENV_STATUSES


class TestLintCollectionsEnvironment:
    """The image must be able to resolve every module the RAG corpus covers."""

    def test_collections_requirements_file_covers_the_scraped_corpus(self):
        reqs = yaml.safe_load(
            (ROOT / "docker" / "ansible-collections.yml").read_text(encoding="utf-8")
        )
        names = {c["name"] for c in reqs["collections"]}
        # data/parsed/ holds these four galaxy collections (+ ansible.builtin,
        # which ships inside ansible-core and needs no install).
        assert names >= {
            "amazon.aws",
            "azure.azcollection",
            "community.general",
            "kubernetes.core",
        }

    def test_dockerfile_installs_and_exposes_the_collections(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "ansible-galaxy collection install" in dockerfile
        assert "ANSIBLE_COLLECTIONS_PATH=/opt/ansible/collections" in dockerfile
