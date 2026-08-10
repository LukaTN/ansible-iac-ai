"""
=============================================================
  AnsibleAI RAG — Generator helpers (retrieval context → YAML prep)

  LLM playbook generation lives in agent/playbook_generator.py.
  This module keeps: context formatting, constraint checks, YAML extract,
  and file save with retrieval metadata headers.
=============================================================
"""

import os
import re
from collections.abc import Mapping
from datetime import datetime

import yaml
from dotenv import load_dotenv
from langchain_core.documents import Document

# Repository root (parent of backend/); data/ and .env live there.
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_ROOT)
os.chdir(PROJECT_ROOT)

# Load .env file from project root
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

OUTPUT_DIR  = "output"
MAX_RETRIES = 2


def ansible_jinja_var(param_name: str) -> str:
    """
    Placeholder value for module params: Ansible treats this as Jinja, not a plain string.
    Example: ansible_jinja_var('allocated_storage') -> '{{ var_allocated_storage }}'
    """
    p = (param_name or "").strip()
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", p):
        return "{{ var_invalid_placeholder }}"
    return f"{{{{ var_{p} }}}}"


# Words that can follow "cluster", "deployment", etc. in English but are not names.
_RESOURCE_NAME_FALSE_POSITIVES = frozenset(
    {
        "with", "the", "a", "an", "for", "and", "or", "in", "on", "to", "from", "by",
        "as", "is", "of", "using", "having", "without", "that", "this", "new", "my",
        "your", "be", "at", "into", "onto", "over", "under", "between", "within",
    }
)


def _sanitize_resource_name_token(tok: str | None) -> str | None:
    if not tok:
        return None
    t = tok.strip()
    if len(t) < 2:
        return None
    if t.lower() in _RESOURCE_NAME_FALSE_POSITIVES:
        return None
    return t


# ─────────────────────────────────────────────
#  CONTEXT BUILDER
# ─────────────────────────────────────────────

def build_context_string(docs: list[Document], scores: list[float]) -> str:
    """
    Build a formatted context string from retrieved docs.
    Ordered by score, with metadata headers.
    """
    blocks = []
    for doc, score in zip(docs, scores):
        meta  = doc.metadata
        header = (
            f"[{meta.get('collection')} | {meta.get('module')} | "
            f"{meta.get('chunk_type')} | relevance: {score:.3f}]"
        )
        blocks.append(f"{header}\n{doc.page_content}")

    return "\n\n---\n\n".join(blocks)


# ─────────────────────────────────────────────
#  YAML EXTRACTOR
# ─────────────────────────────────────────────

def extract_yaml(raw: str) -> str:
    """Extract clean YAML from LLM output."""
    # Strip markdown code fences
    fence = re.search(r"```(?:yaml|yml)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()

    # Find first --- marker
    lines     = raw.splitlines()
    start_idx = next((i for i, l in enumerate(lines) if l.strip() == "---"), None)
    if start_idx is not None:
        return "\n".join(lines[start_idx:]).strip()

    # Fallback: find first - name: line
    for i, line in enumerate(lines):
        if re.match(r"^-\s+name:", line):
            return "\n".join(lines[i:]).strip()

    return raw.strip()


# A YAML scalar that opens with `{` starts a flow mapping, so an unquoted
# `{{ ... }}` blows up the parse — "found unhashable key" — rather than
# reaching Ansible as a Jinja expression. Smaller models get this wrong
# regularly, and the repair loop cannot recover because the redraft makes
# the same mistake, so fix it in place instead of spending an iteration.
#
# Only scalars that *begin* with `{{` are affected; `name: web-{{ env }}`
# is already a valid plain scalar and is left alone.

# `key: <value>` and `- key: <value>`
_MAPPING_VALUE_RE = re.compile(r"^(?P<prefix>\s*(?:-\s+)?[^\s#][^:]*:\s+)(?P<value>\S.*?)\s*$")
# `- <value>`
_SEQUENCE_VALUE_RE = re.compile(r"^(?P<prefix>\s*-\s+)(?P<value>\S.*?)\s*$")
# a value sitting alone on its line
_BARE_VALUE_RE = re.compile(r"^(?P<prefix>\s*)(?P<value>\{\{.*?)\s*$")

_BLOCK_SCALAR_RE = re.compile(r":\s*[|>][+-]?\d*\s*$")


def _split_trailing_comment(value: str) -> tuple[str, str]:
    """Separate `{{ x }}  # note` into the expression and its comment."""
    end = value.rfind("}}")
    if end == -1:
        return value, ""
    tail = value[end + 2:]
    hash_at = tail.find("#")
    if hash_at == -1:
        return value, ""
    return (value[: end + 2] + tail[:hash_at]).rstrip(), tail[hash_at:].strip()


def _quote_scalar(value: str) -> str | None:
    """
    Wrap a Jinja expression in quotes YAML will accept, or None if it cannot
    be quoted safely (it already contains both quote characters).
    """
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    return None


def _quote_jinja_in_line(line: str) -> str | None:
    """Return the line with its Jinja scalar quoted, or None to leave it as is."""
    for pattern in (_MAPPING_VALUE_RE, _SEQUENCE_VALUE_RE, _BARE_VALUE_RE):
        m = pattern.match(line)
        if not m:
            continue
        value = m.group("value")
        if value[:1] in ('"', "'") or not value.startswith("{{"):
            return None
        expr, comment = _split_trailing_comment(value)
        quoted = _quote_scalar(expr)
        if quoted is None:
            return None
        return m.group("prefix") + quoted + (f"  {comment}" if comment else "")
    return None


def quote_bare_jinja(yaml_content: str) -> tuple[str, list[str]]:
    """
    Quote scalars that open with `{{` so the document parses.

    Runs only when the document does not already parse, which keeps it a
    repair path rather than a rewrite of every draft. Returns the (possibly
    unchanged) YAML and a description of each line that was fixed.
    """
    text = yaml_content or ""
    if not text.strip():
        return yaml_content, []
    try:
        yaml.safe_load(text)
        return yaml_content, []
    except yaml.YAMLError:
        pass

    out: list[str] = []
    fixes: list[str] = []
    block_indent: int | None = None

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        # Inside a literal/folded block the content is opaque text.
        if block_indent is not None:
            if stripped and indent > block_indent:
                out.append(line)
                continue
            block_indent = None

        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue

        if _BLOCK_SCALAR_RE.search(line):
            block_indent = indent
            out.append(line)
            continue

        repaired_line = _quote_jinja_in_line(line)
        if repaired_line is None:
            out.append(line)
        else:
            out.append(repaired_line)
            fixes.append(f"line {lineno}: {stripped[:60]}")

    repaired = "\n".join(out)
    if not fixes:
        return yaml_content, []
    try:
        yaml.safe_load(repaired)
    except yaml.YAMLError:
        # The quoting was not the (only) problem — leave the draft untouched
        # so the gate reports the original error rather than a mutated one.
        return yaml_content, []
    return repaired, fixes


def _extract_constraints(user_input: str) -> dict:
    """Parse strong constraints from natural language request."""
    text = user_input or ""
    lower = text.lower()

    def _grab(pattern: str) -> str | None:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            return None
        return m.group(1).strip().rstrip(".,")

    # Free-form resource name: "<type> rg-monitoring", "instance named foo".
    resource_name = _grab(
        r"(?:resource\s+group|instance|bucket|cluster|deployment|vm|virtual\s+machine|function|container)\s+"
        r"(?:called\s+|named\s+)?([a-zA-Z][a-zA-Z0-9\-_]{1,62})"
    )
    # `name: rg-monitoring` or `name=rg-monitoring` replies / captured facts.
    name_kv = _grab(r"(?:^|\s)name\s*[:=]\s*([a-zA-Z][a-zA-Z0-9\-_]{1,62})")

    location_kv = _grab(
        r"(?:location|region)\s*[:=]\s*([a-zA-Z][a-zA-Z0-9\-_]{1,40})"
    )
    subscription_kv = _grab(
        r"subscription(?:_id)?\s*[:=]\s*([a-fA-F0-9\-]{8,64})"
    )
    resource_group_kv = _grab(
        r"resource[_\s]group(?:_name)?\s*[:=]\s*([a-zA-Z][a-zA-Z0-9\-_]{1,62})"
    )

    node_count = _grab(r"\b(\d+)\s+nodes?\b")
    if not node_count:
        node_count = _grab(r"\bnodes?\s*(?:count\s*)?(?:of|with|:)?\s*(\d+)\b")
    if not node_count:
        node_count = _grab(r"\b(\d+)\s+node\s+(?:pool|aks)\b")

    vm_size = _grab(r"vm[_\s-]?size\s*[:=]\s*([A-Za-z0-9_\-]+)")
    if not vm_size:
        vm_size = _grab(r"\b(Standard_[A-Za-z0-9_]+)\b")

    kubernetes_version = _grab(
        r"kubernetes(?:\s+version)?\s*[:=]\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)"
    )
    if not kubernetes_version:
        kubernetes_version = _grab(r"\bk8s(?:\s+version)?\s*[:=]\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)")

    dns_prefix = _grab(r"dns[_\s-]?prefix\s*[:=]\s*([a-zA-Z][a-zA-Z0-9\-]{0,40})")

    constraints = {
        "resource_name": _sanitize_resource_name_token(name_kv)
        or _sanitize_resource_name_token(resource_name),
        "location": location_kv,
        "subscription_id": subscription_kv,
        "resource_group": resource_group_kv,
        "deployment_name": _grab(r"deployment\s+name\s*:\s*([^\n]+)"),
        "image": _grab(r"image\s*:\s*([^\n]+)"),
        "replicas": _grab(r"replicas\s*:\s*(\d+)"),
        "container_port": _grab(r"container\s+port\s*:\s*(\d+)"),
        "configmap_name": _grab(r"configmap\s+name\s*:\s*([^\n]+)"),
        "secret_name": _grab(r"secret\s+name\s*:\s*([^\n]+)"),
        "service_name": _grab(r"connect\s+to\s+([a-zA-Z0-9\-_.]+)"),
        "node_count": node_count,
        "vm_size": vm_size,
        "kubernetes_version": kubernetes_version,
        "dns_prefix": dns_prefix,
        "must_use_configmap": "configmap" in lower and ("load" in lower or "from the configmap" in lower),
        "must_use_secret": "secret" in lower and ("read" in lower or "from the secret" in lower),
        "allow_resource_creation": bool(re.search(r"\b(create|provision|define)\b.*\b(configmap|secret)\b", lower)),
    }

    if not constraints["configmap_name"] and constraints["deployment_name"] and constraints["must_use_configmap"]:
        constraints["configmap_name"] = f"{constraints['deployment_name']}-config"
    if not constraints["secret_name"] and constraints["deployment_name"] and constraints["must_use_secret"]:
        constraints["secret_name"] = f"{constraints['deployment_name']}-secret"

    return constraints


def _format_constraints(constraints: dict) -> str:
    lines = []
    for key in (
        "resource_name",
        "location",
        "subscription_id",
        "resource_group",
        "deployment_name",
        "image",
        "replicas",
        "container_port",
        "configmap_name",
        "secret_name",
        "service_name",
        "node_count",
        "vm_size",
        "kubernetes_version",
        "dns_prefix",
        "must_use_configmap",
        "must_use_secret",
        "allow_resource_creation",
    ):
        val = constraints.get(key)
        if val is not None and val != "":
            lines.append(f"- {key}: {val}")
    return "\n".join(lines) if lines else "- none"


_RST_MARKUP_IN_DOCS = re.compile(
    r"\bI\s*\([^)]+\)|\bM\s*\([^)]+\)|\bC\s*\([^)]+\)",
)


def _check_rst_markup_in_yaml(yaml_content: str) -> list[str]:
    """Flag RST/doc markup (I/M/C(...)) copied from documentation anywhere in YAML."""
    if _RST_MARKUP_IN_DOCS.search(yaml_content or ""):
        return [
            "Task name or value contains RST markup (I(...), M(...), C(...)) copied from a "
            "documentation example. Rewrite task names and values to describe this request "
            "without doc-source artifacts."
        ]
    return []


def _check_literal_secrets_in_yaml(yaml_content: str) -> list[str]:
    """Reject obvious hardcoded secrets (often copied from module examples)."""
    issues: list[str] = []
    secret_key_re = re.compile(
        r"(?i)\b(admin_password|password|client_secret|api_key|secret_key|access_token|"
        r"shared_secret|connection_password)\s*:",
    )
    for line in (yaml_content or "").splitlines():
        ls = line.strip()
        if not ls or ls.startswith("#"):
            continue
        if not secret_key_re.search(ls):
            continue
        if "{{" in ls or "lookup(" in ls or "vault" in ls.lower():
            continue
        if re.search(
            r":\s*(?:[\"']?\{\{\s*var_[a-z0-9_]+\s*\}\}[\"']?|var_[a-z0-9_]+)\s*$",
            ls,
            re.IGNORECASE,
        ):
            continue
        parts = ls.split(":", 1)
        if len(parts) < 2:
            continue
        val = parts[1].strip().strip("'\"")
        if len(val) > 5 and not val.startswith("{{"):
            issues.append(
                "Possible hardcoded password or secret in YAML; remove it and use "
                "\"{{ var_<param> }}\", Ansible Vault, or lookup('env', ...)."
            )
            break
    return issues


def _aks_pool_counts_from_playbook(data: object) -> list[int]:
    counts: list[int] = []
    if not isinstance(data, list):
        return counts
    for play in data:
        if not isinstance(play, dict):
            continue
        for task in play.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            for mod_key, params in task.items():
                if mod_key in ("name", "block", "rescue", "always") or not isinstance(params, dict):
                    continue
                short = mod_key.split(".")[-1] if "." in mod_key else mod_key
                if short != "azure_rm_aks":
                    continue
                pools = params.get("agent_pool_profiles") or params.get("agent_pools")
                if not isinstance(pools, list):
                    continue
                for pool in pools:
                    if not isinstance(pool, dict):
                        continue
                    c = pool.get("count")
                    if c is None:
                        continue
                    try:
                        counts.append(int(c))
                    except (TypeError, ValueError):
                        pass
    return counts


def _check_aks_constraints(raw: str, constraints: dict) -> list[str]:
    issues: list[str] = []
    if not any(constraints.get(k) for k in ("node_count", "vm_size", "kubernetes_version", "dns_prefix")):
        return issues
    if "azure_rm_aks" not in (raw or ""):
        return issues
    try:
        stripped = "\n".join(
            ln for ln in (raw or "").splitlines() if not ln.strip().startswith("#")
        )
        data = yaml.safe_load(stripped)
    except Exception:
        return issues

    if constraints.get("vm_size") and constraints["vm_size"] not in raw:
        issues.append(
            f"Requested vm_size `{constraints['vm_size']}` does not appear in the generated YAML"
        )
    if constraints.get("kubernetes_version") and constraints["kubernetes_version"] not in raw:
        issues.append(
            f"Requested kubernetes_version `{constraints['kubernetes_version']}` "
            "does not appear in the generated YAML"
        )
    if constraints.get("dns_prefix") and constraints["dns_prefix"] not in raw:
        issues.append(
            f"Requested dns_prefix `{constraints['dns_prefix']}` does not appear in the generated YAML"
        )

    nc = constraints.get("node_count")
    if nc:
        try:
            want = int(str(nc).strip())
        except ValueError:
            want = 0
        if want > 0:
            pool_counts = _aks_pool_counts_from_playbook(data)
            if pool_counts:
                total = sum(pool_counts)
                if total != want:
                    issues.append(
                        f"Agent pool node count sum is {total}, but the request asked for {want} nodes"
                    )
                elif len(pool_counts) > 1 and all(c == 1 for c in pool_counts) and want == sum(pool_counts):
                    issues.append(
                        "Multiple single-node pools were used; prefer one agent_pool_profiles "
                        f"entry with count: {want} unless the user asked for multiple pools."
                    )
            elif not re.search(rf"\bcount\s*:\s*{want}\b", raw or ""):
                issues.append(
                    f"Expected node pool count {want} from the request (e.g. count: {want})"
                )

    return issues


def _fqcn_match_task_module(task_key: str, known_fqcns: list[str]) -> str | None:
    """Map a task's module key (short or FQCN) to one entry in known_fqcns."""
    if task_key in known_fqcns:
        return task_key
    short = task_key.split(".")[-1]
    matches = [m for m in known_fqcns if m.split(".")[-1] == short]
    if len(matches) == 1:
        return matches[0]
    return None


def _missing_required_params_in_tasks(
    yaml_content: str,
    required_params_by_module: Mapping[str, list[str]],
) -> list[str]:
    """Verify each task block includes required params for its module (multi-module playbooks)."""
    issues: list[str] = []
    if not required_params_by_module:
        return issues
    try:
        stripped = "\n".join(
            ln for ln in (yaml_content or "").splitlines() if not ln.strip().startswith("#")
        )
        data = yaml.safe_load(stripped)
    except Exception:
        return issues
    if not isinstance(data, list):
        return issues
    fqcn_list = list(required_params_by_module.keys())
    skip_keys = {
        "name",
        "delegate_to",
        "vars",
        "when",
        "async",
        "poll",
        "register",
        "ignore_errors",
        "failed_when",
        "changed_when",
        "tags",
        "loop",
        "until",
        "retries",
        "delay",
        "notify",
        "listen",
    }
    for play in data:
        if not isinstance(play, dict):
            continue
        for task in play.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            if "block" in task:
                continue
            for key, val in task.items():
                if key in skip_keys or not isinstance(val, dict):
                    continue
                fqcn = _fqcn_match_task_module(key, fqcn_list)
                if not fqcn:
                    continue
                for p in required_params_by_module.get(fqcn, []):
                    if p not in val:
                        issues.append(
                            f"Task using module `{key}` is missing required param `{p}` "
                            f"(module {fqcn})"
                        )
    return issues


def _check_yaml_comments_as_placeholders(yaml_content: str) -> list[str]:
    """
    Detect lines where the LLM left a comment instead of a var_ placeholder.
    Pattern: a line that is entirely a YAML comment referencing a param name.
    """
    issues: list[str] = []
    comment_param_pattern = re.compile(
        r"^\s*#\s*(additional parameters|add\s+\w+\s+here|specify\s+\w+|"
        r"provide\s+\w+|required\s+param|missing\s+\w+)",
        re.IGNORECASE,
    )
    for line in (yaml_content or "").splitlines():
        if comment_param_pattern.match(line):
            issues.append(
                f"Found comment placeholder instead of var_ value: '{line.strip()}'. "
                'Replace with a Jinja placeholder (e.g. vm_size: "{{ var_vm_size }}").'
            )
    return issues


def _collect_generation_issues(
    yaml_content: str,
    constraints: dict,
    *,
    required_params: list[str] | None = None,
    required_params_by_module: Mapping[str, list[str]] | None = None,
) -> list[str]:
    """Lightweight guardrails to catch common false playbooks."""
    issues: list[str] = []
    raw = yaml_content or ""

    if "kubernetes.core.k8s_resource" in raw or re.search(r"\bk8s_resource\s*:", raw):
        issues.append("Invalid module detected: kubernetes.core.k8s_resource")

    placeholder_patterns = [
        r"configuration values here",
        r"placeholder",
        r"\btodo\b",
        r"\bchangeme\b",
        r"<[^>]+>",
    ]
    for pat in placeholder_patterns:
        if re.search(pat, raw, flags=re.IGNORECASE):
            issues.append("Placeholder content detected in YAML output")
            break

    if constraints.get("deployment_name") and constraints["deployment_name"] not in raw:
        issues.append("Exact deployment name is missing")
    if constraints.get("resource_name") and constraints["resource_name"] not in raw:
        issues.append(
            f"Exact resource name `{constraints['resource_name']}` from the "
            "request is missing in the YAML"
        )
    if constraints.get("location") and constraints["location"] not in raw:
        issues.append(
            f"Exact location `{constraints['location']}` from the request is "
            "missing in the YAML"
        )
    if constraints.get("subscription_id") and constraints["subscription_id"] not in raw:
        issues.append("Exact subscription_id from the request is missing in the YAML")
    if constraints.get("resource_group") and constraints["resource_group"] not in raw:
        issues.append(
            f"Exact resource_group `{constraints['resource_group']}` from the "
            "request is missing in the YAML"
        )
    if constraints.get("image") and constraints["image"] not in raw:
        issues.append("Exact image is missing")
    if constraints.get("replicas") and not re.search(rf"replicas\s*:\s*{re.escape(str(constraints['replicas']))}\b", raw):
        issues.append("Replicas value does not match request")
    if constraints.get("container_port") and not re.search(rf"containerPort\s*:\s*{re.escape(str(constraints['container_port']))}\b", raw):
        issues.append("Container port does not match request")

    if constraints.get("must_use_configmap"):
        if "configMapRef" not in raw:
            issues.append("Missing configMapRef under container envFrom")
        if constraints.get("configmap_name") and constraints["configmap_name"] not in raw:
            issues.append("ConfigMap name does not match request")
    if constraints.get("must_use_secret"):
        if "secretRef" not in raw:
            issues.append("Missing secretRef under container envFrom")
        if constraints.get("secret_name") and constraints["secret_name"] not in raw:
            issues.append("Secret name does not match request")

    if constraints.get("service_name") and constraints["service_name"] not in raw:
        issues.append("Service connection target missing (expected DB_HOST value)")

    if not constraints.get("allow_resource_creation"):
        if re.search(r"kind:\s*ConfigMap", raw) or re.search(r"kind:\s*Secret", raw):
            issues.append("Unnecessary resource creation task detected for ConfigMap/Secret")

    if re.search(r"-\s+name:\s*Scale deployment", raw, flags=re.IGNORECASE):
        issues.append("Unnecessary separate scale task detected")

    issues.extend(_check_yaml_comments_as_placeholders(raw))
    issues.extend(_check_rst_markup_in_yaml(raw))
    issues.extend(_check_literal_secrets_in_yaml(raw))
    issues.extend(_check_aks_constraints(raw, constraints))

    if re.search(r"\{\{\s*versions\s*\}\}", raw):
        issues.append(
            "Undefined Jinja variable `versions` in kubernetes_version; "
            'use a concrete version string or kubernetes_version: "{{ var_kubernetes_version }}"'
        )

    by_mod = dict(required_params_by_module or {})
    if by_mod:
        issues.extend(_missing_required_params_in_tasks(raw, by_mod))
    else:
        for p in (required_params or []):
            p = (p or "").strip()
            if not p:
                continue
            if not re.search(rf"(^|\s){re.escape(p)}\s*:", raw, flags=re.IGNORECASE):
                issues.append(f"Required param `{p}` is missing in generated YAML")

    issues.extend(_check_secret_no_log(raw))

    return issues


_SECRET_PARAM_KEY_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api_key|access_key|secret_key|"
    r"private_key|client_secret|shared_secret|credential)"
)
_NO_LOG_RETRY_DIRECTIVES = frozenset({
    "name", "register", "tags", "when", "become", "become_user", "no_log",
    "loop", "with_items", "notify", "vars", "changed_when", "failed_when",
    "ignore_errors", "delegate_to", "run_once", "environment", "until",
    "retries", "delay", "check_mode", "diff", "args", "loop_control", "block",
    "rescue", "always",
})


def _params_have_secret_key(obj) -> bool:
    if isinstance(obj, dict):
        return any(
            (isinstance(k, str) and _SECRET_PARAM_KEY_RE.search(k)) or _params_have_secret_key(v)
            for k, v in obj.items()
        )
    if isinstance(obj, list):
        return any(_params_have_secret_key(i) for i in obj)
    return False


def _check_secret_no_log(yaml_content: str) -> list[str]:
    """
    Production guardrail: a task that passes a secret/credential param MUST
    carry `no_log: true`. Triggers a regeneration so secrets aren't logged.
    """
    try:
        parsed = yaml.safe_load(yaml_content)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    offenders: list[str] = []
    for play in parsed:
        if not isinstance(play, dict):
            continue
        for task in play.get("tasks") or []:
            if not isinstance(task, dict) or "block" in task:
                continue
            mod_key = next((k for k in task if k not in _NO_LOG_RETRY_DIRECTIVES), None)
            if not mod_key:
                continue
            if _params_have_secret_key(task.get(mod_key)) and not task.get("no_log"):
                offenders.append(task.get("name") or mod_key)

    if offenders:
        return [
            "Secret/credential field present without `no_log: true` on task(s): "
            f"{offenders[:3]} — add `no_log: true` to those tasks"
        ]
    return []


# ─────────────────────────────────────────────
#  SAVE
# ─────────────────────────────────────────────

def save_playbook(
    yaml_content: str,
    user_input: str,
    retrieval_meta: dict,
    *,
    llm_model: str = "unknown",
) -> str:
    """Save the generated playbook with RAG + LLM metadata header."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_name = re.sub(r"[^a-z0-9]+", "_", user_input.lower())[:30].strip("_")
    filename   = f"playbook_{short_name}_{timestamp}.yml"
    filepath   = os.path.join(OUTPUT_DIR, filename)

    header = (
        f"# ============================================================\n"
        f"# AnsibleAI — ChromaDB retrieval + agent LLM playbook\n"
        f"# Date        : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Request     : {user_input}\n"
        f"# Module      : {retrieval_meta.get('primary_module', '—')}\n"
        f"# Collection  : {retrieval_meta.get('primary_collection', '—')}\n"
        f"# RAG score   : {retrieval_meta.get('primary_score', '—')}\n"
        f"# Chunks used : {len(retrieval_meta.get('docs', []))}\n"
        f"# Source URL  : {retrieval_meta.get('source_url', '—')}\n"
        f"# LLM model   : {llm_model}\n"
        f"# ============================================================\n\n"
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header + yaml_content)

    return filepath
