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
import json
import re
import requests
from datetime import datetime
from dotenv import load_dotenv

# Always run from project root
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

KNOWLEDGE_BASE = "data/knowledge_base.json"
OUTPUT_DIR     = "output"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:14b")

# Max parameters to include in prompt (avoid token overflow)
MAX_PARAMS_IN_PROMPT = 12


# ─────────────────────────────────────────────
#  1. LOAD KNOWLEDGE BASE
# ─────────────────────────────────────────────

def load_knowledge_base():
    if not os.path.exists(KNOWLEDGE_BASE):
        raise FileNotFoundError(
            f"'{KNOWLEDGE_BASE}' not found.\n"
            "→ Run phase3_structurer.py first."
        )
    with open(KNOWLEDGE_BASE, "r", encoding="utf-8") as f:
        return json.load(f)


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
    for kw in module_entry.get("task_keywords", []):
        if kw.lower() in text:
            score += 1
    # Bonus: module name or short name in input
    slug = module_entry["slug"].replace("_module", "").replace("_", " ")
    if slug in text:
        score += 2
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
        key=lambda s: (scores[s], -len(modules[s].get("task_keywords", [])))
    )
    best_score = scores[best_slug]

    # Show top 3 matches for transparency
    top3 = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
    print("\n  [Intent Matcher] Top 3 module matches:")
    for slug, sc in top3:
        bar = "█" * sc if sc > 0 else "·"
        print(f"    {slug:<35} score={sc}  {bar}")

    return best_slug, modules[best_slug], best_score


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

SYSTEM_PROMPT = """You are an expert Ansible engineer specializing in Kubernetes automation.
Generate a valid Ansible playbook in YAML format. Follow every rule exactly.

=== MANDATORY PLAYBOOK STRUCTURE ===
A playbook is a YAML list. hosts/connection/gather_facts go INSIDE the play dict (after the dash).
ALWAYS follow this skeleton exactly:

---
- name: <descriptive play name>
  hosts: localhost
  connection: local
  gather_facts: no
  collections:
    - kubernetes.core
  tasks:
    - name: <task name>
      kubernetes.core.k8s:
        api_version: <version>
        kind: <Kind>
        metadata:
          name: <name>
          namespace: <namespace-if-provided>
        spec:
          ...
        state: present

=== CONTAINER envFrom RULE ===
envFrom and env ALWAYS go INSIDE the container definition, not at pod spec level.
CORRECT:
  containers:
    - name: myapp
      image: myimage:tag
      ports:
        - containerPort: 8080
      envFrom:
        - configMapRef:
            name: <exact-configmap-name-from-prompt>
        - secretRef:
            name: <exact-secret-name-from-prompt>
      env:
        - name: DB_HOST
          value: <service-name>
WRONG (never do this):
  containers:
    - name: myapp
  envFrom:   ← WRONG, this is outside the container

=== RESOURCE NAMING RULE ===
Use the EXACT resource names given in the prompt. Do not invent or modify names.
If the prompt says ConfigMap: analytics-config → use name: analytics-config exactly.
If the prompt says Secret: analytics-db-secret → use name: analytics-db-secret exactly.

=== SERVICE PORT RULE ===
If the prompt specifies port AND targetPort separately, use both exactly as given.
Example: port: 80, targetPort: 8080 → write port: 80 and targetPort: 8080.

=== NAMESPACE RULE ===
If a namespace is mentioned in the prompt, add it to EVERY resource metadata block.

=== DATABASE CONNECTION RULE ===
If the prompt says "connect to <service>", add this inside the container:
  env:
    - name: DB_HOST
      value: <service-name>

=== WHAT NOT TO ADD ===
- Do NOT add imagePullSecrets unless the prompt explicitly mentions them.
- Do NOT create ConfigMaps or Secrets that already exist (just reference them by name).
- Do NOT use clusterIP: None with type: LoadBalancer together.
- Do NOT invent Kubernetes fields (labelAffinity does not exist).
- serviceAccountName belongs only inside PodSpec, never inside Service spec.

=== OUTPUT ===
Output ONLY valid YAML starting with ---. No markdown, no explanations."""


def build_prompt(user_input: str, module_context: str) -> str:
    return f"""{SYSTEM_PROMPT}

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
        print("\n  [WARNING] No keyword match found.")
        print("  → Defaulting to kubernetes.core.k8s (general module)")
        best_slug  = "k8s_module"
        best_entry = modules["k8s_module"]

    # 3. Build context
    print("\n  [3/6] Building module context...")
    context = build_module_context(best_entry)

    # 4. Build prompt
    print("  [4/6] Building prompt...")
    prompt = build_prompt(user_input, context)
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