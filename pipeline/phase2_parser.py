# pipeline/phase2_parser.py
# Replace the whole file with this content.

import os
import json
import re
import sys
from datetime import datetime
from bs4 import BeautifulSoup

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

INPUT_DIR = "data/raw_html"
OUTPUT_DIR = "data/parsed"
REPORT_FILE = "reports/parse_report.json"

def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())

def collection_ns_to_dotted(ns: str) -> str:
    # Example: kubernetes_core -> kubernetes.core
    return ns.replace("_", ".", 1) if "_" in ns else ns

def extract_module_name(soup, slug, collection_name):
    h1 = soup.find("h1")
    if h1:
        text = clean_text(h1.get_text())
        text = re.sub(r"\s*(module\s*[–—-].*|module\s*$)", "", text, flags=re.IGNORECASE).strip()
        text = text.replace("\uf0c1", "").strip()
        if text:
            return text
    return f"{collection_name}.{slug.replace('_module', '')}"

def extract_short_description(soup):
    for section in soup.find_all("section"):
        sid = section.get("id", "")
        if "synopsis" in sid:
            li = section.find("li")
            if li:
                text = clean_text(li.get_text())
                if text:
                    return text
            p = section.find("p")
            if p:
                text = clean_text(p.get_text())
                if text:
                    return text
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return clean_text(meta["content"])
    return ""

def extract_parameters(soup):
    parameters = []
    seen = set()
    param_section = None

    for section in soup.find_all("section"):
        if section.get("id", "").lower() == "parameters":
            param_section = section
            break

    if not param_section:
        for h2 in soup.find_all("h2"):
            if "parameter" in h2.get_text().lower():
                param_section = h2.find_parent("section") or h2
                break
    if not param_section:
        return parameters

    table = param_section.find("table", class_="ansible-option-table") or param_section.find("table")
    if not table:
        return parameters

    body = table.find("tbody")
    rows = body.find_all("tr", recursive=False) if body else table.find_all("tr", recursive=False)

    for row in rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        left_cell, right_cell = cells[0], cells[1]
        # Skip nested sub-options (e.g., parent.child fields)
        if left_cell.find("div", class_="ansible-option-indent"):
            continue

        name_tag = left_cell.find("p", class_="ansible-option-title")
        if not name_tag:
            continue
        strong = name_tag.find("strong")
        name = clean_text(strong.get_text() if strong else name_tag.get_text())
        if not name or name in seen:
            continue
        seen.add(name)

        type_tag = left_cell.find("span", class_="ansible-option-type")
        param_type = clean_text(type_tag.get_text()) if type_tag else ""

        required_tag = left_cell.find("span", class_=re.compile("required"))
        required = required_tag is not None
        type_line = left_cell.find("p", class_="ansible-option-type-line")
        if type_line and "required" in type_line.get_text().lower():
            required = True

        aliases = []
        aliases_tag = left_cell.find("span", class_=re.compile("aliases|alias"))
        if aliases_tag:
            alias_text = re.sub(r"aliases?:", "", clean_text(aliases_tag.get_text()), flags=re.IGNORECASE).strip()
            aliases = [a.strip() for a in alias_text.split(",") if a.strip()]

        desc_parts = [clean_text(p.get_text()) for p in right_cell.find_all("p") if clean_text(p.get_text())]
        description = " ".join(desc_parts)

        default = ""
        default_match = re.search(r"[Dd]efault[:\s]+[\"']?([^\s\"'<]+)[\"']?", description)
        if default_match:
            default = default_match.group(1)

        choices = []
        ul = right_cell.find("ul")
        if ul:
            choices = [clean_text(li.get_text()) for li in ul.find_all("li") if clean_text(li.get_text())]
        else:
            choices_match = re.search(r"[Cc]hoices?[:\s]+((?:[\"'\w]+[\s,]*)+)", description)
            if choices_match:
                raw_c = choices_match.group(1)
                choices = [c.strip().strip("\"'") for c in re.split(r"[\s,]+", raw_c) if c.strip().strip("\"'")]

        parameters.append({
            "name": name,
            "aliases": aliases,
            "type": param_type,
            "required": required,
            "default": default,
            "choices": choices,
            "description": description,
        })
    return parameters

def extract_return_values(soup):
    return_values = []
    seen = set()
    rv_section = None

    for section in soup.find_all("section"):
        if "return" in section.get("id", "").lower():
            rv_section = section
            break
    if not rv_section:
        return return_values

    table = rv_section.find("table", class_="ansible-option-table") or rv_section.find("table")
    if not table:
        return return_values

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        left_cell, right_cell = cells[0], cells[1]

        name_tag = left_cell.find("p", class_="ansible-option-title")
        if not name_tag:
            continue
        strong = name_tag.find("strong")
        name = clean_text(strong.get_text() if strong else name_tag.get_text())
        if not name or name in seen:
            continue
        seen.add(name)

        type_tag = left_cell.find("span", class_="ansible-option-type")
        rv_type = clean_text(type_tag.get_text()) if type_tag else ""
        desc_parts = [clean_text(p.get_text()) for p in right_cell.find_all("p") if clean_text(p.get_text())]
        description = " ".join(desc_parts)

        returned = ""
        ret_match = re.search(r"[Rr]eturned?:\s*(\w+)", description)
        if ret_match:
            returned = ret_match.group(1)

        return_values.append({
            "name": name,
            "type": rv_type,
            "description": description,
            "returned": returned,
        })
    return return_values

def extract_examples(soup):
    examples = []
    examples_section = None

    for section in soup.find_all("section"):
        if "example" in section.get("id", "").lower():
            examples_section = section
            break
    if not examples_section:
        for h2 in soup.find_all("h2"):
            if "example" in h2.get_text().lower():
                examples_section = h2.find_parent("section") or h2
                break
    if not examples_section:
        return examples

    for pre in examples_section.find_all("pre"):
        code = pre.find("code") or pre
        text = code.get_text().strip()
        if text:
            examples.append(text)
    return examples

def parse_module_html(filepath, slug, collection_ns):
    with open(filepath, "r", encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    collection_name = collection_ns_to_dotted(collection_ns)

    module_name = extract_module_name(soup, slug, collection_name)
    module_short = module_name.split(".")[-1] if module_name else slug.replace("_module", "")

    return {
        "collection_ns": collection_ns,
        "collection": collection_name,
        "module": module_name,
        "slug": slug,
        "description": extract_short_description(soup),
        "parameters": extract_parameters(soup),
        "examples": extract_examples(soup),
        "return_values": extract_return_values(soup),
        "source_url": (
            "https://docs.ansible.com/ansible/latest/"
            f"collections/{collection_name.replace('.', '/')}/{module_short}_module.html"
        ),
        "parsed_at": datetime.now().isoformat(),
    }

def iter_collection_html_files(input_dir):
    # Expects Phase1 layout: data/raw_html/<collection_ns>/*.html
    for collection_ns in sorted(os.listdir(input_dir)):
        coll_path = os.path.join(input_dir, collection_ns)
        if not os.path.isdir(coll_path):
            continue
        files = sorted([f for f in os.listdir(coll_path) if f.endswith(".html")])
        for filename in files:
            yield collection_ns, filename, os.path.join(coll_path, filename)

def main():
    print("=" * 60)
    print("  Ansible Multi-Collection — Phase 2 Parser")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs("reports", exist_ok=True)

    if not os.path.exists(INPUT_DIR):
        print(f"\n[ERROR] No input dir: '{INPUT_DIR}/'")
        return

    items = list(iter_collection_html_files(INPUT_DIR))
    if not items:
        print(f"\n[ERROR] No HTML files found under '{INPUT_DIR}/<collection_ns>/'")
        return

    print(f"\n  Found {len(items)} HTML files across collections.\n")

    report = {"parse_date": datetime.now().isoformat(), "modules": []}
    ok_count = 0
    warn_count = 0

    for i, (collection_ns, filename, filepath) in enumerate(items, start=1):
        slug = filename.replace(".html", "")
        try:
            data = parse_module_html(filepath, slug, collection_ns)

            coll_out_dir = os.path.join(OUTPUT_DIR, collection_ns)
            os.makedirs(coll_out_dir, exist_ok=True)
            out_path = os.path.join(coll_out_dir, f"{slug}.json")

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            param_count = len(data["parameters"])
            example_count = len(data["examples"])
            req_params = [p["name"] for p in data["parameters"] if p["required"]]

            if param_count == 0:
                status, flag = "warn_no_params", "[WARN]"
                warn_count += 1
            else:
                status, flag = "ok", "[OK]  "
                ok_count += 1

            print(f"  [{i:04d}/{len(items):04d}] {flag} {collection_ns}/{slug}")
            report["modules"].append({
                "collection_ns": collection_ns,
                "collection": data["collection"],
                "slug": slug,
                "module": data["module"],
                "status": status,
                "param_count": param_count,
                "example_count": example_count,
                "required_params": req_params,
                "output_file": out_path,
            })
        except Exception as e:
            warn_count += 1
            print(f"  [{i:04d}/{len(items):04d}] [ERR]  {collection_ns}/{slug} — {e}")
            report["modules"].append({
                "collection_ns": collection_ns,
                "slug": slug,
                "status": "error",
                "detail": str(e),
            })

    report["summary"] = {"total": len(items), "ok": ok_count, "warnings": warn_count}
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("  PARSING COMPLETE")
    print(f"  OK       : {ok_count}")
    print(f"  Warnings : {warn_count}")
    print(f"  Parsed JSON -> {OUTPUT_DIR}/<collection_ns>/")
    print(f"  Report     -> {REPORT_FILE}")
    print("  Next step  -> run phase3_structurer.py")
    print("=" * 60)

if __name__ == "__main__":
    main()
