"""
=============================================================
  AI-Powered IaC — Phase 4 : Generator
  Input  : data/knowledge_base.json + user natural language input
  Output : output/playbook_<timestamp>.yml
=============================================================
  Pipeline:
    1. Load knowledge_base.json
    2. Intent Matcher  → find best module via task_keywords
    3. Context Builder → extract relevant params + examples
    4. Prompt Builder  → build structured prompt for Ollama
    5. Ollama Call     → call local LLM API
    6. YAML Extractor  → extract clean playbook from response
    7. Save output     → output/playbook_*.yml
=============================================================
"""

import os
import re
import sys
from datetime import datetime

import requests
from dotenv import load_dotenv
from kb_store import load_knowledge_base as load_kb_store

# backend/pipeline/<file>.py → repository root
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

KNOWLEDGE_BASE = "data/knowledge_base.json"
OUTPUT_DIR     = "output"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL")

# Max parameters to include in prompt (avoid token overflow)
MAX_PARAMS_IN_PROMPT = 12
TOKEN_STOPWORDS = {
    "a", "an", "the", "to", "for", "of", "in", "on", "with", "and", "or",
    "is", "are", "be", "as", "by", "from", "that", "this", "it", "at", "into",
    "named", "name", "using", "use", "create", "get", "set", "add", "remove",
}


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-z0-9_]+", (text or "").lower())
    return {t for t in raw if len(t) > 2 and t not in TOKEN_STOPWORDS}


def _infer_collection_hint(user_input: str) -> str:
    text = (user_input or "").lower()
    if any(k in text for k in ("kubernetes", "k8s", "pod", "namespace", "deployment", "service", "helm")):
        return "kubernetes.core"
    if any(k in text for k in ("aws", "iam", "s3", "ec2", "vpc", "lambda", "cloudwatch")):
        return "amazon.aws"
    if any(k in text for k in ("azure", "resource group", "vm", "virtual machine")):
        return "azure.azcollection"
    if any(k in text for k in ("linux", "file", "copy", "user", "package", "shell", "command")):
        return "ansible.builtin"
    return ""


# ─────────────────────────────────────────────
#  1. LOAD KNOWLEDGE BASE
# ─────────────────────────────────────────────

def load_knowledge_base():
    kb = load_kb_store(prefer_parsed=True)
    if not kb.get("modules"):
        raise FileNotFoundError(
            "No module data found.\n"
            "→ Run phase2_parser.py then phase3_structurer.py first."
        )
    return kb


# ─────────────────────────────────────────────
#  2. INTENT MATCHER
# ─────────────────────────────────────────────

def score_module(user_input: str, module_entry: dict) -> int:
    """
    Score a module based on how many of its task_keywords
    appear in the user input. Returns match count.
    """
    text = user_input.lower()
    score = 0
    module_name = module_entry.get("module", "")
    short_name = module_name.split(".")[-1] if module_name else ""
    desc = module_entry.get("description", "")
    required_params = module_entry.get("required_params", []) or []
    user_tokens = _tokens(user_input)
    module_tokens = _tokens(short_name.replace(".", " ").replace("_", " "))
    desc_tokens = _tokens(desc)
    for kw in module_entry.get("task_keywords", []):
        if kw.lower() in text:
            score += 1
    # Bonus: lexical overlap with module name/description/required params
    score += 3 * len(user_tokens & module_tokens)
    score += min(4, len(user_tokens & desc_tokens))
    for p in required_params:
        if p.lower() in text:
            score += 2

    if short_name and short_name in text:
        score += 4

    # Collection-level bias (AWS/Azure/K8s/Builtin)
    hint = _infer_collection_hint(user_input)
    coll = module_entry.get("collection", "")
    if hint and coll == hint:
        score += 5
    if hint == "kubernetes.core" and short_name.startswith("helm"):
        score += 3

    # Boost entity alignment (e.g. "user", "bucket", "instance", ...)
    for ent in ("user", "group", "bucket", "instance", "vm", "pod", "deployment", "service", "secret", "configmap"):
        if re.search(rf"\b{ent}\b", text) and ent in module_tokens:
            score += 4
    if "resource group" in text and "resourcegroup" in short_name:
        score += 8
    if "virtual machine" in text and ("virtualmachine" in short_name or "vm" in module_tokens):
        score += 8

    is_create_intent = any(k in text for k in ("create", "deploy", "provision", "add", "launch", "configure"))
    is_read_intent = any(k in text for k in ("get", "list", "show", "describe", "read", "info"))
    if short_name.endswith("_info"):
        if is_create_intent:
            score -= 5
        if is_read_intent:
            score += 3

    return score


def find_best_module(user_input: str, modules: dict) -> tuple:
    """
    Return (best_slug, best_entry, score) for the user input.
    Tie-breaking: prefer the module with fewer total keywords
    (more specific modules have more targeted keyword sets).
    """
    scores = {
        slug: score_module(user_input, entry)
        for slug, entry in modules.items()
    }
    # Tie-break: among equal scores, prefer module with fewer keywords
    # (smaller keyword set = more specialized module)
    best_slug = max(
        scores,
        key=lambda s: (
            scores[s],
            0 if modules[s].get("module", "").endswith("_info") else 1,
            -len(modules[s].get("task_keywords", [])),
        )
    )
    best_score = scores[best_slug]

    # Show top 3 matches for transparency
    top3 = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
    print("\n  [Intent Matcher] Top 3 module matches:")
    for slug, sc in top3:
        bar = "#" * sc if sc > 0 else "."
        print(f"    {slug:<80} score={sc}  {bar}")

    return best_slug, modules[best_slug], best_score


def pick_fallback_module(user_input: str, modules: dict) -> tuple:
    """
    Fallback chooser when all scores are weak.
    Prefer a sane module from the inferred collection.
    """
    hint = _infer_collection_hint(user_input)
    by_module = {e.get("module", ""): (slug, e) for slug, e in modules.items()}
    preferred = {
        "kubernetes.core": ["kubernetes.core.k8s", "kubernetes.core.helm"],
        "amazon.aws": ["amazon.aws.iam_user", "amazon.aws.s3_bucket", "amazon.aws.ec2_instance"],
        "azure.azcollection": ["azure.azcollection.azure_rm_resourcegroup", "azure.azcollection.azure_rm_virtualmachine"],
        "ansible.builtin": ["ansible.builtin.user", "ansible.builtin.copy", "ansible.builtin.command"],
    }

    for mod in preferred.get(hint, []):
        if mod in by_module:
            slug, entry = by_module[mod]
            return slug, entry

    if hint:
        coll_candidates = [(s, e) for s, e in modules.items() if e.get("collection") == hint]
        if coll_candidates:
            return coll_candidates[0]

    for mod in ("kubernetes.core.k8s", "ansible.builtin.command"):
        if mod in by_module:
            slug, entry = by_module[mod]
            return slug, entry

    first_slug = next(iter(modules))
    return first_slug, modules[first_slug]


# ─────────────────────────────────────────────
#  3. CONTEXT BUILDER
# ─────────────────────────────────────────────

def build_module_context(entry: dict) -> str:
    """
    Build a compact, structured text summary of a module
    to inject into the LLM prompt.
    """
    lines = []

    lines.append(f"MODULE: {entry['module']}")
    lines.append(f"DESCRIPTION: {entry['description']}")
    lines.append("")

    # Required params first
    required = [p for p in entry["parameters"] if p["required"]]
    optional = [p for p in entry["parameters"] if not p["required"]]

    lines.append("REQUIRED PARAMETERS:")
    if required:
        for p in required:
            lines.append(f"  - {p['name']} ({p['type']}): {p['description'][:120]}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"OPTIONAL PARAMETERS (showing top {MAX_PARAMS_IN_PROMPT}):")

    # Show most useful optional params (skip auth/proxy boilerplate)
    skip_keywords = {"api_key", "ca_cert", "client_cert", "client_key",
                     "proxy", "proxy_headers", "basic_auth", "proxy_basic_auth",
                     "user_agent", "no_proxy", "impersonate_groups",
                     "impersonate_user", "persist_config", "password",
                     "username", "validate_certs", "host"}

    useful_optional = [
        p for p in optional
        if p["name"] not in skip_keywords
    ][:MAX_PARAMS_IN_PROMPT]

    for p in useful_optional:
        default_str = f" (default: {p['default']})" if p["default"] else ""
        choices_str = f" choices: {p['choices']}" if p["choices"] else ""
        lines.append(
            f"  - {p['name']} ({p['type']}){default_str}{choices_str}: "
            f"{p['description'][:100]}"
        )

    # Example (first one, truncated)
    if entry.get("examples"):
        lines.append("")
        lines.append("EXAMPLE:")
        example = entry["examples"][0]
        # Take first task only to save tokens
        first_task = example.split("\n\n")[0]
        lines.append(first_task)

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  4. PROMPT BUILDER
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert Ansible engineer across Kubernetes, cloud, and system automation.
Generate a valid Ansible playbook in YAML format from the selected module documentation.

=== MANDATORY PLAYBOOK STRUCTURE ===
A playbook must be a YAML list:
---
- name: <descriptive play name>
  hosts: localhost
  connection: local
  gather_facts: no
  collections:
    - <collection when module is not ansible.builtin>
  tasks:
    - name: <task name>
      <selected.module.name>:
        <module parameters>

=== STRICT RULES ===
- Use the selected module exactly as provided in MODULE DOCUMENTATION.
- Include all required parameters from docs.
- Use exact user-provided resource names/regions/IDs.
- Do not invent unsupported parameters or fields.
- Keep output concise and executable.
- Output ONLY valid YAML starting with ---.
- No markdown fences, no extra explanations."""


def build_prompt(user_input: str, module_context: str, module_entry: dict) -> str:
    collection = module_entry.get("collection", "")
    module_name = module_entry.get("module", "")
    return f"""{SYSTEM_PROMPT}

SELECTED MODULE: {module_name}
SELECTED COLLECTION: {collection}

===== MODULE DOCUMENTATION =====
{module_context}
===== END DOCUMENTATION =====

USER REQUEST: {user_input}

Generate ONLY the Ansible playbook YAML. Start with ---. No explanations.
"""


# ─────────────────────────────────────────────
#  5. OLLAMA CALL
# ─────────────────────────────────────────────

def call_ollama(prompt: str, model: str = OLLAMA_MODEL) -> str:
    """
    Call Ollama API and return the generated text.
    """
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model"  : model,
        "prompt" : prompt,
        "stream" : False,
        "options": {
            "temperature": 0.2,   # Low temp = more deterministic YAML
            "top_p"      : 0.9,
            "num_predict": 1024,
        }
    }

    print(f"\n  [Ollama] Calling {model} at {OLLAMA_BASE_URL}...")
    print("  [Ollama] Generating playbook (this may take 10-30 seconds)...")

    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")

    except requests.exceptions.ConnectionError:
        print("\n  [ERROR] Cannot connect to Ollama.")
        print("  → Make sure Ollama is running: open a terminal and run 'ollama serve'")
        print(f"  → Expected URL: {OLLAMA_BASE_URL}")
        raise

    except requests.exceptions.Timeout:
        print("\n  [ERROR] Ollama request timed out after 120 seconds.")
        print("  → Try a smaller model: set OLLAMA_MODEL=llama3.2 in .env")
        raise


# ─────────────────────────────────────────────
#  6. YAML EXTRACTOR
# ─────────────────────────────────────────────

def extract_yaml(raw_response: str) -> str:
    """
    Extract clean YAML playbook from Ollama's response.
    Handles cases where the model wraps it in markdown code blocks
    or adds text/lines before the --- marker.
    """
    # Step 1: Strip markdown code fences if present
    # Handles ```yaml ... ``` and ``` ... ```
    fence_match = re.search(r"```(?:yaml|yml)?\s*(.*?)```", raw_response, re.DOTALL)
    if fence_match:
        raw_response = fence_match.group(1).strip()

    # Step 2: Find the FIRST occurrence of --- and take everything from there
    # This strips any text/lines Ollama added before the playbook
    lines = raw_response.splitlines()
    start_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "---":
            start_idx = i
            break

    if start_idx is not None:
        return "\n".join(lines[start_idx:]).strip()

    # Step 3: If no --- found but looks like a playbook (starts with -)
    # find first line starting with - name:
    for i, line in enumerate(lines):
        if re.match(r"^-\s+name:", line):
            return "\n".join(lines[i:]).strip()

    # Fallback: return as-is
    return raw_response.strip()


# ─────────────────────────────────────────────
#  7. SAVE OUTPUT
# ─────────────────────────────────────────────

def save_playbook(yaml_content: str, user_input: str, module_slug: str) -> str:
    """
    Save the generated playbook to output/ directory.
    Returns the output file path.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Build a short filename from user input
    short_name = re.sub(r"[^a-z0-9]+", "_", user_input.lower())[:30].strip("_")
    filename = f"playbook_{short_name}_{timestamp}.yml"
    filepath = os.path.join(OUTPUT_DIR, filename)

    header = f"""# ============================================================
# Generated by AI-Powered IaC Generator
# Date    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Request : {user_input}
# Module  : {module_slug}
# Model   : {OLLAMA_MODEL}
# ============================================================

"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header + yaml_content)

    return filepath


# ─────────────────────────────────────────────
#  MAIN PIPELINE
# ─────────────────────────────────────────────

def generate_playbook(user_input: str) -> str:
    """
    Full pipeline: user input → Ansible playbook file.
    Returns path to generated file.
    """
    print("=" * 60)
    print("  AI-Powered IaC — Phase 4 Generator")
    print(f"  Model   : {OLLAMA_MODEL}")
    print(f"  Request : {user_input}")
    print("=" * 60)

    # 1. Load knowledge base
    print("\n  [1/6] Loading knowledge base...")
    kb = load_knowledge_base()
    modules = kb["modules"]
    print(f"        Loaded {len(modules)} modules.")

    # 2. Intent matching
    print("\n  [2/6] Running intent matcher...")
    best_slug, best_entry, score = find_best_module(user_input, modules)
    print(f"\n        → Selected: {best_entry['module']} (score={score})")

    if score == 0:
        print("\n  [WARNING] No strong keyword match found.")
        best_slug, best_entry = pick_fallback_module(user_input, modules)
        print(f"  → Fallback module: {best_entry.get('module')} ({best_entry.get('collection')})")

    # 3. Build context
    print("\n  [3/6] Building module context...")
    context = build_module_context(best_entry)

    # 4. Build prompt
    print("  [4/6] Building prompt...")
    prompt = build_prompt(user_input, context, best_entry)
    print(f"        Prompt length: {len(prompt)} chars")

    # 5. Call Ollama
    print("\n  [5/6] Calling Ollama...")
    raw_response = call_ollama(prompt)
    print(f"        Response length: {len(raw_response)} chars")

    # 6. Extract YAML
    print("\n  [6/6] Extracting YAML...")
    yaml_content = extract_yaml(raw_response)

    # 7. Save
    output_path = save_playbook(yaml_content, user_input, best_slug)
    print(f"\n  ✅ Playbook saved → {output_path}")
    print("=" * 60)

    # Print preview
    print("\n  PLAYBOOK PREVIEW:")
    print("  " + "-" * 50)
    for line in yaml_content.split("\n")[:30]:
        print(f"  {line}")
    if yaml_content.count("\n") > 30:
        print("  ... (truncated, see full file)")
    print("  " + "-" * 50)

    return output_path


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  Ansible AI Playbook Generator")
    print("  ──────────────────────────────")
    print("  Examples:")
    print("    - execute a command in a kubernetes pod")
    print("    - deploy nginx using helm in the default namespace")
    print("    - scale a deployment to 3 replicas")
    print("    - copy a file to a pod")
    print("    - get logs from a pod")
    print()

    user_request = input("  Enter your request: ").strip()

    if not user_request:
        print("  [ERROR] Empty request. Please enter a description.")
    else:
        generate_playbook(user_request)
