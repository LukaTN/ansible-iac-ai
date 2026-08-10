import type { ReactNode } from 'react';

function esc(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

interface Block {
  lang: string;
  code: string;
}

export function renderMarkdown(text: string | null | undefined): ReactNode {
  if (!text) return null;
  const raw = String(text);
  const blocks: Block[] = [];

  let body = raw.replace(/```([a-zA-Z0-9_-]+)?\s*\n([\s\S]*?)```/g, (_, lang: string, code: string) => {
    blocks.push({ lang: lang || '', code });
    return `\u0000BLOCK${blocks.length - 1}\u0000`;
  });

  body = esc(body);
  body = body.replace(/`([^`]+)`/g, '<code>$1</code>');
  body = body.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');

  const lines = body.split('\n');
  const out: string[] = [];
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${para.join(' ')}</p>`);
      para = [];
    }
  };
  let inList = false;

  for (const line of lines) {
    if (/^BLOCK\d+$/.test(line.replace(/\u0000/g, ''))) {
      flushPara();
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
      out.push(line);
      continue;
    }
    const t = line.trim();
    if (!t) {
      flushPara();
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
      continue;
    }
    if (/^[-*]\s+/.test(t)) {
      flushPara();
      if (!inList) {
        out.push('<ul>');
        inList = true;
      }
      out.push(`<li>${t.replace(/^[-*]\s+/, '')}</li>`);
    } else {
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
      para.push(t);
    }
  }
  flushPara();
  if (inList) out.push('</ul>');

  let html = out.join('\n');
  html = html.replace(/\u0000BLOCK(\d+)\u0000/g, (_, i: string) => {
    const b = blocks[+i];
    return `<pre class="md-code"><code class="lang-${esc(b.lang)}">${esc(b.code)}</code></pre>`;
  });

  return <div className="md-content" dangerouslySetInnerHTML={{ __html: html }} />;
}
