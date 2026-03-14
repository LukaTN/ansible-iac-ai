"""
=============================================================
  AI-Powered IaC — Phase 2 : Parser (v4 - correct HTML structure)
  Input  : data/raw_html/*.html  (Phase 1 output)
  Output : data/parsed/*.json    (one JSON per module)
=============================================================
  Ansible docs use <table class="ansible-option-table"> for parameters.
  Each row has:
    - <p class="ansible-option-title"><strong> → parameter name
    - <span class="ansible-option-type">       → type
    - <span class="ansible-option-required">   → required flag
    - 2nd <td>                                 → description + choices/default
=============================================================
"""

import os
import json
import re
from datetime import datetime
from bs4 import BeautifulSoup

# Always run from project root
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

INPUT_DIR   = "data/raw_html"
OUTPUT_DIR  = "data/parsed"
REPORT_FILE = "reports/parse_report.json"


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


# ─────────────────────────────────────────────
#  EXTRACTION FUNCTIONS
# ─────────────────────────────────────────────

def extract_module_name(soup, slug):
    h1 = soup.find("h1")
    if h1:
        text = clean_text(h1.get_text())
        # Remove ' module – ...' suffix
        text = re.sub(r"\s*(module\s*[–—].*|module\s*$)", "", text, flags=re.IGNORECASE).strip()
        # Remove trailing unicode link icon
        text = text.replace("\uf0c1", "").strip()
        if text:
            return text
    return "kubernetes.core." + slug.replace("_module", "")


def extract_short_description(soup):
    # Look in Synopsis section
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

    # Fallback: meta description
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return clean_text(meta["content"])

    return ""


def extract_parameters(soup):
    """
    Parse parameters from <table class="ansible-option-table">.
    Finds the table inside the Parameters section only.
    """
    parameters = []
    seen = set()

    # Find the Parameters section
    param_section = None
    for section in soup.find_all("section"):
        sid = section.get("id", "").lower()
        if sid == "parameters":
            param_section = section
            break

    if not param_section:
        # Fallback: find by heading
        for h2 in soup.find_all("h2"):
            if "parameter" in h2.get_text().lower():
                param_section = h2.find_parent("section") or h2
                break

    if not param_section:
        return parameters

    # Find the ansible-option-table inside this section
    table = param_section.find("table", class_="ansible-option-table")
    if not table:
        # Try any table in the section
        table = param_section.find("table")

    if not table:
        return parameters

    # Parse each row
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        left_cell  = cells[0]
        right_cell = cells[1]

        # ── Parameter name
        name_tag = left_cell.find("p", class_="ansible-option-title")
        if not name_tag:
            continue
        strong = name_tag.find("strong")
        name = clean_text(strong.get_text() if strong else name_tag.get_text())
        if not name or name in seen:
            continue
        seen.add(name)

        # ── Type
        type_tag = left_cell.find("span", class_="ansible-option-type")
        param_type = clean_text(type_tag.get_text()) if type_tag else ""

        # ── Required
        required_tag = left_cell.find("span", class_=re.compile("required"))
        required = required_tag is not None
        # Also check text "required" in the type line
        type_line = left_cell.find("p", class_="ansible-option-type-line")
        if type_line and "required" in type_line.get_text().lower():
            required = True

        # ── Aliases
        aliases = []
        aliases_tag = left_cell.find("span", class_=re.compile("aliases|alias"))
        if aliases_tag:
            alias_text = clean_text(aliases_tag.get_text())
            alias_text = re.sub(r"aliases?:", "", alias_text, flags=re.IGNORECASE).strip()
            aliases = [a.strip() for a in alias_text.split(",") if a.strip()]

        # ── Description (from right cell)
        desc_parts = []
        for p in right_cell.find_all("p"):
            t = clean_text(p.get_text())
            if t:
                desc_parts.append(t)
        description = " ".join(desc_parts)

        # ── Default value
        default = ""
        default_match = re.search(
            r"[Dd]efault[:\s]+[\"']?([^\s\"'<]+)[\"']?", description
        )
        if default_match:
            default = default_match.group(1)

        # ── Choices
        choices = []
        # Look for list items in right cell (Ansible uses <ul> for choices)
        ul = right_cell.find("ul")
        if ul:
            choices = [
                clean_text(li.get_text())
                for li in ul.find_all("li")
                if clean_text(li.get_text())
            ]
        else:
            choices_match = re.search(
                r"[Cc]hoices?[:\s]+((?:[\"'\w]+[\s,]*)+)", description
            )
            if choices_match:
                raw_c = choices_match.group(1)
                choices = [
                    c.strip().strip("\"'")
                    for c in re.split(r"[\s,]+", raw_c)
                    if c.strip().strip("\"'")
                ]

        parameters.append({
            "name"       : name,
            "aliases"    : aliases,
            "type"       : param_type,
            "required"   : required,
            "default"    : default,
            "choices"    : choices,
            "description": description,
        })

    return parameters


def extract_return_values(soup):
    """
    Parse return values from the Return Values section table.
    Same structure as parameters table.
    """
    return_values = []
    seen = set()

    rv_section = None
    for section in soup.find_all("section"):
        sid = section.get("id", "").lower()
        if "return" in sid:
            rv_section = section
            break

    if not rv_section:
        return return_values

    table = rv_section.find("table", class_="ansible-option-table")
    if not table:
        table = rv_section.find("table")
    if not table:
        return return_values

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        left_cell  = cells[0]
        right_cell = cells[1]

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

        desc_parts = [
            clean_text(p.get_text())
            for p in right_cell.find_all("p")
            if clean_text(p.get_text())
        ]
        description = " ".join(desc_parts)

        # Extract "Returned: always/success/..."
        returned = ""
        ret_match = re.search(r"[Rr]eturned?:\s*(\w+)", description)
        if ret_match:
            returned = ret_match.group(1)

        return_values.append({
            "name"       : name,
            "type"       : rv_type,
            "description": description,
            "returned"   : returned,
        })

    return return_values


def extract_examples(soup):
    """Extract YAML examples from Examples section."""
    examples = []

    examples_section = None
    for section in soup.find_all("section"):
        sid = section.get("id", "").lower()
        if "example" in sid:
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


# ─────────────────────────────────────────────
#  MAIN PARSE FUNCTION
# ─────────────────────────────────────────────

def parse_module_html(filepath, slug):
    with open(filepath, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    return {
        "module"       : extract_module_name(soup, slug),
        "slug"         : slug,
        "description"  : extract_short_description(soup),
        "parameters"   : extract_parameters(soup),
        "examples"     : extract_examples(soup),
        "return_values": extract_return_values(soup),
        "source_url"   : (
            "https://docs.ansible.com/ansible/latest/"
            f"collections/kubernetes/core/{slug}.html"
        ),
        "parsed_at"    : datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Ansible kubernetes.core — Phase 2 Parser (v4)")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    html_files = [f for f in os.listdir(INPUT_DIR) if f.endswith(".html")]

    if not html_files:
        print(f"\n[ERROR] No HTML files found in '{INPUT_DIR}/'")
        print("  → Make sure you ran phase1_scraper.py first.")
        return

    print(f"\n  Found {len(html_files)} HTML files to parse.\n")

    report = {
        "parse_date": datetime.now().isoformat(),
        "modules"   : [],
    }

    ok_count   = 0
    warn_count = 0

    for i, filename in enumerate(sorted(html_files), start=1):
        slug     = filename.replace(".html", "")
        filepath = os.path.join(INPUT_DIR, filename)

        try:
            data = parse_module_html(filepath, slug)

            out_path = os.path.join(OUTPUT_DIR, slug + ".json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            param_count   = len(data["parameters"])
            example_count = len(data["examples"])
            req_params    = [p["name"] for p in data["parameters"] if p["required"]]

            if param_count == 0:
                status     = "warn_no_params"
                warn_count += 1
                flag       = "[WARN]"
            else:
                status   = "ok"
                ok_count += 1
                flag     = "[OK]  "

            print(f"  [{i:02d}/{len(html_files):02d}] {flag} {slug}")
            print(f"           params={param_count}  "
                  f"examples={example_count}  "
                  f"required={req_params}")

            report["modules"].append({
                "slug"           : slug,
                "module"         : data["module"],
                "status"         : status,
                "param_count"    : param_count,
                "example_count"  : example_count,
                "required_params": req_params,
                "output_file"    : out_path,
            })

        except Exception as e:
            warn_count += 1
            print(f"  [{i:02d}/{len(html_files):02d}] [ERR]  {slug} — {e}")
            report["modules"].append({
                "slug"  : slug,
                "status": "error",
                "detail": str(e),
            })

    report["summary"] = {
        "total"   : len(html_files),
        "ok"      : ok_count,
        "warnings": warn_count,
    }

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"""
{'=' * 60}
  PARSING COMPLETE
  OK       : {ok_count}
  Warnings : {warn_count}

  Parsed JSON → {OUTPUT_DIR}/
  Report     → {REPORT_FILE}

  Next step  → run phase3_structurer.py
{'=' * 60}
""")


if __name__ == "__main__":
    main()