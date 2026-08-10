"""
Trace a single query through the full RAG pipeline for debugging.
Usage: python scripts/trace_query.py "your query here"
"""

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rag.retriever import analyze_query, route_collections, score_collections


def trace(query: str):
    SEP = "=" * 70

    # ── STAGE 1 ──────────────────────────────────────────────────
    print(SEP)
    print("  STAGE 1: QUERY ANALYSIS")
    print(SEP)
    a = analyze_query(query)
    print(f"  raw       : {a.raw}")
    print(f"  tokens    : {sorted(a.tokens)}")
    print(f"  write     : {a.write_intent}")
    print(f"  read      : {a.read_intent}")
    print(f"  example   : {a.example_intent}")
    print(f"  param     : {a.param_intent}")
    print(f"  fqcn_cols : {a.fqcn_collections}")

    # ── STAGE 2 ──────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  STAGE 2: COLLECTION SCORING + ROUTING")
    print(SEP)
    scores = score_collections(query)
    for coll, sc in sorted(scores.items(), key=lambda x: -x[1]):
        print(f"  {coll:<30} confidence={sc:.4f}")

    route = route_collections(query)
    print(f"\n  Route: mode={route.mode}, collections={route.collections}")
    print(f"  Chroma where : {route.where}")
    print(f"  Quotas       : {route.quotas}")

    # ── STAGE 3-6 : full retrieval ───────────────────────────────
    print(f"\n{SEP}")
    print("  STAGES 3-6: VECTOR SEARCH + RERANK + DIVERSITY + COVERAGE")
    print(SEP)
    from rag.indexer import load_vectorstore
    from rag.retriever import get_retrieval_metadata

    vs = load_vectorstore()
    t0 = time.time()
    meta = get_retrieval_metadata(query, vs)
    elapsed = time.time() - t0

    print(f"\n  Retrieval took {elapsed:.2f}s")
    print(f"  primary_module     : {meta.get('primary_module')}")
    print(f"  primary_collection : {meta.get('primary_collection')}")
    print(f"  primary_score      : {meta.get('primary_score')}")
    print(f"  collection_filter  : {meta.get('collection_filter')}")
    print(f"  routing            : {meta.get('routing')}")
    print(f"  required_params    : {meta.get('required_params')}")
    print(f"  chunk_type_counts  : {meta.get('chunk_type_counts')}")
    print(f"  module_candidates  : {meta.get('module_candidates')}")

    print("\n  RANKED MODULES:")
    for rm in meta.get("ranked_modules") or []:
        print(
            f"    #{rm['rank']}  {rm['module']:<50} "
            f"top_score={rm['top_score']} chunks={rm['chunk_hits']}"
        )

    print(f"\n  ALL DOCS RETURNED ({len(meta['docs'])}):")
    for i, (doc, score) in enumerate(zip(meta["docs"], meta["scores"]), 1):
        md = doc.metadata or {}
        preview = doc.page_content[:120].replace("\n", " ")
        print(
            f"    {i}. [{score:.3f}] {md.get('module','?'):<45} "
            f"ctype={md.get('chunk_type','?'):<18} "
            f"coll={md.get('collection','?')}"
        )
        print(f"       preview: {preview}...")

    # ── RAW SIMILARITY (pre-rerank) ─────────────────────────────
    print("\n  RAW TOP-20 SIMILARITY HITS (Chroma, same filter):")
    raw_results = vs.similarity_search_with_relevance_scores(
        query=query, k=20, filter=route.where,
    )
    for i, (doc, score) in enumerate(raw_results, 1):
        md = doc.metadata or {}
        mod = md.get("module", "?")
        ctype = md.get("chunk_type", "?")
        exi = md.get("example_index", "-")
        print(f"    {i:2d}. [{score:.4f}] {mod:<45} ctype={ctype:<18} ex_idx={exi}")

    # ── GENERATION ───────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  GENERATION: PLAYBOOK VIA AGENT LLM")
    print(SEP)
    from agent.playbook_generator import generate_playbook_from_retrieval

    t0 = time.time()
    output_path, yaml_content = generate_playbook_from_retrieval(query, meta)
    gen_elapsed = time.time() - t0

    print(f"\n  Generation took {gen_elapsed:.2f}s")
    print(f"  Output file: {output_path}")

    # ── VALIDATION ───────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  VALIDATION")
    print(SEP)
    try:
        from pipeline.validator import validate_playbook

        kb_modules = {}
        try:
            from agent.tools import _get_kb_modules
            kb_modules = _get_kb_modules() or {}
        except Exception:
            pass

        vr = validate_playbook(yaml_content, kb_modules=kb_modules)
        print(f"  valid          : {vr.valid}")
        print(f"  checks_passed  : {vr.checks_passed}")
        print(f"  checks_total   : {vr.checks_total}")
        print(f"  detected_module: {vr.detected_module}")
        for msg in vr.messages:
            status = "PASS" if msg.get("passed") else "FAIL"
            print(f"  {status} {msg.get('check')}: {msg.get('message','')}")
    except Exception as e:
        print(f"  (validator import failed: {e})")

    # ── FINAL PLAYBOOK ──────────────────────────────────────────
    print(f"\n{SEP}")
    print("  GENERATED PLAYBOOK")
    print(SEP)
    print(yaml_content)

    # ── SUMMARY ─────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  SUMMARY")
    print(SEP)
    print(f"  Query           : {query}")
    print(f"  Route           : {route.mode} -> {route.collections}")
    print(f"  Primary module  : {meta.get('primary_module')}")
    print(f"  Retrieval time  : {elapsed:.2f}s")
    print(f"  Generation time : {gen_elapsed:.2f}s")
    print(SEP)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Create an EC2 instance t3.micro in AWS using variables for subnet, security group, and AMI."
    )
    trace(q)
