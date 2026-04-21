"""
=============================================================
  AnsibleAI RAG — Main Pipeline Entry Point
  Orchestrates: ingestion → indexing → retrieval → agent playbook generation
=============================================================
  Quick start:
    # 1. Install dependencies
    pip install langchain langchain-community chromadb ragas datasets

    # 2. Pull embedding model
    ollama pull nomic-embed-text

    # 3. Build the index (first time)
    python rag/pipeline.py --build

    # 4. Generate a playbook
    python rag/pipeline.py --query "deploy nginx using helm"

    # 5. Evaluate the pipeline
    python rag/pipeline.py --evaluate --compare

    # 6. Run everything from scratch
    python rag/pipeline.py --build --query "scale a deployment to 3 replicas" --evaluate
=============================================================
"""

import os
import sys
import json
import argparse
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

FILE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(FILE_DIR)

os.chdir(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def cmd_build(collection: str = None, reset: bool = False):
    """Build or update the vector index."""
    print("\n" + "═" * 60)
    print("  Step 1/2 — Building RAG Index")
    print("═" * 60)

    from rag.indexer import build_index
    build_index(collection_name=collection, reset=reset)


def cmd_generate(query: str) -> tuple[str, dict]:
    """Run full RAG pipeline for a single query."""
    print("\n" + "═" * 60)
    print("  AnsibleAI RAG Pipeline")
    print(f"  Query: {query}")
    print("═" * 60)

    from agent.playbook_generator import generate_playbook_from_retrieval
    from rag.indexer   import load_vectorstore
    from rag.retriever import get_retrieval_metadata

    vectorstore    = load_vectorstore()
    retrieval_meta = get_retrieval_metadata(query, vectorstore)
    output_path, yaml_content = generate_playbook_from_retrieval(query, retrieval_meta)

    print(f"\n  ✅ Generated: {output_path}")
    print(f"  Module     : {retrieval_meta.get('primary_module')}")
    print(f"  Collection : {retrieval_meta.get('primary_collection')}")
    print(f"  RAG score  : {retrieval_meta.get('primary_score')}")

    return output_path, retrieval_meta


def cmd_evaluate(quick: bool = False, compare: bool = False):
    """Run RAGAS evaluation."""
    print("\n" + "═" * 60)
    print("  AnsibleAI RAGAS Evaluation")
    print("═" * 60)

    from rag.evaluator import run_evaluation
    run_evaluation(quick=quick, compare=compare)


def cmd_status():
    """Show current RAG system status."""
    print("\n" + "═" * 60)
    print("  AnsibleAI RAG — System Status")
    print("═" * 60)

    # ChromaDB
    try:
        import chromadb
        from chromadb.config import Settings
        client = chromadb.PersistentClient(
            path="data/chromadb",
            settings=Settings(anonymized_telemetry=False)
        )
        col   = client.get_collection("ansible_docs")
        count = col.count()
        print(f"  ✅ ChromaDB   : {count:,} chunks indexed")
    except Exception as e:
        print(f"  ❌ ChromaDB   : {e}")
        print(f"     → Run: python rag/pipeline.py --build")

    # Ollama models
    try:
        import requests
        tags   = requests.get("http://localhost:11434/api/tags", timeout=3).json()
        models = [m["name"] for m in tags.get("models", [])]
        llm    = os.getenv("OLLAMA_MODEL")
        embed  = "nomic-embed-text"
        print(f"  {'✅' if any(embed in m for m in models) else '❌'} Embed model  : {embed}")
        print(f"  {'✅' if any(llm.split(':')[0] in m for m in models) else '❌'} LLM model    : {llm}")
    except Exception:
        print("  ❌ Ollama     : not reachable (is it running?)")

    # Parsed data
    parsed_dir = "data/parsed"
    if os.path.exists(parsed_dir):
        collections = [d for d in os.listdir(parsed_dir) if os.path.isdir(os.path.join(parsed_dir, d))]
        total_mods  = sum(
            len([f for f in os.listdir(os.path.join(parsed_dir, d)) if f.endswith(".json")])
            for d in collections
        )
        print(f"  ✅ Parsed data: {len(collections)} collections, {total_mods} modules")
    else:
        print(f"  ❌ Parsed data: not found — run scraper first")

    # Evaluation report
    eval_report = "reports/ragas_evaluation_report.json"
    if os.path.exists(eval_report):
        with open(eval_report) as f:
            r = json.load(f)
        m = r.get("rag_metrics", {})
        print(f"\n  Last RAGAS evaluation ({r.get('evaluated_at', '—')[:10]}):")
        for k, v in m.items():
            bar = "█" * int(v * 20)
            print(f"    {k:<25} {v:.4f}  {bar}")
    else:
        print(f"\n  No evaluation report yet.")
        print(f"  → Run: python rag/pipeline.py --evaluate")

    print("═" * 60)


# ─────────────────────────────────────────────
#  FLASK-COMPATIBLE FUNCTION
# ─────────────────────────────────────────────

def generate_playbook_rag_v2(user_input: str) -> tuple[str, dict]:
    """
    Drop-in replacement for the old generate_playbook_rag().
    Used by app.py Flask routes.
    Returns (output_path, retrieval_meta).
    """
    from agent.playbook_generator import generate_playbook_from_retrieval
    from rag.indexer   import load_vectorstore
    from rag.retriever import get_retrieval_metadata

    vectorstore    = load_vectorstore()
    retrieval_meta = get_retrieval_metadata(user_input, vectorstore)
    output_path, _ = generate_playbook_from_retrieval(user_input, retrieval_meta)

    return output_path, retrieval_meta


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AnsibleAI RAG Pipeline — ChromaDB retrieval + agent LLM + RAGAS"
    )
    parser.add_argument("--build",      action="store_true", help="Build/update the vector index")
    parser.add_argument("--collection", type=str, default=None, help="Index one collection only")
    parser.add_argument("--reset",      action="store_true", help="Wipe ChromaDB before building")
    parser.add_argument("--query",      type=str, default=None, help="Generate a playbook from a query")
    parser.add_argument("--evaluate",   action="store_true", help="Run RAGAS evaluation")
    parser.add_argument("--compare",    action="store_true", help="Compare RAG vs classic (with --evaluate)")
    parser.add_argument("--quick",      action="store_true", help="Quick eval: 5 samples only")
    parser.add_argument("--status",     action="store_true", help="Show system status")
    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        sys.exit(0)

    if args.status:
        cmd_status()

    if args.build:
        cmd_build(collection=args.collection, reset=args.reset)

    if args.query:
        cmd_generate(args.query)

    if args.evaluate:
        cmd_evaluate(quick=args.quick, compare=args.compare)
