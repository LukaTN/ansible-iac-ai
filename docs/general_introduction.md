# General Introduction

**Project:** AnsibleAI — AI-powered generation of Ansible playbooks grounded on official module documentation  
**Type:** End-of-studies project (PFE)  
**Domain:** DevOps, Infrastructure as Code, retrieval-augmented generation, LLM agents

---

## 1. Context

### 1.1 Infrastructure has become software

Organizations no longer operate a handful of servers that a sysadmin can configure by hand. Production estates now span public clouds (AWS, Azure), container platforms (Kubernetes), Linux and Windows hosts, and a growing set of managed services. Changes must be repeatable, auditable, and reversible. That requirement is what **Infrastructure as Code (IaC)** answers: the desired state of the infrastructure is declared in version-controlled files, then applied by an automation engine rather than by ad-hoc shell commands.

Among IaC tools, **Ansible** occupies a particular place. It is agentless (SSH / APIs), declarative, and expressed in YAML **playbooks** that compose **modules**. Each module is a small, documented unit of work — create an EC2 instance, scale a Kubernetes Deployment, install a package, schedule a cron job. The Ansible ecosystem today comprises well over a thousand modules, spread across official and community **collections** (`ansible.builtin`, `amazon.aws`, `azure.azcollection`, `kubernetes.core`, `community.general`, and many others). That breadth is the source of Ansible’s power, and of its cost.

### 1.2 Writing playbooks remains a specialist skill

To produce a correct playbook, an engineer must:

1. **Identify the right module** among hundreds of similarly named candidates (`ec2_instance` vs `ec2_instance_info`, `k8s` vs `helm`, `azure_rm_virtualmachine` vs `azure_rm_virtualmachine_info`).
2. **Supply required parameters** with the exact names, types, and nesting that the module expects — including Fully Qualified Collection Names (FQCN) that linters now require.
3. **Respect YAML, Jinja2, and Ansible idioms** (idempotence, `become`, loops, `register`, handlers).
4. **Keep up with documentation drift**: modules gain and lose parameters between collection versions.

The result is a task that is slow, error-prone, and poorly suited to engineers who know *what* they want (“deploy nginx in the production namespace with three replicas”) but not *which* module and *which* keys implement it. Documentation exists — Ansible’s official module pages are detailed — but it is fragmented, verbose, and designed for human reading, not for a one-shot lookup from a natural-language request.

### 1.3 Generative AI as a promising, incomplete answer

Large language models (LLMs) can already emit plausible YAML from a sentence. That capability has made “AI for IaC” an attractive idea: describe the intent, obtain a playbook. In practice, a generic chatbot is a weak automation engineer:

- It **hallucinates** module names, parameters, and examples that never existed.
- Its training cut-off is **stale** relative to the live collection APIs.
- It has **no obligation** to cite a source, so the user cannot tell a grounded answer from a fluent guess.
- It stops at a draft. It does not run **ansible-lint**, does not reject placeholders (`CHANGEME`, `var_*`), and does not repair the file when the linter fails.

The research and engineering question is therefore not “can an LLM write YAML?” — it can. The question is: **how do we constrain generation so that the artefact is grounded on official documentation and mechanically checkable before a human ever copies it into Git?**

---

## 2. Problem statement

The problem this project addresses can be stated as follows:

> **How can we turn a natural-language infrastructure request into an Ansible playbook that (i) uses real modules from a maintained knowledge base, (ii) is syntactically and idiomatically valid, and (iii) is produced through a conversational, observable, multi-user system rather than a one-shot prototype?**

This problem has four coupled difficulties.

**Grounding.** The generator must retrieve the *right* module documentation for the user’s intent, not a near neighbour. Semantic search alone confuses `*_info` modules with mutating ones; lexical search alone misses paraphrases (“object storage” vs `s3_bucket`). Retrieval quality is the ceiling of generation quality.

**Closing the loop.** A single LLM call is not a production workflow. A draft that fails YAML validation, misses required keys, or fails ansible-lint must be **repaired** with the exact diagnostics, not regenerated from scratch with a hope that the next sample is better.

**Ambiguity.** Users omit the cloud, the namespace, or the replica count. The system must know when to **ask** rather than invent.

**From prototype to platform.** An academic demo that runs in one Flask process, with no users, no persistence of conversations, and a vector store on local disk, cannot be evaluated as a usable assistant. Auth, async jobs, durable storage, and observability are not “ops extras”; they are what make the agent usable and inspectable.

---

## 3. Positioning and related approaches

Several families of tools sit near this problem. None of them, taken alone, solves it.

| Approach | What it does well | Where it falls short for Ansible IaC |
|----------|-------------------|--------------------------------------|
| **Generic chatbots** (ChatGPT, Copilot Chat) | Fluent YAML, fast iteration | No binding to a local module corpus; no lint gate; no FQCN discipline |
| **IDE copilots** | Inline completion from local files | Weak at choosing among collections; no RAG over official docs |
| **Template / Galaxy roles** | Known-good patterns | The user must already know which role to pick; no NL interface |
| **Naïve RAG** (embed docs → one generate call) | Better grounding than a bare LLM | One retrieval, one draft, no repair; retrieval errors become playbook errors |
| **Unvalidated agents** | Multi-step tool use | Tools without a **production gate** still ship broken YAML |

AnsibleAI is positioned at the intersection of **hybrid RAG**, a **single LangGraph agent** with an explicit repair loop, and a **production-oriented application stack**. The claim is not that the model “knows Ansible”. The claim is that **documentation retrieval + deterministic validation + bounded self-repair** can raise the rate of playbooks that are *statically* production-ready (valid YAML, real modules, ansible-lint passed, no placeholders). Applying those playbooks on a live cloud remains out of scope: the gate certifies the *artefact*, not a Molecule run.

---

## 4. Proposed solution — AnsibleAI

**AnsibleAI** is an AI-powered Infrastructure-as-Code assistant. A user describes a task in natural language (or pastes YAML to explain, compare, or fix). The system:

1. **Indexes** official Ansible module documentation (scraped, parsed, and stored as structured chunks) into PostgreSQL with **pgvector**, alongside a **BM25** lexical index.
2. **Retrieves** a hybrid ranking of modules, with collection routing and demotion of read-only (`*_info` / `*_facts`) modules when the query is a write intent.
3. **Reasons** with a LangGraph agent: decide intent (generate, edit, explain, compare, diagnose), search the knowledge base as many times as needed, or stop to ask a clarifying question.
4. **Drafts** YAML with a code-oriented LLM, then **gates** it (validator + ansible-lint + placeholder checks). On failure, the same agent produces a **fix plan** and loops until the gate passes or the iteration budget is exhausted.
5. **Delivers** the playbook in a conversational React UI, with sources, validation cards, thread history, and a live trace of agent steps.

Generation is **asynchronous**: the API accepts the message (`202`), a **Celery** worker runs the graph, and progress arrives over **WebSocket**. Playbooks are archived in **MinIO**. Users are authenticated; threads are private. Metrics and traces (Prometheus, Grafana, Langfuse) make the loop inspectable.

The knowledge base currently covers five collections aligned with the lint image: `ansible.builtin`, `community.general`, `amazon.aws`, `azure.azcollection`, and `kubernetes.core`.

---

## 5. Objectives

### 5.1 General objective

Design, implement, and evaluate an end-to-end system that generates Ansible playbooks from natural language, **grounded on official module documentation** and **accepted only when a static production gate passes**.

### 5.2 Specific objectives

1. **Knowledge pipeline** — scrape, parse, and structure Ansible module docs into a versionable knowledge base, with admin tooling to refresh or roll back the corpus.
2. **Hybrid retrieval** — combine dense embeddings and BM25, route by collection, and rank at *module* level so the agent sees the right API, not a lucky chunk.
3. **Agentic generation** — a single LangGraph state machine (reason → tools → draft → gate → repair), not a one-shot prompt and not a swarm of disconnected personas.
4. **Mechanical quality bar** — YAML/schema validation, FQCN and required-parameter checks, ansible-lint as a hard gate, cooperative cancel, bounded iterations.
5. **Usable product surface** — conversational UI, multi-turn threads, auth, CSRF, rate limits, async jobs, artefact storage.
6. **Measurability** — retrieval benchmarks (hit-rate, MRR), end-to-end golden cases across collections, unit tests on the gate and routing, and LLMOps traces/metrics.

---

## 6. Contributions

This project’s contributions are both **scientific/engineering** (how generation is constrained) and **systemic** (how that loop is shipped):

1. **A documentation-grounded Ansible assistant**, rather than a generic YAML writer: every generation path is expected to search the indexed corpus; invented parameters are treated as failures of grounding or of the gate.
2. **A production gate inside the agent loop.** ansible-lint is not a cosmetic badge after the fact. Failed lint and validator errors drive a chain-of-thought **repair plan** until the artefact is clean or the budget is spent.
3. **Hybrid RAG tuned on a task-phrased benchmark** (not only on module names): module-level score aggregation, write-intent demotion of info modules, and overview chunks that include example task titles — with ablations that rejected changes which hurt top-1.
4. **A single-agent LangGraph design** with shared state (retrieved chunks, draft, lint output), chosen over multi-agent handoffs that drop context and multiply LLM calls.
5. **A path from prototype to operable stack:** Flask + React, PostgreSQL/pgvector, Redis, Celery, MinIO, Docker Compose, Alembic, auth, and Phase-6a observability (Prometheus, Grafana, Langfuse).
6. **An evaluation harness** — retrieval eval without an LLM, and a multi-layer E2E dataset (intent, retrieval, module choice, playbook structure, syntax) so quality is not judged by a single happy demo.

---

## 7. Scope and non-goals

**In scope**

- Natural-language generation, explanation, comparison, and repair of Ansible playbooks for the indexed collections.
- Static quality: YAML validity, module existence in the KB, required parameters, placeholders, ansible-lint `passed`.
- Multi-user web application with conversation memory, docs administration, and local/Compose deployment.

**Out of scope (deliberate)**

- **Live apply** on AWS, Azure, or a Kubernetes cluster; Molecule integration tests; “this playbook ran in production”.
- Generating Terraform, Pulumi, Helm charts, or GitHub Actions as first-class targets.
- Training or fine-tuning a foundation model; the project *uses* local (Ollama) or routed (OpenRouter) LLMs.
- Full Kubernetes production hardening (SSO, Vault, ArgoCD, GPU vLLM) — these are identified on the LLMOps roadmap (Phases 4–8) but are not required to demonstrate the core claim.

Honesty about that boundary matters: a lint-clean playbook is a **necessary** condition for production use, not a **sufficient** one.

---

## 8. Target users

| Persona | Need |
|---------|------|
| **DevOps / SRE** | Faster first draft of a playbook, with citations and a lint verdict, then human review in Git |
| **Platform engineer** | A corpus they can scrape and re-index when collections move, without rewriting prompts by hand |
| **Student / new Ansible user** | An explanation of *why* a module was chosen, not only a blob of YAML |
| **Operator of the assistant** | Auth, metrics, traces, and a cancel path when a generation hangs |

The primary user remains a **human in the loop**. AnsibleAI proposes; the engineer commits.

---

## 9. Methodology (how the project was conducted)

The work followed an incremental, evaluation-driven path rather than a single waterfall:

1. **Knowledge engineering** — documentation pipeline and structured KB.
2. **Retrieval** — embeddings, hybrid search, then measured ranking fixes.
3. **Generation** — LLM drafts conditioned on retrieved chunks.
4. **Agent loop** — unification of chat paths into LangGraph with a repair gate.
5. **Productization** — auth, containers, async workers, Postgres+pgvector, artefact store.
6. **Observability** — traces of each generation and RED/domain metrics.

Each step kept a **fail-closed** bias: missing docs, failed lint, or an ambiguous request should surface as a question or a gate failure, not as silent invention.

---

## 10. Organization of the report

The remainder of the report develops this introduction as follows:

| Chapter | Content |
|---------|---------|
| **State of the art** | IaC and Ansible; LLMs for code; RAG; agent graphs; lint-as-oracle |
| **Requirements and analysis** | Functional / non-functional needs, use cases, constraints |
| **Architecture** | Frontend, API, worker, RAG, agent graph, data stores |
| **Implementation** | Knowledge pipeline, retriever, LangGraph nodes, gate, UI, security |
| **Evaluation** | Retrieval benchmark, E2E golden set, limitations |
| **Conclusion and perspectives** | Results, roadmap (GPU serving, SSO, CI eval gate), future work |

---

## 11. Summary

Infrastructure as Code made operations programmable; it did not make them easy. Ansible’s module surface is too large for memory and too precise for unconstrained language models. **AnsibleAI** treats playbook generation as a **grounded, gated, conversational process**: retrieve official documentation, draft under that context, prove the file against a validator and ansible-lint, and repair until the bar is met. The surrounding platform — authentication, asynchronous execution, hybrid search in PostgreSQL, and observability — exists so that this loop can be used, measured, and trusted as an assistant, not merely demonstrated as a prompt.
