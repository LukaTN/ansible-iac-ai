if True:
    """
    =============================================================
    AnsibleAI RAG — Step 1 : Ingestion
    Loads parsed module JSONs into LangChain Documents
    with rich metadata for filtering and retrieval.
    =============================================================
    """

    import json
    import os

    from langchain_core.documents import Document

    # backend/rag/ingestion.py → repository root (not backend/, which
    # made `data/parsed` resolve to a missing path in the container).
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    PARSED_DIR  = os.path.join(_PROJECT_ROOT, "data", "parsed")
    KB_FILE     = os.path.join(_PROJECT_ROOT, "data", "knowledge_base.json")

    CHUNK_SCHEMA_VERSION = "v5_overview_tasks"
    CHUNK_SIZE  = 1100
    CHUNK_OVERLAP = 180
    SKIP_PARAMS = {     # params to exclude from context (noise)
        "api_key", "ca_cert", "client_cert", "client_key",
        "proxy", "proxy_headers", "basic_auth", "validate_certs",
        "host", "username", "password", "no_proxy", "user_agent",
    }


    def _split_with_overlap(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            part = text[start:end]
            chunks.append(part)
            if end >= len(text):
                break
            start = max(0, end - overlap)
        return chunks


    def _effective_parsed_dir(parsed_dir: str | None = None) -> str:
        """
        Resolve parsed data root in priority order:
        1) explicit function arg
        2) RAG_PARSED_DIR env var
        3) default PARSED_DIR
        """
        explicit = (parsed_dir or "").strip()
        if explicit:
            return explicit
        env_dir = (os.getenv("RAG_PARSED_DIR") or "").strip()
        if env_dir:
            return env_dir
        return PARSED_DIR


    # ─────────────────────────────────────────────
    #  DOCUMENT BUILDERS
    #  Each function returns a list of LangChain Documents
    #  from a single module dict.
    # ─────────────────────────────────────────────

    def _base_meta(module: dict, collection: str, chunk_type: str) -> dict:
        """Build shared metadata dict for a module chunk."""
        return {
            "collection" : collection,
            "module"     : module.get("module", ""),
            "slug"       : module.get("slug", ""),
            "category"   : module.get("category", "general"),
            "source_url" : module.get("source_url", ""),
            "chunk_type" : chunk_type,
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        }


    def _required_param_names(module: dict) -> list[str]:
        """Required parameter names, read from the schema the parser actually writes.

        Parsed modules carry ``parameters: [{name, required, ...}]``. There is no
        top-level ``required_params`` key — reading one silently produced an empty
        list for all 1222 modules, so every overview chunk claimed the module had
        no required parameters.
        """
        return [
            p["name"]
            for p in module.get("parameters", [])
            if p.get("required") and p.get("name") and p["name"] not in SKIP_PARAMS
        ]


    def build_overview_doc(module: dict, collection: str) -> list[Document]:
        """
        Chunk 1: Module overview — what this module is for.

        Only fields with real content are emitted. Constant or empty lines
        ("Category: general" on every module, an empty "Use this module to:")
        are pure boilerplate: they made overview chunks near-identical to each
        other in embedding space, which is the opposite of what this chunk is for.
        """
        mod   = module.get("module", "")
        desc  = module.get("description", "")
        kws   = " | ".join(module.get("task_keywords", []))
        cat   = module.get("category", "")
        req   = ", ".join(_required_param_names(module))
        # The short name split into words is often the clearest statement of
        # purpose the module has ("ec2_instance" -> "ec2 instance"), and it lets
        # a query say "instance" without naming the module.
        name_words = mod.split(".")[-1].replace("_", " ") if mod else ""

        lines = [
            f"Module: {mod}",
            f"Collection: {collection}",
            f"Name: {name_words}",
            f"Description: {desc}",
        ]
        if cat and cat != "general":
            lines.append(f"Category: {cat}")
        if kws:
            lines.append(f"Use this module to: {kws}")
        # Example task names are the only natural-language *task statements*
        # in the docs — "Manages apt packages" never embeds near "install a
        # package", but "Install a list of packages" does. Folded into the
        # overview (rather than a separate chunk) so the module gains task
        # vocabulary without adding 1,200 generic-verb chunks that compete
        # for pack slots — benchmarked: a separate purpose chunk cost 5.5
        # points of top1 by matching every action query.
        tasks = _extract_task_names(module, cap=6)
        if tasks:
            lines.append("Typical tasks:")
            lines.extend(f"- {t}" for t in tasks)
        lines.append(f"Required parameters: {req or 'none'}")

        text = "\n".join(lines)
        parts = _split_with_overlap(text)
        docs = []
        for i, part in enumerate(parts):
            meta = _base_meta(module, collection, "overview")
            meta["overview_part"] = str(i)
            docs.append(Document(page_content=part, metadata=meta))
        return docs


    # Task names inside EXAMPLES blocks ("- name: Install apache httpd").
    # These are the only natural-language *task statements* in the docs — the
    # description says what the module manages, but the task names say what a
    # user actually asks for, in the user's own phrasing.
    import re as _re_mod

    _TASK_NAME_RE = _re_mod.compile(r"-\s*name:\s*(.+)")


    def _extract_task_names(module: dict, cap: int = 10) -> list[str]:
        """Unique example task names, order-preserved, trimmed of quotes/jinja."""
        names: list[str] = []
        seen: set[str] = set()
        for ex in module.get("examples", []):
            for m in _TASK_NAME_RE.finditer(ex or ""):
                name = m.group(1).strip().strip("\"'")
                # Drop jinja-heavy or degenerate names — they embed as noise.
                if not name or "{{" in name or len(name) < 8:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                names.append(name[:120])
                if len(names) >= cap:
                    return names
        return names


    def build_required_params_doc(module: dict, collection: str) -> list[Document]:
        """
        Chunk 2: Required parameters (most important for generation quality).
        """
        params   = module.get("parameters", [])
        required = [p for p in params if p.get("required") and p["name"] not in SKIP_PARAMS]
        if not required:
            return []

        mod  = module.get("module", "")
        text = f"Module {mod} — REQUIRED parameters (must always be provided):\n"
        for p in required:
            text += (
                f"- {p['name']} ({p.get('type', 'any')}): "
                f"{p.get('description', '')[:150]}\n"
            )

        parts = _split_with_overlap(text)
        docs = []
        for i, part in enumerate(parts):
            meta = _base_meta(module, collection, "required_params")
            meta["required_params_list"] = ",".join(p["name"] for p in required)
            meta["required_part"] = str(i)
            docs.append(Document(page_content=part, metadata=meta))
        return docs


    def build_optional_params_docs(module: dict, collection: str) -> list[Document]:
        """
        Chunk 3+: Optional parameters grouped by 5.
        """
        params   = module.get("parameters", [])
        optional = [
            p for p in params
            if not p.get("required") and p["name"] not in SKIP_PARAMS
        ]
        docs = []
        mod  = module.get("module", "")

        for i in range(0, len(optional), 5):
            group = optional[i:i + 5]
            text  = f"Module {mod} — optional parameters (group {i // 5 + 1}):\n"
            for p in group:
                default = f" (default: {p['default']})" if p.get("default") else ""
                choices = f" choices: {p['choices']}" if p.get("choices") else ""
                text += (
                    f"- {p['name']} ({p.get('type', 'any')}){default}{choices}: "
                    f"{p.get('description', '')[:100]}\n"
                )
            meta = _base_meta(module, collection, "optional_params")
            meta["optional_group_index"] = str(i // 5)
            docs.append(Document(page_content=text, metadata=meta))
        return docs


    def build_example_docs(module: dict, collection: str) -> list[Document]:
        """
        Chunk 4+: YAML examples (most useful for generation).
        """
        docs = []
        mod  = module.get("module", "")

        for j, ex in enumerate(module.get("examples", [])[:3]):
            full = f"Example usage of {mod} (example {j + 1}):\n{ex}"
            parts = _split_with_overlap(full)
            for k, part in enumerate(parts):
                meta = _base_meta(module, collection, "example")
                meta["example_index"] = str(j)
                meta["example_part"] = str(k)
                docs.append(Document(page_content=part, metadata=meta))
        return docs


    def module_to_documents(module: dict, collection: str) -> list[Document]:
        """Convert a single module dict into all its LangChain Documents."""
        docs = []

        docs.extend(build_overview_doc(module, collection))
        docs.extend(build_required_params_doc(module, collection))

        docs.extend(build_optional_params_docs(module, collection))
        docs.extend(build_example_docs(module, collection))

        return docs


    # ─────────────────────────────────────────────
    #  COLLECTION LOADERS
    # ─────────────────────────────────────────────

    def load_collection(collection_name: str, parsed_dir: str | None = None) -> list[Document]:
        """
        Load all modules for a collection and convert to Documents.
        Supports both directory-based (parsed/) and legacy KB format.
        """
        ns = collection_name.replace(".", "_")
        parsed_root = _effective_parsed_dir(parsed_dir)
        collection_dir = os.path.join(parsed_root, ns)
        documents  = []

        # ── Directory-based (new format) ──
        if os.path.exists(collection_dir):
            files = sorted(f for f in os.listdir(collection_dir) if f.endswith(".json"))
            print(f"  Loading {collection_name}: {len(files)} modules from {collection_dir}")
            for fname in files:
                try:
                    with open(os.path.join(collection_dir, fname), encoding="utf-8") as f:
                        module = json.load(f)
                    documents.extend(module_to_documents(module, collection_name))
                except Exception as e:
                    print(f"    [WARN] Could not load {fname}: {e}")

        # ── Legacy KB format (kubernetes.core only) ──
        elif collection_name == "kubernetes.core" and os.path.exists(KB_FILE):
            print(f"  Loading kubernetes.core from {KB_FILE} (legacy)")
            with open(KB_FILE, encoding="utf-8") as f:
                kb = json.load(f)
            for module in kb["modules"].values():
                documents.extend(module_to_documents(module, collection_name))

        else:
            print(f"  [WARN] No data found for {collection_name} — run scraper first.")

        print(f"    → {len(documents)} documents created")
        return documents


    def load_all_collections(parsed_dir: str | None = None) -> list[Document]:
        """
        Auto-detect and load all available collections.
        Returns combined list of Documents.
        """
        parsed_root = _effective_parsed_dir(parsed_dir)
        collections = []

        # Detect from parsed/ subdirectories
        if os.path.exists(parsed_root):
            for d in sorted(os.listdir(parsed_root)):
                full = os.path.join(parsed_root, d)
                if os.path.isdir(full) and any(f.endswith(".json") for f in os.listdir(full)):
                    collections.append(d.replace("_", ".", 1))

        # Always include kubernetes.core via KB if not already found
        if "kubernetes.core" not in collections and os.path.exists(KB_FILE):
            collections.insert(0, "kubernetes.core")

        if not collections:
            raise FileNotFoundError(
                "No parsed data found.\n"
                "→ Run pipeline/phase1_scraper_multi.py + phase2_parser.py first."
            )

        print(f"\n  Collections found: {collections}")
        all_docs = []
        for coll in collections:
            all_docs.extend(load_collection(coll, parsed_dir=parsed_root))

        print(f"\n  Total documents loaded: {len(all_docs)}")
        return all_docs


    if __name__ == "__main__":
        docs = load_all_collections()
        print("\nSample document:")
        print(f"  Content: {docs[0].page_content[:200]}...")
        print(f"  Metadata: {docs[0].metadata}")
