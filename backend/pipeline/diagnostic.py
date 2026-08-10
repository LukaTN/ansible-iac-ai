"""
=============================================================
  DIAGNOSTIC — Inspect HTML structure of k8s_exec_module.html
  Run this BEFORE fixing the parser.
=============================================================
"""

import os

from bs4 import BeautifulSoup

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

filepath = "data/raw_html/k8s_exec_module.html"

with open(filepath, encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, "html.parser")

print("=" * 60)
print("  DIAGNOSTIC — k8s_exec_module.html")
print("=" * 60)

# ── 1. All headings
print("\n[1] ALL HEADINGS (h1/h2/h3/h4):")
for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
    print(f"  <{tag.name}> {tag.get_text()[:80].strip()!r}")

# ── 2. Count structural tags
print("\n[2] TAG COUNTS:")
for tag_name in ["dl", "dt", "dd", "table", "tr", "section", "div"]:
    count = len(soup.find_all(tag_name))
    print(f"  <{tag_name}>: {count}")

# ── 3. Show first <dl> if any
print("\n[3] FIRST <dl> TAG (if any):")
dl = soup.find("dl")
if dl:
    print(dl.prettify()[:1000])
else:
    print("  NO <dl> FOUND")

# ── 4. Show first <table> if any
print("\n[4] FIRST <table> TAG (if any):")
table = soup.find("table")
if table:
    print(table.prettify()[:1000])
else:
    print("  NO <table> FOUND")

# ── 5. Look for the word 'command' (a known required param)
print("\n[5] TAGS CONTAINING 'command':")
for tag in soup.find_all(True):
    if tag.string and "command" in tag.string.lower() and len(tag.string) < 100:
        print(f"  <{tag.name}> class={tag.get('class')} → {tag.string.strip()!r}")

# ── 6. Show 500 chars around the word 'Parameters' in raw HTML
print("\n[6] RAW HTML AROUND 'Parameters' KEYWORD:")
idx = html.find("Parameters")
if idx != -1:
    print(repr(html[max(0, idx-100):idx+500]))
else:
    print("  'Parameters' not found in raw HTML")

# ── 7. Show all unique class names used on divs/sections
print("\n[7] UNIQUE CLASSES ON <div> and <section>:")
classes = set()
for tag in soup.find_all(["div", "section"]):
    for c in tag.get("class", []):
        classes.add(c)
for c in sorted(classes):
    print(f"  .{c}")
