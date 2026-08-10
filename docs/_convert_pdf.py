"""Convert proposition markdown to a well-formatted HTML then PDF via Edge/Chrome."""

import os
import subprocess
import sys

import markdown

SRC = os.path.join(os.path.dirname(__file__), "proposition_architecture_agent.md")
HTML_OUT = os.path.join(os.path.dirname(__file__), "proposition_architecture_agent.html")
PDF_OUT = os.path.join(os.path.dirname(__file__), "proposition_architecture_agent.pdf")

CSS = r"""
@page {
  size: A4;
  margin: 25mm 20mm 20mm 20mm;
  @top-left { content: none; }
  @top-right { content: none; }
  @bottom-left { content: none; }
  @bottom-right { content: none; }
  @bottom-center { content: none; }
  @top-center { content: none; }
}
body {
  font-family: 'Segoe UI', Calibri, Arial, sans-serif;
  font-size: 11pt;
  line-height: 1.55;
  color: #1a1a1a;
  max-width: 100%;
  margin: 0 auto;
  padding: 0;
}
h1 {
  font-size: 20pt;
  border-bottom: 3px solid #2563eb;
  padding-bottom: 8px;
  margin-top: 0;
  color: #111;
}
h2 {
  font-size: 15pt;
  color: #1e40af;
  border-bottom: 1px solid #cbd5e1;
  padding-bottom: 5px;
  margin-top: 28px;
  page-break-after: avoid;
}
h3 {
  font-size: 12.5pt;
  color: #334155;
  margin-top: 18px;
  page-break-after: avoid;
}
p, li {
  text-align: justify;
  orphans: 3;
  widows: 3;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
  font-size: 10pt;
  page-break-inside: avoid;
}
th {
  background: #f1f5f9;
  font-weight: 600;
  text-align: left;
  padding: 7px 10px;
  border: 1px solid #cbd5e1;
}
td {
  padding: 6px 10px;
  border: 1px solid #e2e8f0;
  vertical-align: top;
}
tr:nth-child(even) td {
  background: #f8fafc;
}
pre {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 12px 14px;
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 9pt;
  line-height: 1.45;
  overflow-x: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
  page-break-inside: avoid;
}
code {
  font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
  font-size: 9.5pt;
  background: #f1f5f9;
  padding: 1px 4px;
  border-radius: 3px;
}
pre code {
  background: none;
  padding: 0;
}
strong {
  color: #0f172a;
}
em {
  color: #475569;
}
hr {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 24px 0;
}
ul, ol {
  padding-left: 22px;
}
li {
  margin-bottom: 3px;
}
a {
  color: #2563eb;
  text-decoration: none;
}
blockquote {
  border-left: 3px solid #2563eb;
  margin: 12px 0;
  padding: 6px 14px;
  background: #f8fafc;
  color: #475569;
}
"""

with open(SRC, encoding="utf-8") as f:
    md_text = f.read()

html_body = markdown.markdown(
    md_text,
    extensions=["tables", "fenced_code", "toc", "nl2br"],
)

full_html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

with open(HTML_OUT, "w", encoding="utf-8") as f:
    f.write(full_html)
print(f"HTML generated: {HTML_OUT}")

browsers = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
browser = None
for b in browsers:
    if os.path.exists(b):
        browser = b
        break

if not browser:
    print("No Edge/Chrome found. Open the HTML file in a browser and Print > Save as PDF.")
    sys.exit(0)

print(f"Using: {os.path.basename(browser)}")

import base64
import json
import socket
import time
import urllib.request


def _free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]

port = _free_port()
file_url = "file:///" + HTML_OUT.replace("\\", "/")

proc = subprocess.Popen(
    [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--disable-extensions",
        "--no-first-run",
        "about:blank",
    ],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)

time.sleep(2)

try:
    import websocket

    tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read())
    ws_url = None
    for t in tabs:
        if t.get("type") == "page":
            ws_url = t["webSocketDebuggerUrl"]
            break
    if not ws_url:
        raise RuntimeError("No page target found")

    ws = websocket.create_connection(ws_url)
    _counter = [0]

    def cdp(method, params=None):
        _counter[0] += 1
        _msg_id = _counter[0]
        payload = {"id": _msg_id, "method": method}
        if params:
            payload["params"] = params
        ws.send(json.dumps(payload))
        while True:
            resp = json.loads(ws.recv())
            if resp.get("id") == _msg_id:
                if "error" in resp:
                    raise RuntimeError(resp["error"].get("message", str(resp["error"])))
                return resp.get("result", {})

    cdp("Page.enable")
    cdp("Page.navigate", {"url": file_url})
    time.sleep(3)

    result = cdp("Page.printToPDF", {
        "displayHeaderFooter": False,
        "printBackground": True,
        "preferCSSPageSize": True,
        "marginTop": 0.98,
        "marginBottom": 0.78,
        "marginLeft": 0.78,
        "marginRight": 0.78,
        "paperWidth": 8.27,
        "paperHeight": 11.69,
    })
    ws.close()

    pdf_data = base64.b64decode(result["data"])
    with open(PDF_OUT, "wb") as f:
        f.write(pdf_data)
    print(f"PDF generated: {PDF_OUT} ({len(pdf_data)//1024} KB)")

except Exception as e:
    print(f"CDP failed: {e}")
    print("Falling back to CLI...")
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        pass
    result = subprocess.run(
        [browser, "--headless=new", "--disable-gpu",
         f"--print-to-pdf={PDF_OUT}", "--print-to-pdf-no-header",
         file_url],
        capture_output=True, text=True, timeout=30,
    )
    if os.path.exists(PDF_OUT) and os.path.getsize(PDF_OUT) > 1000:
        print(f"PDF generated (fallback): {PDF_OUT} ({os.path.getsize(PDF_OUT)//1024} KB)")
    else:
        print("Failed. Open the HTML in browser and Print > Save as PDF.")

try:
    proc.terminate()
    proc.wait(timeout=5)
except Exception:
    pass
