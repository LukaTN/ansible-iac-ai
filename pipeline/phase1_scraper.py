"""
=============================================================
  AI-Powered IaC — Phase 1 : Scraper
  Target : Ansible kubernetes.core collection
  URL    : https://docs.ansible.com/ansible/latest/collections/kubernetes/core/
=============================================================
  Usage (PyCharm terminal):
      pip install requests beautifulsoup4
      Run phase1_scraper.py

  Output:
      data/raw_html/                 -> one .html file per module page
      reports/scrape_report.json     -> summary of what was scraped
=============================================================
"""

import os
import json
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

BASE_URL    = "https://docs.ansible.com/ansible/latest/collections/kubernetes/core/"
INDEX_URL   = BASE_URL + "index.html"
OUTPUT_DIR  = "data/raw_html"
REPORT_DIR  = "reports"
REPORT_FILE = "reports/scrape_report.json"
DELAY_SECONDS = 1.0   # polite delay between requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AnsibleDocScraper/1.0; "
        "PFE-IaC-AI research project)"
    )
}


# ─────────────────────────────────────────────
#  KNOWN MODULES — fallback if index parse fails
#  Source: kubernetes.core collection
# ─────────────────────────────────────────────

KNOWN_MODULES = [
    "helm_module",
    "helm_info_module",
    "helm_plugin_module",
    "helm_plugin_info_module",
    "helm_repository_module",
    "helm_template_module",
    "k8s_module",
    "k8s_cluster_info_module",
    "k8s_cp_module",
    "k8s_drain_module",
    "k8s_exec_module",
    "k8s_info_module",
    "k8s_json_patch_module",
    "k8s_log_module",
    "k8s_rollback_module",
    "k8s_scale_module",
    "k8s_service_module",
    "k8s_taint_module",
]


# ─────────────────────────────────────────────
#  STEP 1 — Discover module URLs from index page
# ─────────────────────────────────────────────

def get_module_urls_from_index(session):
    """
    Fetches the kubernetes.core index page and extracts
    all links ending in '_module.html'.
    Returns a dict: { module_slug: full_url }
    """
    print(f"\n[1/3] Fetching index page: {INDEX_URL}")

    try:
        resp = session.get(INDEX_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()

    except requests.RequestException as e:
        print(f"  [WARN] Could not fetch index: {e}")
        print("  [INFO] Falling back to KNOWN_MODULES list.")
        return {slug: BASE_URL + slug + ".html" for slug in KNOWN_MODULES}

    soup = BeautifulSoup(resp.text, "html.parser")
    module_urls = {}

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        # Module pages end with _module.html
        if href.endswith("_module.html"):
            if href.startswith("http"):
                full_url = href
            else:
                full_url = BASE_URL + href.lstrip("./")
            slug = href.split("/")[-1].replace(".html", "")
            module_urls[slug] = full_url

    if module_urls:
        print(f"  [OK] Found {len(module_urls)} module pages in index.")
    else:
        print("  [WARN] No modules found in index. Using fallback list.")
        module_urls = {slug: BASE_URL + slug + ".html" for slug in KNOWN_MODULES}

    return module_urls


# ─────────────────────────────────────────────
#  STEP 2 — Download and save each module page
# ─────────────────────────────────────────────

def scrape_module_page(session, slug, url, output_dir):
    """
    Downloads one module page and saves the raw HTML.
    Returns a status dict for the report.
    """
    filepath = os.path.join(output_dir, slug + ".html")

    # Skip if already downloaded (resume support)
    if os.path.exists(filepath):
        print(f"  [SKIP] {slug} — already downloaded.")
        return {"slug": slug, "url": url, "status": "skipped", "file": filepath}

    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(resp.text)

        print(f"  [OK]   {slug}  ({len(resp.text):,} chars)")
        return {
            "slug"  : slug,
            "url"   : url,
            "status": "ok",
            "size"  : len(resp.text),
            "file"  : filepath,
        }

    except requests.HTTPError as e:
        print(f"  [ERR]  {slug} — HTTP {e.response.status_code}")
        return {"slug": slug, "url": url, "status": f"http_{e.response.status_code}"}

    except requests.RequestException as e:
        print(f"  [ERR]  {slug} — {e}")
        return {"slug": slug, "url": url, "status": "error", "detail": str(e)}


# ─────────────────────────────────────────────
#  STEP 3 — Sanity check on saved HTML files
# ─────────────────────────────────────────────

def verify_html_files(output_dir):
    """
    Checks that each saved HTML contains expected Ansible doc sections.
    Prints a warning for any file that looks incomplete.
    """
    print(f"\n[3/3] Verifying downloaded files in '{output_dir}/' ...")

    required_sections = ["Parameters", "Examples"]
    ok_count   = 0
    warn_count = 0

    for filename in sorted(os.listdir(output_dir)):
        if not filename.endswith(".html"):
            continue

        filepath = os.path.join(output_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        missing = [s for s in required_sections if s not in content]
        if missing:
            print(f"  [WARN] {filename} — missing sections: {missing}")
            warn_count += 1
        else:
            ok_count += 1

    print(f"  {ok_count} files OK  |  {warn_count} files with warnings")
    return ok_count, warn_count


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Ansible kubernetes.core — Phase 1 Scraper")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Create output directories if they don't exist
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    report = {
        "scrape_date": datetime.now().isoformat(),
        "base_url"   : BASE_URL,
        "modules"    : [],
    }

    with requests.Session() as session:

        # Step 1 — Discover module URLs
        module_urls = get_module_urls_from_index(session)

        # Step 2 — Download each module page
        print(f"\n[2/3] Downloading {len(module_urls)} module pages ...")
        for i, (slug, url) in enumerate(sorted(module_urls.items()), start=1):
            print(f"  [{i:02d}/{len(module_urls):02d}] ", end="")
            result = scrape_module_page(session, slug, url, OUTPUT_DIR)
            report["modules"].append(result)
            if result["status"] == "ok":
                time.sleep(DELAY_SECONDS)   # polite delay

    # Step 3 — Verify files
    ok_count, warn_count = verify_html_files(OUTPUT_DIR)

    # Save report
    report["summary"] = {
        "total"         : len(report["modules"]),
        "ok"            : sum(1 for m in report["modules"] if m["status"] == "ok"),
        "skipped"       : sum(1 for m in report["modules"] if m["status"] == "skipped"),
        "errors"        : sum(1 for m in report["modules"] if m["status"] not in ("ok", "skipped")),
        "verified_ok"   : ok_count,
        "verified_warn" : warn_count,
    }

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    s = report["summary"]
    print(f"""
{'=' * 60}
  SCRAPING COMPLETE
  Downloaded : {s['ok']}
  Skipped    : {s['skipped']}
  Errors     : {s['errors']}

  Raw HTML   → {OUTPUT_DIR}/
  Report     → {REPORT_FILE}

  Next step  → run phase2_parser.py
{'=' * 60}
""")


if __name__ == "__main__":
    main()
