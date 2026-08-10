"""
Unit tests for the LangGraph agent: production-ready gate semantics and
the draft → gate → repair loop (all LLM calls and tools mocked — no
network, no vectorstore, no ansible-lint).
"""

from __future__ import annotations

import json

import pytest

import agent.graph as graph_mod
from agent.graph import _route_from_gate, build_graph
from agent.state import build_initial_state, evaluate_gate, format_repair_feedback

# ─────────────────────────────────────────────
#  evaluate_gate
# ─────────────────────────────────────────────

def _validation(
    errors=None, warnings=None, lint_status="passed", lint_violations=None,
):
    return {
        "is_valid": not errors,
        "errors": list(errors or []),
        "warnings": list(warnings or []),
        "passed": [],
        "ansible_lint": {
            "status": lint_status,
            "violations": list(lint_violations or []),
        },
    }


class TestEvaluateGate:
    def test_clean_validation_and_lint_is_ready(self):
        ready, repairable, env = evaluate_gate(_validation())
        assert ready is True
        assert repairable == []
        assert env == []

    def test_validator_errors_are_repairable(self):
        ready, repairable, env = evaluate_gate(
            _validation(errors=["Missing required param: region"])
        )
        assert ready is False
        assert any("region" in f for f in repairable)
        assert env == []

    def test_lint_violations_are_repairable(self):
        ready, repairable, _ = evaluate_gate(
            _validation(lint_status="violations",
                        lint_violations=["fqcn[action-core]: use ansible.builtin.service"])
        )
        assert ready is False
        assert any("fqcn" in f for f in repairable)

    def test_lint_skipped_blocks_gate_as_environmental(self):
        ready, repairable, env = evaluate_gate(_validation(lint_status="skipped"))
        assert ready is False
        assert repairable == []
        assert len(env) == 1 and "ansible-lint could not run" in env[0]

    def test_placeholder_warnings_promoted_to_failures(self):
        ready, repairable, _ = evaluate_gate(
            _validation(warnings=["Possible placeholder values found: ['your-namespace']"])
        )
        assert ready is False
        assert any("placeholder" in f for f in repairable)

    def test_non_placeholder_warnings_do_not_block(self):
        ready, repairable, _ = evaluate_gate(
            _validation(warnings=["Consider adding tags for filtering"])
        )
        assert ready is True
        assert repairable == []

    def test_draft_issues_included(self):
        ready, repairable, _ = evaluate_gate(
            _validation(), draft_issues=["Invalid module detected: kubernetes.core.k8s_resource"]
        )
        assert ready is False
        assert any("k8s_resource" in f for f in repairable)

    def test_missing_validation_fails(self):
        ready, repairable, _ = evaluate_gate(None)
        assert ready is False
        assert repairable == ["Validation did not run"]


class TestGateRouting:
    def test_ready_goes_to_respond(self):
        assert _route_from_gate({"gate_ready": True}) == "respond"

    def test_repairable_with_budget_goes_to_reason(self):
        state = {"gate_ready": False, "gate_failures": ["x"],
                 "iteration": 1, "max_iterations": 4}
        assert _route_from_gate(state) == "reason"

    def test_budget_exhausted_goes_to_respond(self):
        state = {"gate_ready": False, "gate_failures": ["x"],
                 "iteration": 4, "max_iterations": 4}
        assert _route_from_gate(state) == "respond"

    def test_environmental_only_goes_to_respond(self):
        state = {"gate_ready": False, "gate_failures": [],
                 "gate_environment": ["lint unavailable"],
                 "iteration": 1, "max_iterations": 4}
        assert _route_from_gate(state) == "respond"


def test_format_repair_feedback_numbers_items():
    fb = format_repair_feedback(["a", "b"])
    assert fb.splitlines() == ["1. a", "2. b"]
    assert format_repair_feedback([]) == "none"


# ─────────────────────────────────────────────
#  Graph repair loop (mocked tools + LLM)
# ─────────────────────────────────────────────

_SEARCH_PAYLOAD = {
    "query": "create ec2 instance",
    "primary_module": "amazon.aws.ec2_instance",
    "primary_collection": "amazon.aws",
    "score": 0.82,
    "source_url": "https://docs.example/ec2",
    "required_params": ["image_id"],
    "module_candidates": ["amazon.aws.ec2_instance"],
    "ranked_modules": [],
    "required_params_by_module": {},
    "chunks": [],
    "_retrieval_meta": {
        "docs": [],
        "scores": [],
        "primary_module": "amazon.aws.ec2_instance",
        "primary_collection": "amazon.aws",
        "primary_score": 0.82,
        "source_url": "https://docs.example/ec2",
    },
}


def _fake_llm_factory():
    def fake_llm(prompt, **kwargs):
        if "fix_plan" in prompt:  # REPAIR_PROMPT
            return json.dumps({
                "thought": "The task module is missing its FQCN prefix.",
                "fix_plan": "1. Use amazon.aws.ec2_instance as the task module name.",
                "needs_different_module": False,
                "search_query": "",
            })
        if "search_query" in prompt:  # REASON_PROMPT
            return json.dumps({
                "thought": "User wants an EC2 instance playbook.",
                "intent": "generate",
                "pivot": False,
                "search_query": "create ec2 instance",
                "ask_user": False,
                "questions": [],
            })
        return "Here is your summary."  # RESPOND_PROMPT
    return fake_llm


@pytest.fixture
def patched_graph(monkeypatch):
    """Patch every external dependency of the graph; return call recorders."""
    calls = {"draft": 0, "validate": 0}
    validations = []  # queue of validation payloads, one per gate pass

    def fake_resolve(query, planner_hint, pinned, pivot, top_k=8):
        return "amazon.aws", "vote", dict(_SEARCH_PAYLOAD)

    def fake_draft(user_request, retrieval_meta, *, conversation_facts=None,
                   feedback="none", fix_plan="none", existing_path=None):
        calls["draft"] += 1
        calls["last_feedback"] = feedback
        calls["last_fix_plan"] = fix_plan
        return {
            "yaml": f"---\n- name: Launch EC2 (draft {calls['draft']})\n",
            "path": "output/fake.yml",
            "filename": "fake.yml",
            "issues": [],
        }

    def fake_validate_file(filepath):
        calls["validate"] += 1
        return validations.pop(0) if validations else _validation()

    monkeypatch.setattr(graph_mod.T, "resolve_collection_with_prefetch", fake_resolve)
    monkeypatch.setattr(graph_mod.T, "search_docs",
                        lambda **kw: dict(_SEARCH_PAYLOAD))
    monkeypatch.setattr(graph_mod.T, "draft_playbook", fake_draft)
    monkeypatch.setattr(graph_mod.T, "validate_playbook_file", fake_validate_file)
    monkeypatch.setattr(graph_mod.T, "get_module_info",
                        lambda module: {"module": module})
    monkeypatch.setattr(graph_mod, "llm_chat", _fake_llm_factory())

    return calls, validations


def _run(message="create an ec2 instance named web-1", max_iterations=4):
    state = build_initial_state(1, message, [])
    state["max_iterations"] = max_iterations
    return build_graph().invoke(
        state, config={"configurable": {"on_progress": None}, "recursion_limit": 60},
    )


class TestRepairLoop:
    def test_first_draft_passing_gate_releases_playbook(self, patched_graph):
        calls, validations = patched_graph
        validations.append(_validation())  # clean on first pass

        final = _run()

        assert calls["draft"] == 1
        assert final["gate_ready"] is True
        assert final["draft_yaml"].startswith("---")
        assert "production-ready" in final["final_text"]

    def test_lint_failure_triggers_cot_repair_then_passes(self, patched_graph):
        calls, validations = patched_graph
        validations.append(_validation(
            lint_status="violations",
            lint_violations=["fqcn[action-core]: use FQCN for the module"],
        ))
        validations.append(_validation())  # repaired draft passes

        final = _run()

        assert calls["draft"] == 2
        assert final["iteration"] == 2
        assert final["gate_ready"] is True
        # The redraft received the gate failures AND the CoT fix plan.
        assert "fqcn" in calls["last_feedback"]
        assert "amazon.aws.ec2_instance" in calls["last_fix_plan"]

    def test_budget_exhaustion_returns_draft_marked_not_ready(self, patched_graph):
        calls, validations = patched_graph
        for _ in range(10):
            validations.append(_validation(
                lint_status="violations", lint_violations=["yaml[indentation]: bad indent"],
            ))

        final = _run(max_iterations=2)

        assert calls["draft"] == 2          # budget respected
        assert final["gate_ready"] is False
        assert final["draft_yaml"]          # best draft still returned
        assert "not** fully pass" in final["final_text"]
        assert any("yaml[indentation]" in f for f in final["gate_failures"])

    def test_lint_unavailable_stops_loop_without_wasting_drafts(self, patched_graph):
        calls, validations = patched_graph
        validations.append(_validation(lint_status="skipped"))

        final = _run()

        assert calls["draft"] == 1          # environmental failure: no redraft
        assert final["gate_ready"] is False
        assert final["gate_environment"]
        assert "Environment limitations" in final["final_text"]

    def test_chat_intent_skips_tools_and_drafting(self, patched_graph, monkeypatch):
        calls, _ = patched_graph

        def chat_llm(prompt, **kwargs):
            if "search_query" in prompt:
                return json.dumps({
                    "thought": "Just a greeting.", "intent": "chat", "pivot": False,
                    "search_query": "", "ask_user": False, "questions": [],
                })
            return "Hello! Ask me for an Ansible playbook."

        monkeypatch.setattr(graph_mod, "llm_chat", chat_llm)
        final = _run(message="hello there")

        assert calls["draft"] == 0
        assert final.get("draft_yaml") is None
        assert final["final_text"]

    def test_ask_user_decision_stops_before_generation(self, patched_graph, monkeypatch):
        calls, _ = patched_graph

        def ask_llm(prompt, **kwargs):
            return json.dumps({
                "thought": "Ambiguous stack.", "intent": "generate", "pivot": False,
                "search_query": "", "ask_user": True,
                "questions": ["Which observability backend do you want?"],
            })

        monkeypatch.setattr(graph_mod, "llm_chat", ask_llm)
        final = _run(message="collect metrics from my service")

        assert calls["draft"] == 0
        assert final["awaiting_user"] is True
        assert "observability backend" in final["final_text"]


class TestHandleMessageMapping:
    def test_agent_response_carries_playbook_and_validation(
        self, patched_graph, monkeypatch,
    ):
        _, validations = patched_graph
        validations.append(_validation())

        monkeypatch.setenv("AGENT_MAX_ITERATIONS", "4")
        from agent import handle_message

        resp = handle_message(
            thread_id=1,
            user_message="create an ec2 instance named web-1",
            history=[],
        )

        assert resp.playbook and resp.playbook.startswith("---")
        assert resp.filename == "fake.yml"
        assert resp.module  # detected or retrieval module
        assert resp.validation and resp.validation["is_valid"] is True
        assert resp.rag_meta and resp.rag_meta["primary_module"] == "amazon.aws.ec2_instance"
        assert resp.awaiting_user is False
        assert any(t.get("tool") == "gate" for t in resp.tool_trace)
