# Proposition d'Evolution Architecturale — AnsibleAI

## Introduction d'un Agent Orchestrateur et Refonte de l'Interface en Mode Conversationnel

**Projet :** AnsibleAI — Génération automatisée de playbooks Ansible par Intelligence Artificielle  
**Auteur :** Melek  
**Statut :** Proposition soumise pour validation

---

## Table des matières

1. [Contexte et état actuel](#1-contexte-et-état-actuel)
2. [Problématique identifiée](#2-problématique-identifiée)
3. [Solution proposée](#3-solution-proposée)
4. [Impact sur l'existant](#4-impact-sur-lexistant)
5. [Plan de réalisation](#5-plan-de-réalisation)
6. [Résultats attendus](#6-résultats-attendus)

---

## 1. Contexte et état actuel

### 1.1 Présentation du projet

AnsibleAI est une application web développée en Flask (Python) qui permet de générer automatiquement des playbooks Ansible à partir de descriptions en langage naturel. L'utilisateur décrit une tâche d'infrastructure (par exemple : *"déployer nginx avec helm dans le namespace production"*), et le système produit un playbook YAML valide, prêt à l'exécution.

### 1.2 Architecture actuelle

L'application repose aujourd'hui sur les composants suivants :

- **Base de connaissances :** 1 240 modules Ansible documentés, couvrant 5 collections (`ansible.builtin`, `community.general`, `azure.azcollection`, `amazon.aws`, `kubernetes.core`)
- **Deux modes de génération :**
  - *Classic* : correspondance par mots-clés entre la requête et les modules de la base de connaissances, puis génération via LLM (Ollama)
  - *RAG (Retrieval-Augmented Generation)* : recherche sémantique dans une base vectorielle ChromaDB, enrichissement du contexte par les chunks documentaires récupérés, puis génération via LLM
- **Validation post-génération :** vérification syntaxique YAML, détection de module, contrôle des paramètres requis, détection de placeholders, et intégration optionnelle d'`ansible-lint`
- **Persistance :** base MySQL pour l'historique des générations et les sessions de maintenance documentaire
- **Interface web :** interface mono-page avec quatre panneaux (Génération, Historique, Statistiques, Gestion documentaire)

### 1.3 Flux actuel de génération

```
Utilisateur                     Serveur Flask
    |                               |
    |-- requête texte ------------->|
    |   (+ choix mode: RAG/Classic) |
    |                               |-- appel unique au pipeline RAG ou Classic
    |                               |-- génération LLM (1 seul appel)
    |                               |-- validation
    |                               |-- sauvegarde en base
    |<-- playbook YAML + résultat --|
```

L'interaction est de type **requête-réponse unique** : un message entrant produit un seul playbook en sortie, sans mémoire conversationnelle ni possibilité de raffinement itératif.

---

## 2. Problématique identifiée

### 2.1 Limitations du modèle actuel

L'architecture actuelle présente plusieurs limites qui contraignent la qualité et l'expérience utilisateur :

**a) Absence de contexte conversationnel**
Chaque génération est indépendante. L'utilisateur ne peut pas dire *"maintenant ajoute la persistence"* ou *"change le namespace en staging"* sans reformuler entièrement sa requête. Il n'existe aucune mémoire des échanges précédents.

**b) Appel unique au RAG**
Le pipeline effectue une seule requête de recherche sémantique, puis une seule génération. Si la requête est complexe ou ambiguë, le système n'a aucun mécanisme pour poser des sous-questions, croiser plusieurs sources documentaires, ou affiner sa compréhension avant de produire le résultat.

**c) Mode opératoire limité**
Le système ne peut que *générer* des playbooks. L'utilisateur ne peut pas lui demander d'*expliquer* un module, de *comparer* deux approches, de *corriger* un playbook existant, ou de *diagnostiquer* une erreur — alors que ces besoins sont courants dans un flux de travail IaC.

**d) Interface orientée formulaire**
L'interface actuelle est structurée autour d'un formulaire de saisie unique avec un bouton de génération. Ce modèle ne favorise pas l'interaction itérative ni la continuité du travail sur un même sujet d'infrastructure.

### 2.2 Besoin identifié

Il est nécessaire d'introduire une couche d'intelligence intermédiaire — un **agent orchestrateur** — capable de :
- Comprendre l'intention de l'utilisateur dans le contexte d'une conversation
- Interroger le RAG autant de fois que nécessaire pour rassembler l'information pertinente
- Synthétiser une réponse complète et structurée
- Supporter des interactions multi-tours (questions de suivi, éditions, explications)

---

## 3. Solution proposée

### 3.1 Vue d'ensemble

La solution consiste en deux changements majeurs :

1. **Introduction d'un Agent Orchestrateur** entre l'utilisateur et le pipeline RAG existant
2. **Refonte de l'interface** en mode conversationnel (chat) avec persistance des conversations

### 3.2 Principe de fonctionnement de l'agent

L'agent agit comme un intermédiaire intelligent. Il ne remplace pas le RAG — il l'utilise comme outil. Son rôle est de :

1. **Analyser** le message de l'utilisateur et l'historique de la conversation
2. **Planifier** les requêtes RAG nécessaires (une ou plusieurs)
3. **Exécuter** ces requêtes pour collecter le contexte documentaire
4. **Synthétiser** une réponse finale cohérente et concise

```
┌────────────────────────────────────────────────────────┐
│                    UTILISATEUR                          │
│            (interface conversationnelle)                │
└───────────────────────┬────────────────────────────────┘
                        │ message
                        ▼
┌────────────────────────────────────────────────────────┐
│                 AGENT ORCHESTRATEUR                     │
│                                                        │
│  1. Analyse l'intention (générer, expliquer,           │
│     corriger, comparer, diagnostiquer)                 │
│                                                        │
│  2. Planifie les requêtes RAG nécessaires              │
│     (ex: 2 recherches + 1 vérification de paramètres)  │
│                                                        │
│  3. Exécute les appels aux outils :                    │
│     ┌─────────────────┐  ┌──────────────────┐         │
│     │  Recherche RAG   │  │  Info module      │         │
│     │  (1..N appels)   │  │  (paramètres,     │         │
│     │                  │  │   exemples)       │         │
│     └─────────────────┘  └──────────────────┘         │
│     ┌─────────────────┐  ┌──────────────────┐         │
│     │  Génération      │  │  Validation       │         │
│     │  playbook YAML   │  │  (YAML, params,   │         │
│     │                  │  │   ansible-lint)   │         │
│     └─────────────────┘  └──────────────────┘         │
│                                                        │
│  4. Synthétise la réponse finale                       │
└───────────────────────┬────────────────────────────────┘
                        │ réponse structurée
                        ▼
┌────────────────────────────────────────────────────────┐
│                    UTILISATEUR                          │
│   (texte + playbook YAML + validation + sources)       │
└────────────────────────────────────────────────────────┘
```

### 3.3 Capacités de l'agent

| Capacité | Description | Exemple d'interaction |
|----------|-------------|----------------------|
| **Génération** | Produire un playbook Ansible complet | *"Déploie redis avec helm dans production"* |
| **Explication** | Expliquer un module, ses paramètres, son usage | *"Explique-moi le module k8s_drain"* |
| **Correction** | Analyser et corriger un playbook fourni par l'utilisateur | *"Voici mon playbook, pourquoi ça ne marche pas ?"* |
| **Comparaison** | Comparer deux approches ou modules pour une même tâche | *"Quelle différence entre helm et k8s pour déployer ?"* |
| **Edition** | Modifier un playbook précédemment généré dans la conversation | *"Ajoute la persistence au playbook précédent"* |
| **Diagnostic** | Identifier les problèmes dans une configuration | *"Mon déploiement ne démarre pas, que vérifier ?"* |

### 3.4 Refonte de l'interface

L'interface actuelle à panneaux sera remplacée par une disposition conversationnelle :

```
┌─────────────┬──────────────────────────────┬──────────────┐
│  THREADS    │        ZONE DE CHAT          │  PANNEAU     │
│             │                              │  LATÉRAL     │
│  [+ Nouveau]│  Agent: Bonjour ! Je suis    │              │
│             │  votre assistant Ansible...   │  [Stats]     │
│  Conv. 1    │                              │  [Docs Mgmt] │
│  Conv. 2  * │  Vous: déploie nginx avec    │              │
│  Conv. 3    │  helm en production          │              │
│             │                              │              │
│             │  Agent: Voici le playbook    │              │
│             │  ┌──────────────────────┐    │              │
│             │  │ ---                  │    │              │
│             │  │ - name: Deploy nginx │    │              │
│             │  │   hosts: localhost   │    │              │
│             │  │   ...               │    │              │
│             │  └──────────────────────┘    │              │
│             │  ✅ Validation réussie       │              │
│             │  📖 Source: kubernetes.core   │              │
│             │                              │              │
│             │  [Saisir un message...][Env] │              │
└─────────────┴──────────────────────────────┴──────────────┘
```

**Barre latérale gauche :** liste des conversations (threads) avec possibilité de créer, supprimer et renommer  
**Zone centrale :** fil de messages avec affichage riche (texte, blocs de code YAML, badges de validation, références documentaires)  
**Panneau droit (repliable) :** tableaux de bord Statistiques et Gestion documentaire, conservés de l'architecture actuelle

---

## 4. Impact sur l'existant

### 4.1 Ce qui ne change pas

| Composant | Statut |
|-----------|--------|
| Pipeline RAG (`rag/`) | **Inchangé** — utilisé comme outil par l'agent |
| Pipeline classique (`pipeline/`) | **Inchangé** — disponible comme outil alternatif |
| Base vectorielle ChromaDB | **Inchangée** |
| Gestion documentaire (`/docs/*`) | **Inchangée** — conservée comme panneau latéral |
| Tableau de bord Statistiques | **Inchangé** — même source de données |
| Évaluateur RAGAS (`rag/evaluator.py`) | **Inchangé** |
| Modèle LLM (Ollama `qwen2.5-coder`) | **Inchangé** — réutilisé par l'agent |

### 4.2 Ce qui est ajouté

| Composant | Nature |
|-----------|--------|
| `agent/orchestrator.py` | Nouveau — logique de l'agent |
| `agent/tools.py` | Nouveau — encapsulation des outils |
| `agent/prompts.py` | Nouveau — prompts système |
| Tables `chat_threads`, `chat_messages` | Nouveau — modèle de données conversationnel |
| Endpoints `/api/chat`, `/api/threads` | Nouveau — API conversationnelle |

### 4.3 Ce qui est modifié

| Composant | Modification |
|-----------|-------------|
| `models.py` | Ajout des deux nouveaux modèles |
| `app.py` | Ajout des routes chat, suppression des routes `/generate` et `/history` |
| `templates/index.html` | Refonte de la disposition (chat) |
| `static/js/app.js` | Réécriture pour la logique conversationnelle |
| `static/css/style.css` | Nouveaux styles pour le chat |

---

## 5. Plan de réalisation

### Phase 1 — Agent Backend 
1. Création des modèles `ChatThread` et `ChatMessage`
2. Développement du module `agent/` (orchestrateur, outils, prompts)
3. Mise en place des endpoints API `/api/chat` et `/api/threads`
4. Tests fonctionnels de l'agent (génération, explication, édition)

### Phase 2 — Interface Chat 
5. Refonte du template HTML en disposition conversationnelle
6. Développement du JavaScript pour la gestion des messages et threads
7. Intégration des panneaux Statistiques et Gestion documentaire comme panneaux latéraux
8. Stylisation CSS et ajustements visuels

### Phase 3 — Intégration et validation 
9. Vérification de la compatibilité avec les statistiques existantes
10. Tests d'intégration end-to-end
11. Correction des régressions éventuelles


---

## 6. Résultats attendus

### 6.1 Améliorations fonctionnelles

- **Interactions multi-tours :** l'utilisateur peut affiner, compléter ou modifier sa demande de manière itérative au sein d'une même conversation
- **Réponses plus pertinentes :** l'agent peut interroger le RAG plusieurs fois pour croiser les informations avant de répondre
- **Polyvalence :** au-delà de la simple génération, le système peut expliquer, corriger, comparer et diagnostiquer
- **Historique conversationnel :** les conversations sont persistantes et consultables, remplaçant l'historique linéaire actuel

### 6.2 Améliorations techniques

- **Découplage :** l'agent est un composant séparé qui utilise le RAG existant sans le modifier
- **Extensibilité :** de nouveaux outils peuvent être ajoutés à l'agent (par exemple, exécution de playbooks, vérification de syntaxe Jinja2) sans impact sur l'architecture
- **Réutilisation :** le pipeline RAG et les évaluations RAGAS restent fonctionnels indépendamment

### 6.3 Améliorations de l'expérience utilisateur

- **Interface intuitive :** le modèle conversationnel est familier (similaire à ChatGPT) et réduit la courbe d'apprentissage
- **Continuité du travail :** l'utilisateur peut reprendre une conversation antérieure et poursuivre son travail
- **Transparence :** les sources documentaires et les résultats de validation restent visibles dans chaque réponse

---

*Document soumis pour approbation avant implémentation.*
