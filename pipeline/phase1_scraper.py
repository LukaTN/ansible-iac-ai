"""
=============================================================
  AI-Powered IaC — Phase 1 : Multi-Collection Scraper
  Supports any Ansible collection on docs.ansible.com
  Output : data/raw_html/<collection>/<module>.html
           reports/scrape_report_<collection>.json
=============================================================
  Usage:
    # Scrape all configured collections
    python pipeline/phase1_scraper_multi.py

    # Scrape a single collection
    python pipeline/phase1_scraper_multi.py --collection kubernetes.core

    # Add a custom collection
    python pipeline/phase1_scraper_multi.py --collection amazon.aws
=============================================================
"""

import os
import sys
import json
import time
import argparse
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─────────────────────────────────────────────
#  COLLECTION REGISTRY
#  Add any Ansible collection here
# ─────────────────────────────────────────────

COLLECTIONS = {

    "kubernetes.core": {
        "index_url": "https://docs.ansible.com/ansible/latest/collections/kubernetes/core/index.html",
        "base_url":  "https://docs.ansible.com/ansible/latest/collections/kubernetes/core/",
        "module_suffix": "_module.html",
        "description": "Kubernetes orchestration modules",
    },

    "amazon.aws": {
        "index_url": "https://docs.ansible.com/ansible/latest/collections/amazon/aws/index.html",
        "base_url":  "https://docs.ansible.com/ansible/latest/collections/amazon/aws/",
        "module_suffix": "_module.html",
        "description": "Amazon Web Services modules",
    },

    "azure.azcollection": {
        "index_url": "https://docs.ansible.com/ansible/latest/collections/azure/azcollection/index.html",
        "base_url":  "https://docs.ansible.com/ansible/latest/collections/azure/azcollection/",
        "module_suffix": "_module.html",
        "description": "Microsoft Azure modules",
    },

    "community.general": {
        "index_url": "https://docs.ansible.com/ansible/latest/collections/community/general/index.html",
        "base_url":  "https://docs.ansible.com/ansible/latest/collections/community/general/",
        "module_suffix": "_module.html",
        "description": "Community general-purpose modules (400+)",
    },

    "ansible.builtin": {
        "index_url": "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/index.html",
        "base_url":  "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/",
        "module_suffix": "_module.html",
        "description": "Ansible built-in modules",
    },
}

HEADERS = {
    "User-Agent": "AnsibleAI-Scraper/2.0 (PFE Research Project)"
}
DELAY      = 0.5   # seconds between requests (be polite)
TIMEOUT    = 30
OUTPUT_DIR = "data/raw_html"
REPORT_DIR = "reports"


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def get_module_links(index_url: str, base_url: str, module_suffix: str) -> list[dict]:
    """
    Scrape the collection index page and return all module links.
    Returns list of {"slug": "k8s_module", "url": "https://..."}
    """
    print(f"  Fetching index: {index_url}")
    resp = requests.get(index_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    modules = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]

        # Resolve relative URLs
        if href.startswith("http"):
            full_url = href
        else:
            full_url = urljoin(base_url, href)

        # Remove query/fragment to avoid bad slugs like "..._module#anchor"
        normalized_url = full_url.split("#", 1)[0].split("?", 1)[0]

        # Only keep module pages (ending with _module.html)
        if module_suffix in normalized_url and normalized_url not in seen:
            seen.add(normalized_url)
            # Extract slug from URL
            slug = normalized_url.rstrip("/").split("/")[-1].replace(".html", "")
            modules.append({"slug": slug, "url": normalized_url})

    return modules


def scrape_module(module: dict, output_dir: str) -> dict:
    """Download and save a single module page. Returns result dict."""
    slug     = module["slug"]
    url      = module["url"]
    filepath = os.path.join(output_dir, f"{slug}.html")

    # Skip if already scraped
    if os.path.exists(filepath):
        size = os.path.getsize(filepath)
        if size > 1000:
            return {"slug": slug, "url": url, "status": "skipped", "size": size}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(resp.text)

        size = len(resp.text)
        print(f"    ✓ {slug:<50} ({size:,} chars)")
        return {"slug": slug, "url": url, "status": "ok", "size": size}

    except Exception as e:
        print(f"    ✗ {slug:<50} ERROR: {e}")
        return {"slug": slug, "url": url, "status": "error", "error": str(e)}


# ─────────────────────────────────────────────
#  MAIN SCRAPER
# ─────────────────────────────────────────────

def scrape_collection(collection_name: str) -> dict:
    """Scrape all modules for a single collection."""

    if collection_name not in COLLECTIONS:
        raise ValueError(f"Unknown collection: {collection_name}\n"
                         f"Available: {list(COLLECTIONS.keys())}")

    cfg = COLLECTIONS[collection_name]
    ns  = collection_name.replace(".", "_")   # kubernetes_core

    print(f"\n{'='*60}")
    print(f"  Scraping collection: {collection_name}")
    print(f"  Description: {cfg['description']}")
    print(f"{'='*60}")

    # Create output directory
    coll_dir = os.path.join(OUTPUT_DIR, ns)
    os.makedirs(coll_dir, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    # Discover modules
    print(f"\n  Discovering modules...")
    modules = get_module_links(
        cfg["index_url"],
        cfg["base_url"],
        cfg["module_suffix"]
    )
    print(f"  Found {len(modules)} modules.\n")

    if not modules:
        print("  [WARNING] No modules found — check the index URL.")
        return {"collection": collection_name, "total": 0, "results": []}

    # Scrape each module
    results = []
    for i, module in enumerate(modules, 1):
        print(f"  [{i:>3}/{len(modules)}] ", end="")
        result = scrape_module(module, coll_dir)
        results.append(result)
        if result["status"] == "ok":
            time.sleep(DELAY)

    # Summary
    ok      = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errors  = sum(1 for r in results if r["status"] == "error")

    report = {
        "collection"  : collection_name,
        "description" : cfg["description"],
        "index_url"   : cfg["index_url"],
        "scraped_at"  : datetime.now().isoformat(),
        "total"       : len(modules),
        "ok"          : ok,
        "skipped"     : skipped,
        "errors"      : errors,
        "output_dir"  : coll_dir,
        "results"     : results,
    }

    report_path = os.path.join(REPORT_DIR, f"scrape_report_{ns}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n  ─────────────────────────────────────────")
    print(f"  Collection : {collection_name}")
    print(f"  Total      : {len(modules)}")
    print(f"  ✓ OK       : {ok}")
    print(f"  ⏭ Skipped  : {skipped}")
    print(f"  ✗ Errors   : {errors}")
    print(f"  Report     : {report_path}")

    return report


def scrape_all():
    """Scrape all registered collections."""
    print(f"\n{'='*60}")
    print(f"  AnsibleAI — Multi-Collection Scraper")
    print(f"  Collections: {len(COLLECTIONS)}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    global_report = []
    for coll_name in COLLECTIONS:
        try:
            report = scrape_collection(coll_name)
            global_report.append(report)
        except Exception as e:
            print(f"\n  [ERROR] {coll_name}: {e}")
            global_report.append({"collection": coll_name, "error": str(e)})

    # Global summary
    print(f"\n{'='*60}")
    print(f"  GLOBAL SUMMARY")
    total_modules = sum(r.get("total", 0) for r in global_report)
    total_ok      = sum(r.get("ok", 0) + r.get("skipped", 0) for r in global_report)
    print(f"  Collections scraped : {len(global_report)}")
    print(f"  Total modules       : {total_modules}")
    print(f"  Successfully saved  : {total_ok}")
    print(f"{'='*60}")

    with open(os.path.join(REPORT_DIR, "scrape_report_global.json"), "w") as f:
        json.dump(global_report, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AnsibleAI Multi-Collection Scraper")
    parser.add_argument("--collection", type=str, default=None,
                        help=f"Collection to scrape. Options: {list(COLLECTIONS.keys())}")
    parser.add_argument("--list", action="store_true",
                        help="List all available collections")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable collections:")
        for name, cfg in COLLECTIONS.items():
            print(f"  {name:<30} — {cfg['description']}")
        sys.exit(0)

    if args.collection:
        scrape_collection(args.collection)
    else:
        scrape_all()