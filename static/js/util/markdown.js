/* =============================================================
   util/markdown.js — minimal markdown renderer for assistant text.

   Supports:
     • fenced code blocks (```lang … ```)
     • inline `code`
     • bullet lists
     • bold **text**
     • paragraphs
   ============================================================= */

import { esc } from './dom.js';

export function renderMarkdown(text) {
  if (!text) return '';
  const raw = String(text);

  // Extract fenced code blocks first to placeholders so downstream
  // escaping doesn't mangle their contents.
  const blocks = [];
  let body = raw.replace(/```([a-zA-Z0-9_-]+)?\s*\n([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push({ lang: lang || '', code });
    return `\u0000BLOCK${blocks.length - 1}\u0000`;
  });

  body = esc(body);
  body = body.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  body = body.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');

  const lines = body.split('\n');
  const out = [];
  let para = [];
  const flushPara = () => {
    if (para.length) { out.push(`<p>${para.join(' ')}</p>`); para = []; }
  };
  let inList = false;
  for (const line of lines) {
    if (/^BLOCK\d+$/.test(line.replace(/\u0000/g, ''))) {
      flushPara();
      if (inList) { out.push('</ul>'); inList = false; }
      out.push(line);
      continue;
    }
    const t = line.trim();
    if (!t) {
      flushPara();
      if (inList) { out.push('</ul>'); inList = false; }
      continue;
    }
    if (/^[-*]\s+/.test(t)) {
      flushPara();
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push(`<li>${t.replace(/^[-*]\s+/, '')}</li>`);
    } else {
      if (inList) { out.push('</ul>'); inList = false; }
      para.push(t);
    }
  }
  flushPara();
  if (inList) out.push('</ul>');

  let html = out.join('\n');
  html = html.replace(/\u0000BLOCK(\d+)\u0000/g, (_, i) => {
    const b = blocks[+i];
    return `<pre class="md-code"><code class="lang-${esc(b.lang)}">${esc(b.code)}</code></pre>`;
  });
  return html;
}
