# Script de Présentation — AI Powered IaC (AnsibleAI)

**Durée estimée : 20–25 minutes**

---

## SLIDE 1 — Page de garde (30 sec)

Bonjour membres du jury, bonjour à tous.

Je suis [Votre Nom], étudiant en [Filière], et je vous présente aujourd'hui mon projet de fin d'études intitulé : **Automatisation du déploiement d'infrastructure avec IA — AI Powered IaC**.

---

## SLIDE 2 — Plan de la présentation (20 sec)

Voici le plan que nous allons suivre :

1. Contexte et problématique
2. Objectifs du projet
3. Architecture et technologies
4. Pipeline de données
5. Génération de playbooks par IA
6. Validation automatique
7. Interface web
8. Démonstration
9. Bilan et perspectives

---

## SLIDE 3 — Contexte et problématique (2 min)

Aujourd'hui, les entreprises gèrent des infrastructures de plus en plus complexes : des serveurs cloud sur AWS et Azure, de l'orchestration avec Kubernetes, des systèmes Linux et Windows à configurer.

L'approche **Infrastructure as Code**, notamment avec **Ansible**, permet d'automatiser tout ça via des fichiers appelés **playbooks**.

Mais le problème, c'est que rédiger ces playbooks reste une tâche **manuelle et complexe**. Ansible compte aujourd'hui plus de **1 200 modules** répartis sur des dizaines de collections. Pour chaque tâche, il faut :

- Identifier le bon module parmi des centaines
- Comprendre ses paramètres obligatoires et optionnels
- Respecter la syntaxe YAML et les bonnes pratiques

Résultat : c'est **chronophage**, **source d'erreurs**, et ça demande une expertise importante.

D'où notre question : **Comment peut-on utiliser l'intelligence artificielle pour générer automatiquement des playbooks Ansible valides à partir d'une simple description en langage naturel ?**

---

## SLIDE 4 — Objectifs du projet (1 min)

Notre projet vise à répondre à cette problématique en construisant un système complet qui :

1. **Collecte et structure** automatiquement la documentation officielle d'Ansible
2. **Indexe** cette documentation dans une base vectorielle pour permettre la recherche sémantique
3. **Génère** des playbooks Ansible à partir de requêtes en langage naturel grâce à un LLM local
4. **Valide** automatiquement les playbooks générés avant tout déploiement
5. Propose une **interface web** intuitive pour interagir avec le système

---

## SLIDE 5 — Architecture générale (2 min)

*(Montrer le diagramme d'architecture)*

L'architecture du projet se décompose en **trois grandes couches** :

**Premièrement, la couche données** — c'est notre pipeline offline :
- On scrape la documentation depuis docs.ansible.com
- On parse le HTML pour extraire les informations structurées
- On stocke le tout en fichiers JSON organisés par collection

**Deuxièmement, la couche intelligence** — c'est le cœur du système :
- Un mode **classique** qui fait de la correspondance par mots-clés pour trouver le bon module, puis construit un prompt ciblé pour le LLM
- Un mode **RAG** — Retrieval-Augmented Generation — qui utilise des embeddings vectoriels et ChromaDB pour retrouver le contexte documentaire le plus pertinent, puis enrichit le prompt envoyé au LLM

**Troisièmement, la couche présentation** — l'interface Flask qui relie tout et offre la génération, l'historique, les statistiques et la gestion documentaire.

---

## SLIDE 6 — Technologies utilisées (1 min)

Voici la stack technique :

- **Python** comme langage principal
- **Flask** pour le serveur web et l'API
- **MySQL** avec SQLAlchemy pour la persistance
- **BeautifulSoup** pour le scraping et le parsing HTML
- **Ollama** pour exécuter le LLM localement — on utilise le modèle **qwen2.5-coder:7b**
- **LangChain** pour orchestrer la chaîne RAG
- **ChromaDB** comme base de données vectorielle
- **nomic-embed-text** comme modèle d'embeddings
- Et **ansible-lint** pour la validation avancée

Un point important : tout fonctionne **en local**, sans dépendance à des API cloud payantes.

---

## SLIDE 7 — Pipeline de données : Scraping (1.5 min)

La première étape est la collecte des données.

Notre scraper parcourt automatiquement les pages d'index des collections Ansible sur docs.ansible.com. Il cible **5 collections principales** :

- **ansible.builtin** — les modules de base
- **amazon.aws** — pour le cloud AWS
- **azure.azcollection** — pour Azure
- **kubernetes.core** — pour l'orchestration Kubernetes
- **community.general** — les modules communautaires

Pour chaque collection, il identifie les liens vers les pages de modules, télécharge le HTML et le stocke localement. Au total, on a collecté la documentation de plus de **1 240 modules**.

---

## SLIDE 8 — Pipeline de données : Parsing et structuration (1.5 min)

Une fois le HTML téléchargé, le parser entre en jeu.

Il extrait de chaque page de module :
- Le **synopsis** et la description
- La **table des paramètres** avec les types, les valeurs par défaut, et si le paramètre est obligatoire
- Les **exemples** de code fournis dans la documentation

Tout est transformé en fichiers **JSON structurés**, organisés par collection dans le dossier `data/parsed/`.

Ensuite, le structureur enrichit les données avec des **mots-clés d'intent** — des phrases associées à chaque module pour faciliter la correspondance avec les requêtes utilisateur — et génère un **manifest unifié** qui sert de point d'entrée à la base de connaissances.

---

## SLIDE 9 — Génération : Mode classique (2 min)

Le premier mode de génération fonctionne par **correspondance d'intent**.

Quand l'utilisateur tape par exemple : *"Deploy an Nginx container on Kubernetes"*

Le système :
1. **Analyse la requête** — identifie des indices de collection (ici "Kubernetes") et des mots-clés
2. **Score chaque module** — en combinant la correspondance avec les task_keywords, le nom du module et sa description
3. **Sélectionne le meilleur module** — dans cet exemple, `kubernetes.core.k8s`
4. **Construit le contexte** — extrait les paramètres pertinents et les exemples de ce module
5. **Génère un prompt structuré** avec des contraintes précises : YAML valide, pas de placeholders, modules réels uniquement
6. **Envoie au LLM** via l'API Ollama
7. **Extrait le YAML** de la réponse et sauvegarde le playbook

---

## SLIDE 10 — Génération : Mode RAG (2 min)

Le mode RAG va plus loin en utilisant la **recherche sémantique**.

**Phase d'indexation** (offline) :
- Chaque module est découpé en plusieurs **chunks** : vue d'ensemble, paramètres obligatoires, groupes de paramètres optionnels, exemples
- Ces chunks sont transformés en **vecteurs** par le modèle d'embeddings **nomic-embed-text**
- Stockés dans **ChromaDB** avec des métadonnées pour le filtrage

**Phase de génération** (runtime) :
- La requête utilisateur est convertie en vecteur
- On effectue une **recherche par similarité** dans ChromaDB
- Les chunks les plus pertinents sont récupérés et dédupliqués
- LangChain construit un prompt enrichi avec ce **contexte documentaire**
- Le LLM génère le playbook avec une connaissance précise des modules et paramètres concernés

L'avantage du RAG : le LLM ne s'appuie pas sur sa mémoire interne potentiellement obsolète, mais sur la **documentation officielle à jour**.

---

## SLIDE 11 — Validation automatique (1.5 min)

Chaque playbook généré passe par un pipeline de validation en **5 niveaux** :

1. **Validité YAML** — le fichier est-il du YAML syntaxiquement correct ?
2. **Structure du playbook** — présence des clés obligatoires : `hosts`, `tasks`, structure de liste
3. **Modules connus** — chaque module utilisé existe-t-il dans notre base de connaissances ?
4. **Paramètres obligatoires** — les paramètres requis par la documentation sont-ils bien présents ?
5. **Détection de placeholders** — vérification qu'il ne reste pas de valeurs factices comme `your_value_here`

En option, **ansible-lint** est appelé pour une analyse avancée des bonnes pratiques.

Le résultat est un rapport détaillé avec un score de validité, des warnings et des suggestions de correction.

---

## SLIDE 12 — Interface web (1 min)

*(Montrer une capture d'écran de l'interface)*

L'interface web AnsibleAI se compose de **4 panneaux** :

- **Generate** — l'écran principal où l'utilisateur saisit sa requête en langage naturel, choisit le mode classique ou RAG, et obtient le playbook généré avec le rapport de validation
- **History** — l'historique complet de toutes les générations avec le score de validation
- **Statistics** — des métriques sur l'utilisation : taux de succès, modules les plus utilisés, temps de génération
- **Docs** — la gestion de la base documentaire : vérification des mises à jour, re-scraping, backup et restauration

---

## SLIDE 13 — Démonstration (3–4 min)

Je vais maintenant vous faire une démonstration en direct.

*(Ouvrir http://localhost:5000)*

**Scénario 1 — Mode classique :**
> Requête : "Create a Kubernetes deployment for an Nginx web server with 3 replicas"

*(Montrer la génération, le playbook résultat, le rapport de validation)*

**Scénario 2 — Mode RAG :**
> Requête : "Create an S3 bucket on AWS with versioning enabled and server-side encryption"

*(Montrer la différence avec le mode RAG : contexte plus riche, playbook plus complet)*

Comme vous pouvez le voir, le système génère des playbooks valides, structurés et conformes à la documentation officielle, en quelques secondes.

---

## SLIDE 14 — Résultats et évaluation (1 min)

Quelques chiffres clés :

- **1 240+ modules** indexés dans la base de connaissances
- **5 collections** Ansible couvertes
- Temps de génération moyen : **quelques secondes** en mode classique, légèrement plus en RAG
- Le mode RAG produit des playbooks plus complets et plus précis grâce au contexte documentaire
- La validation détecte efficacement les erreurs de syntaxe, les modules invalides et les paramètres manquants

---

## SLIDE 15 — Difficultés rencontrées (1 min)

Parmi les défis principaux :

- La **diversité du format HTML** entre les différentes collections Ansible, qui a nécessité un parser robuste et adaptable
- La **qualité de génération du LLM** — parfois des hallucinations ou des paramètres inventés, d'où l'importance du pipeline de validation
- L'**optimisation du chunking** pour le RAG — trouver le bon découpage pour maximiser la pertinence de la recherche
- L'exécution **locale** du LLM qui demande des ressources matérielles conséquentes

---

## SLIDE 16 — Perspectives (1 min)

Pour la suite, plusieurs axes d'amélioration sont envisagés :

- **Dockerisation** complète de l'application pour un déploiement simplifié
- Mise en place d'un **pipeline CI/CD** pour automatiser les tests et le déploiement
- Support de **nouvelles collections** Ansible et mise à jour automatique de la documentation
- **Fine-tuning** du LLM sur des données Ansible pour améliorer la précision
- Ajout d'un mode **multi-playbook** pour des scénarios d'infrastructure complexes
- **Évaluation RAGAS** systématique pour mesurer la qualité du RAG

---

## SLIDE 17 — Conclusion (30 sec)

Pour conclure, ce projet démontre qu'il est tout à fait possible de combiner l'intelligence artificielle générative avec les techniques de RAG pour **automatiser la génération de scripts d'infrastructure**. Le système que nous avons développé transforme une simple description en langage naturel en un playbook Ansible valide et conforme à la documentation officielle, tout en fonctionnant entièrement en local.

Merci pour votre attention. Je suis prêt à répondre à vos questions.

---

## QUESTIONS ANTICIPÉES

**Q : Pourquoi Ollama et pas une API cloud comme ChatGPT ?**
> R : Le choix d'Ollama permet un fonctionnement 100% local, sans coût d'API, sans dépendance réseau, et avec un contrôle total sur les données. C'est important dans un contexte d'infrastructure où les données peuvent être sensibles.

**Q : Quelle est la différence entre le mode classique et le mode RAG ?**
> R : Le mode classique utilise une correspondance par mots-clés pour trouver un seul module puis construit un prompt ciblé. Le mode RAG utilise la recherche sémantique pour retrouver plusieurs chunks de documentation pertinents et enrichit le prompt avec ce contexte, produisant des résultats plus précis et complets.

**Q : Comment gérez-vous les hallucinations du LLM ?**
> R : C'est le rôle du pipeline de validation en 5 niveaux. On vérifie que les modules existent dans notre base, que les paramètres obligatoires sont présents, et qu'il n'y a pas de valeurs placeholder. Le mode RAG réduit aussi les hallucinations en ancrant le LLM dans la documentation réelle.

**Q : Le système peut-il gérer des playbooks complexes multi-tâches ?**
> R : Actuellement, le système est optimisé pour des playbooks à tâche unique ou simple. Le support multi-tâches est prévu comme perspective d'amélioration.

**Q : Pourquoi ces 5 collections spécifiquement ?**
> R : Ce sont les collections les plus utilisées en production : les modules de base Ansible, les deux principaux cloud providers (AWS et Azure), Kubernetes pour l'orchestration, et community.general qui couvre une large gamme d'outils.
