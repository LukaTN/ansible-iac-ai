"""
=============================================================
  AnsibleAI RAG — Step 5 : Evaluator (RAGAS 0.4.x + Ollama)
  Uses Ollama as LLM judge — NO OpenAI API key required.
=============================================================
  Metrics:
    - faithfulness       : answer grounded in retrieved context?
    - answer_relevancy   : answer relevant to the question?
    - context_precision  : retrieved chunks actually useful?
    - context_recall     : context covers the expected answer?
=============================================================
  Usage:
    python rag/evaluator.py                   # full eval (20 samples)
    python rag/evaluator.py --quick           # 5 samples only
    python rag/evaluator.py --compare         # RAG vs classic
=============================================================
"""

import argparse
import json
import math
import os
import sys
import traceback
import warnings
from datetime import datetime

# RAGAS 0.4.x has two metric hierarchies: ragas.metrics (old, works with
# evaluate()) and ragas.metrics.collections (new, only works with @experiment).
# Ollama requires LangchainLLMWrapper which is also deprecated but functional.
# Suppress the noise — these are the only correct imports for our setup.
warnings.filterwarnings("ignore", message=".*LangchainLLMWrapper.*deprecated.*")
warnings.filterwarnings("ignore", message=".*LangchainEmbeddingsWrapper.*deprecated.*")
warnings.filterwarnings("ignore", message=".*Importing.*from 'ragas.metrics'.*deprecated.*")


FILE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.dirname(FILE_DIR)
PROJECT_ROOT = os.path.dirname(BACKEND_ROOT)
os.chdir(PROJECT_ROOT)
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPORT_DIR   = "reports"
EVAL_DIR     = "data/rag_eval"
DATASET_FILE = "backend/rag/test_dataset.json"

OLLAMA_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
JUDGE_MODEL  = os.getenv("RAGAS_JUDGE_MODEL", "qwen2.5-coder:14b")
EMBED_MODEL  = "nomic-embed-text"


# ─────────────────────────────────────────────
#  RAGAS 0.4.x + OLLAMA SETUP
# ─────────────────────────────────────────────

def _build_ragas_llm():
    """Build RAGAS-compatible LLM wrapper using Ollama."""
    from langchain_ollama import OllamaLLM
    from ragas.llms import LangchainLLMWrapper
    llm = OllamaLLM(model=JUDGE_MODEL, base_url=OLLAMA_URL, temperature=0)
    return LangchainLLMWrapper(llm)


def _build_ragas_embeddings():
    """Build RAGAS-compatible embeddings using Ollama nomic-embed-text."""
    from langchain_ollama import OllamaEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    emb = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_URL)
    return LangchainEmbeddingsWrapper(emb)


def get_ragas_components():
    """
    Return (evaluate_fn, metrics_list, ragas_llm, ragas_emb) for RAGAS 0.4.x.
    Uses old-style ragas.metrics (only ones compatible with evaluate()).
    """
    from ragas import evaluate
    from ragas.metrics import (
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
    )

    print(f"\n  [RAGAS] Configuring LLM judge: {JUDGE_MODEL} via Ollama")
    print(f"  [RAGAS] Embeddings: {EMBED_MODEL} via Ollama")

    ragas_llm = _build_ragas_llm()
    ragas_emb = _build_ragas_embeddings()

    metrics = [
        Faithfulness(),
        AnswerRelevancy(),
        ContextPrecision(),
        ContextRecall(),
    ]

    return evaluate, metrics, ragas_llm, ragas_emb


# ─────────────────────────────────────────────
#  PIPELINE RUNNERS
# ─────────────────────────────────────────────

def run_rag_pipeline(question: str, vectorstore) -> dict:
    """Run full RAG pipeline for one question."""
    from agent.playbook_generator import generate_playbook_from_retrieval
    from rag.retriever import get_retrieval_metadata

    retrieval_meta = get_retrieval_metadata(question, vectorstore)
    docs   = retrieval_meta.get("docs", [])

    _, yaml_content = generate_playbook_from_retrieval(question, retrieval_meta)

    contexts = [doc.page_content for doc in docs]
    if not contexts:
        contexts = ["No context retrieved."]

    return {
        "user_input"          : question,
        "response"            : yaml_content or "No playbook generated.",
        "retrieved_contexts"  : contexts,
        "module"              : retrieval_meta.get("primary_module", ""),
        "score"               : retrieval_meta.get("primary_score", 0.0),
        "collection"          : retrieval_meta.get("primary_collection", ""),
    }


def run_classic_pipeline(question: str) -> dict:
    """Run classic keyword-based pipeline for comparison."""
    sys.path.insert(0, os.path.join(BACKEND_ROOT, "pipeline"))
    from phase4_generator import (
        build_module_context,
        find_best_module,
        generate_playbook,
        load_knowledge_base,
    )

    kb      = load_knowledge_base()
    modules = kb["modules"]
    slug, entry, score = find_best_module(question, modules)
    context = build_module_context(entry)

    output_path = generate_playbook(question)
    with open(output_path, encoding="utf-8") as f:
        raw = f.read()

    lines = [l for l in raw.splitlines() if not l.startswith("#")]
    start = next((i for i, l in enumerate(lines) if l.strip() == "---"), None)
    yaml_clean = "\n".join(lines[start:]).strip() if start is not None else raw

    return {
        "user_input"          : question,
        "response"            : yaml_clean or "No playbook generated.",
        "retrieved_contexts"  : [context] if context else ["No context available."],
        "module"              : entry.get("module", ""),
        "score"               : score,
        "collection"          : entry.get("collection", ""),
    }


# ─────────────────────────────────────────────
#  RAGAS EVALUATION
# ─────────────────────────────────────────────

def _safe_mean(values) -> float:
    """Compute mean from a list of scores, ignoring NaN values."""
    if isinstance(values, (int, float)):
        return 0.0 if math.isnan(values) else float(values)
    if not hasattr(values, "__iter__"):
        return float(values)
    clean = [v for v in values if isinstance(v, (int, float)) and not math.isnan(v)]
    return sum(clean) / len(clean) if clean else 0.0


def evaluate_pipeline(
    results: list[dict],
    ground_truths: list[str],
    label: str = "RAG"
) -> dict:
    """Run RAGAS evaluation on pipeline results using Ollama as judge."""
    from ragas import EvaluationDataset, SingleTurnSample
    from ragas.run_config import RunConfig

    evaluate, metrics, ragas_llm, ragas_emb = get_ragas_components()

    print(f"\n  [RAGAS] Evaluating {label} pipeline ({len(results)} samples)...")

    samples = []
    for r, gt in zip(results, ground_truths):
        samples.append(SingleTurnSample(
            user_input=r["user_input"],
            response=r["response"],
            retrieved_contexts=r["retrieved_contexts"],
            reference=gt,
        ))

    dataset = EvaluationDataset(samples=samples)

    run_config = RunConfig(
        timeout=600,
        max_retries=5,
        max_wait=120,
        max_workers=2,
    )

    print(f"  [RAGAS] Running evaluation with {len(metrics)} metrics...")
    print(f"  [RAGAS] Timeout: {run_config.timeout}s, workers: {run_config.max_workers}")
    scores = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=ragas_llm,
        embeddings=ragas_emb,
        run_config=run_config,
        raise_exceptions=False,
        show_progress=True,
    )

    print(f"  [RAGAS] Raw scores type: {type(scores)}")

    metric_names = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    result = {}
    for key in metric_names:
        try:
            raw_val = scores[key]
            val = _safe_mean(raw_val)
            result[key] = round(val, 4)
            print(f"  [RAGAS] {key}: raw={type(raw_val).__name__}, mean={result[key]}")
        except Exception as e:
            print(f"  [WARN] Could not extract '{key}': {e}")
            result[key] = 0.0

    result["overall"] = round(sum(result.values()) / 4, 4)

    print(f"\n  {label} RAGAS Scores:")
    print(f"  {'─'*42}")
    for k, v in result.items():
        bar = "█" * int(v * 20)
        print(f"  {k:<25} {v:.4f}  {bar}")

    return result


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def run_evaluation(quick: bool = False, compare: bool = False):
    """Full evaluation pipeline."""
    print(f"\n{'='*60}")
    print("  AnsibleAI — RAGAS Evaluation (Ollama judge)")
    print(f"  Judge model : {JUDGE_MODEL}")
    print(f"  Embed model : {EMBED_MODEL}")
    print(f"  Started     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if not os.path.exists(DATASET_FILE):
        raise FileNotFoundError(
            f"Test dataset not found: {DATASET_FILE}\n"
            "→ Make sure backend/rag/test_dataset.json exists."
        )

    with open(DATASET_FILE, encoding="utf-8") as f:
        dataset = json.load(f)

    samples = dataset["samples"]
    if quick:
        samples = samples[:5]
        print(f"  [Quick mode] Using {len(samples)} samples.")

    questions     = [s["question"]     for s in samples]
    ground_truths = [s["ground_truth"] for s in samples]

    os.makedirs(EVAL_DIR,   exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    # ── RAG evaluation ──
    print(f"\n  [1] Running RAG pipeline on {len(samples)} queries...")
    try:
        from rag.indexer import load_vectorstore
    except ImportError:
        from indexer import load_vectorstore
    vectorstore = load_vectorstore()

    rag_results = []
    for i, q in enumerate(questions, 1):
        print(f"\n  [{i}/{len(questions)}] {q[:65]}...")
        try:
            result = run_rag_pipeline(q, vectorstore)
            rag_results.append(result)
        except Exception as e:
            print(f"    [ERROR] {e}")
            traceback.print_exc()
            rag_results.append({
                "user_input": q,
                "response": "Error generating playbook.",
                "retrieved_contexts": ["No context due to error."],
                "module": "", "score": 0.0, "collection": ""
            })

    rag_metrics = evaluate_pipeline(rag_results, ground_truths, label="RAG")

    # ── Classic comparison ──
    classic_metrics = None
    if compare:
        print("\n  [2] Running CLASSIC pipeline for comparison...")
        classic_results = []
        for i, q in enumerate(questions, 1):
            print(f"  [{i}/{len(questions)}] {q[:65]}...")
            try:
                classic_results.append(run_classic_pipeline(q))
            except Exception as e:
                print(f"    [ERROR] {e}")
                traceback.print_exc()
                classic_results.append({
                    "user_input": q,
                    "response": "Error generating playbook.",
                    "retrieved_contexts": ["No context due to error."],
                })

        classic_metrics = evaluate_pipeline(
            classic_results, ground_truths, label="Classic"
        )

    # ── Report ──
    report = {
        "evaluated_at"   : datetime.now().isoformat(),
        "judge_model"    : JUDGE_MODEL,
        "embed_model"    : EMBED_MODEL,
        "samples"        : len(samples),
        "quick_mode"     : quick,
        "rag_metrics"    : rag_metrics,
        "classic_metrics": classic_metrics,
        "improvement"    : {
            k: round(rag_metrics[k] - classic_metrics.get(k, 0), 4)
            for k in rag_metrics
        } if classic_metrics else None,
    }

    report_path = os.path.join(REPORT_DIR, "ragas_evaluation_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # ── Summary ──
    print(f"\n{'='*60}")
    print("  EVALUATION COMPLETE")
    if classic_metrics:
        print(f"\n  {'Metric':<25} {'Classic':>10} {'RAG':>10} {'Δ':>10}")
        print(f"  {'─'*58}")
        for k in rag_metrics:
            r = rag_metrics[k]
            c = classic_metrics.get(k, 0)
            d = r - c
            sym = "▲" if d > 0 else ("▼" if d < 0 else "─")
            print(f"  {k:<25} {c:>10.4f} {r:>10.4f}  {sym} {abs(d):.4f}")
    else:
        for k, v in rag_metrics.items():
            print(f"  {k:<25} {v:.4f}")

    print(f"\n  Report → {report_path}")
    print(f"{'='*60}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AnsibleAI RAGAS Evaluator (Ollama)")
    parser.add_argument("--quick",   action="store_true", help="5 samples only")
    parser.add_argument("--compare", action="store_true", help="RAG vs classic")
    args = parser.parse_args()
    run_evaluation(quick=args.quick, compare=args.compare)
