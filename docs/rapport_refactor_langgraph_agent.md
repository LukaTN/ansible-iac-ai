# Rapport — Refonte de l’agent AnsibleAI (LangGraph + CoT + boucle de réparation)

**Projet :** AnsibleAI — génération de playbooks Ansible assistée par IA  
**Date :** 13 juillet 2026  
**Objet :** Remplacer l’orchestrateur multi-phases / double chemin (`CHAT_MODE=fast|agent`) par un **agent unique LangGraph** qui raisonne en chaîne de pensée (CoT) et **itère jusqu’à un gate de production** (0 erreurs validator + ansible-lint passé).

---

## 1. Contexte et problème

### 1.1 Situation avant

L’application exposait **deux chemins** pour le chat :

| Mode | Comportement |
|------|----------------|
| `CHAT_MODE=fast` (défaut) | RAG direct → une génération YAML → validation une fois |
| `CHAT_MODE=agent` | Plan LLM → outils → (clarify désactivé) → génération → synthèse |

Les deux chemins partageaient le même générateur YAML, mais :

1. **ansible-lint n’entrait pas dans la boucle de retry.**  
   Le générateur ne réessayait que sur des heuristiques légères (`_collect_generation_issues`). Le lint tournait *après* coup et servait surtout d’affichage UI.
2. **Des playbooks « valides » pouvaient être non prêts pour la prod.**  
   Placeholders `var_*`, lint skippé (Windows sans WSL/Docker), contradictions de prompts (Azure sans FQCN alors que lint exige le FQCN).
3. **Logique duale difficile à maintenir.**  
   Fast path dans `app.py`, phases mortes (`if False` clarify), prompts contradictoires (demander des clarifications vs « never ask »).

### 1.2 Objectif du changement

- Un **seul chemin** : tout chat passe par l’agent.
- Un **agent unique** (pas multi-agents séparés) avec nœuds de contrôle LangGraph.
- **Chain-of-Thought** explicite pour décider et pour planifier les corrections lint.
- Boucle **draft → gate → repair** jusqu’à critères de « production-ready » *statiques* (pas d’exécution cloud réelle — hors scope).

### 1.3 Limite volontaire (honnêteté technique)

« Fonctionne sur tous les environnements » ne peut pas être prouvé sans Molecule / apply réel.  
Le gate certifie un **artefact** : YAML propre, FQCN, pas de placeholders suspects, **ansible-lint `passed`** (pas `skipped`).

---

## 2. Décision d’architecture : un agent vs multi-agents

| Critère | Agent unique (retenu) | Multi-agents (planner / generator / critic) |
|---------|------------------------|---------------------------------------------|
| Contexte | Un `AgentState` partagé (RAG + draft + lint) | Handoffs qui perdent souvent les chunks / le draft |
| Réparation lint | Même modèle voit le YAML + les lignes lint exactes | Ping-pong critic→generator, fixes partiels |
| CoT | Scratchpad continu | Raisonnement fragmenté |
| Coût | 1 flux LLM + outils | 2–3× d’appels par tour |
| Adéquation | Un seul artefact YAML | Sur-ingénierie pour cette tâche |

**Décision :** LangGraph avec **nœuds de contrôle** (`reason`, `tools`, `draft`, `gate`, `ask_user`, `respond`) — **pas** plusieurs personas LLM indépendantes.

---

## 3. Ce qui a changé (fichier par fichier)

### 3.1 Nouveaux modules

| Fichier | Rôle |
|---------|------|
| [`agent/state.py`](../agent/state.py) | `AgentState` typé, helpers conversation, **`evaluate_gate()`** (échecs *repairable* vs *environmental*) |
| [`agent/graph.py`](../agent/graph.py) | `StateGraph` LangGraph : reason ↔ tools ↔ draft ↔ gate ↔ respond |
| [`tests/test_agent_gate.py`](../tests/test_agent_gate.py) | 20 tests unitaires : gate, routage, boucle de réparation (mocks) |

### 3.2 Modules réécrits / refondus

| Fichier | Avant | Après | Pourquoi |
|---------|-------|-------|----------|
| [`agent/orchestrator.py`](../agent/orchestrator.py) | ~1160 lignes de phases PLAN/EXECUTE/CLARIFY/GENERATE/SYNTHESIZE | Wrapper mince : `build_initial_state` → `graph.invoke` → `AgentResponse` | Une seule entrée API ; la boucle vit dans le graphe |
| [`agent/tools.py`](../agent/tools.py) | `generate_playbook` one-shot + `run_tool` registry | `draft_playbook` (1 passe), `validate_playbook_file`, search/KB inchangés | Séparer génération et validation pour pouvoir boucler |
| [`agent/playbook_generator.py`](../agent/playbook_generator.py) | Retry interne sur heuristiques légères (`MAX_RETRIES`) | `draft_playbook_from_retrieval` = **une** passe ; le graphe possède la qualité | Le gate validator+lint pilote les retries |
| [`agent/prompts.py`](../agent/prompts.py) | PLANNING / CLARIFY / SYNTHESIS + playbook | `AGENT_SYSTEM`, `REASON_PROMPT`, `REPAIR_PROMPT`, `RESPOND_PROMPT` + playbook avec section **ANSIBLE-LINT COMPLIANCE** (FQCN Azure inclus) | Alignement prompts ↔ lint ; CoT structuré JSON |
| [`app.py`](../app.py) | Branche `CHAT_MODE` fast vs agent | Toujours `handle_message` | Supprimer le double chemin |
| [`README.md`](../README.md), [`.env.example`](../.env.example) | Documentait `CHAT_MODE` | Documente le graphe + `AGENT_MAX_ITERATIONS` + backends lint | Cohérence docs / runtime |
| [`requirements.txt`](../requirements.txt) | LangChain pour RAG seulement | + `langgraph>=1.0,<2` | Dépendance du graphe |
| [`tests/e2e/runner.py`](../tests/e2e/runner.py) | Mode `pipeline` = `generate_playbook_rag_v2` | Mode `pipeline` = `handle_message` | E2E sur le vrai chemin chat |

### 3.3 Conservé volontairement (bibliothèques)

- **RAG** (`rag/retriever.py`, ChromaDB, embeddings) — LangChain reste ici, pas pour l’orchestration agent.
- **Validator + ansible-lint** (`pipeline/validator.py`, `pipeline/ansible_lint_runner.py`) — inchangés comme moteurs de check ; le *gate* les appelle à chaque draft.
- **`agent/llm.py`** — OpenRouter / Ollama + fallbacks ; les nœuds appellent `chat()`.
- **Forme publique `AgentResponse`** — le frontend n’a pas besoin de changer.

### 3.4 Flux cible

```
START → reason ──→ tools ──→ reason
           │
           ├──→ ask_user → END
           │
           ├──→ draft → gate ──→ reason   (réparation CoT)
           │              │
           │              └──→ respond → END
           └──→ respond → END
```

**Gate « production-ready » si et seulement si :**

1. `validation.errors == []`
2. `ansible_lint.status == "passed"` (si backend disponible ; sinon échec *environmental*, pas de redraft inutile)
3. Pas d’avertissements placeholder (promus en échecs du gate)
4. Pas d’issues heuristiques de génération (modules inventés, RST, secrets littéraux, …)

Budget : `AGENT_MAX_ITERATIONS` (défaut **4**). Si épuisé → meilleur draft **marqué non prêt** + liste des issues.

---

## 4. Outils utilisés par l’agent — et pourquoi

Ces outils sont des wrappers Python appelés par les nœuds du graphe (pas importés directement par le LLM).

| Outil | Qu’il fait | Pourquoi on l’utilise |
|-------|------------|------------------------|
| **`search_docs`** | Recherche sémantique ChromaDB (multi-collections) ; vote de collection (pin / pivot / supermajority) | Ancrer le playbook dans la **doc officielle indexée** ; éviter d’inventer des modules |
| **`draft_playbook`** | Une passe LLM (génération ou réparation) + écriture fichier `output/` | Produire / corriger le YAML avec contexte RAG + `repair_feedback` + `fix_plan` CoT |
| **`validate_playbook_file` / `validate_yaml`** | Validator KB (syntaxe, structure, params, secrets, k8s definition, …) + **ansible-lint** | Critère objectif de qualité ; alimente le gate et la boucle repair |
| **`get_module_info`** | Référence structurée module (UI « Source ») | Enrichir la réponse sans re-scraper |
| **`ask_user` (nœud)** | Stoppe le tour avec 1–4 questions | Quand la requête est **ambiguë** (ex. stack d’observabilité) — pas pour chaque param manquant (ceux-ci → `"{{ var_x }}"`) |

### Outils / couches techniques du projet (hors « tools » agent)

| Technologie | Rôle dans cette refonte | Pourquoi |
|-------------|-------------------------|----------|
| **LangGraph** | Machine à états (nœuds + arêtes conditionnelles) | Contrôle explicite de la boucle repair ; état partagé ; meilleur fit qu’un AgentExecutor LangChain opaque |
| **LangChain (core/community/chroma/ollama)** | RAG uniquement (Documents, Chroma, embeddings) | Déjà en place ; pas besoin de le retirer pour l’index |
| **Chain-of-Thought (JSON `thought` / `fix_plan`)** | Raisonner avant d’agir ; diagnostiquer chaque échec lint | Améliore la qualité des redrafts vs retry aveugle |
| **ansible-lint** (`-p`) via `ansible_lint_runner` | Règles qualité / FQCN / YAML style | Standard de facto Ansible ; c’était la source principale des « mauvais playbooks » |
| **Validator maison** | Contraintes KB + k8s + secrets | Complète lint avec la connaissance locale des modules indexés |
| **OpenRouter / Ollama (`agent/llm.py`)** | LLM reason / repair / respond / draft | Déjà provider-agnostique ; CoT JSON compatible free-tier |
| **pytest** | Tests gate + boucle mockée | Valider la logique sans réseau / Chroma / lint réel |

---

## 5. Pourquoi ces choix techniques (synthèse)

1. **LangGraph plutôt que LangChain Agents / multi-agents**  
   Besoin d’une boucle *déterministe* autour du gate, pas d’un ReAct libre. Un état unique évite la perte de contexte lors des réparations lint.

2. **Séparer `draft` et `gate`**  
   Avant : générer puis « décorer » avec la validation.  
   Après : la validation **décide** si on s’arrête ou on répare.

3. **Supprimer `CHAT_MODE=fast`**  
   Deux chemins = deux comportements, docs fausses, qualité inégale. Un seul produit = un seul standard.

4. **Promouvoir placeholders + lint skip en échecs de gate**  
   Évite de présenter comme « Valid » un playbook non exécutable ou non linté.

5. **Échecs environmental vs repairable**  
   Si lint n’est pas installé (Windows sans WSL/Docker), redrafter 4 fois ne sert à rien — on le signale clairement.

6. **Conserver LangChain pour le RAG**  
   L’index et les `Document`s fonctionnent ; seule l’orchestration agent a changé.

---

## 6. Impact observé / vérifications

| Vérification | Résultat |
|--------------|----------|
| Compilation du graphe LangGraph | OK |
| Suite unitaire (`pytest tests/ --ignore=tests/e2e`) | **48 passed**, 1 skipped |
| Tests dédiés gate / repair loop | Couvrent : lint → repair → pass ; budget épuisé ; lint skippé (pas de redraft) ; ask_user ; mapping `AgentResponse` |
| API publique chat | Toujours `AgentResponse` (playbook, validation, tool_trace, awaiting_user) |

### Config à connaître en déploiement

```bash
# Boucle (défaut 4)
AGENT_MAX_ITERATIONS=4

# Lint obligatoire pour un badge « production-ready »
ANSIBLE_LINT_MODE=wsl   # ou docker / native
```

Sans backend lint, le playbook peut être renvoyé mais **pas** marqué production-ready.

---

## 7. Hors scope (explicitement)

- Molecule / apply réel multi-cloud (« works on every environment » runtime)
- Architecture multi-agents (Crew / supervisor)
- Remplacement du stack RAG LangChain+Chroma
- Changement du frontend React (forme de réponse inchangée)

---

## 8. Structure finale du package `agent/`

```
agent/
├── orchestrator.py        # handle_message(), AgentResponse
├── graph.py               # LangGraph : reason / tools / draft / gate / ask_user / respond
├── state.py               # AgentState + evaluate_gate
├── tools.py               # search_docs, draft_playbook, validate_*, get_module_info
├── playbook_generator.py  # une passe draft/repair
├── llm.py                 # OpenRouter / Ollama
├── prompts.py             # CoT reason/repair/respond + playbook
└── collections.py         # allow-list collections KB
```

---

## 9. Conclusion

La refonte corrige la cause structurelle des mauvais playbooks : **génération one-shot sans feedback lint dans la boucle**, plus un **double chemin** qui diluait la qualité.  

En passant à **un agent LangGraph unique** avec **CoT** et un **gate production** (validator + ansible-lint), le système itère jusqu’à un artefact lint-clean (dans la limite du budget et de l’environnement lint), tout en gardant RAG, LLM providers et l’API chat stables pour le frontend.

---

*Document généré dans le cadre du stage / PFE AnsibleAI — à joindre au journal de bord ou à la proposition d’architecture.*
