import { useState } from 'react';

interface PlaybookCardProps {
  playbook: string;
  filename?: string | null;
  module?: string | null;
}

export function PlaybookCard({ playbook, filename, module }: PlaybookCardProps) {
  const [copied, setCopied] = useState(false);
  const name = filename || 'playbook.yml';
  const id = `pb-${name.replace(/\W/g, '-')}`;

  const handleCopy = (e: React.MouseEvent<HTMLButtonElement>) => {
    navigator.clipboard.writeText(playbook).then(() => {
      setCopied(true);
      const btn = e.currentTarget;
      const prev = btn.textContent;
      btn.textContent = 'copied!';
      setTimeout(() => {
        btn.textContent = prev;
        setCopied(false);
      }, 1200);
    });
  };

  return (
    <div className="code-card">
      <div className="code-hdr">
        <div className="code-hdr-left">
          <div className="dots">
            <div className="dot r" />
            <div className="dot y" />
            <div className="dot g" />
          </div>
          <span className="fname">{name}</span>
          {module && <span className="mod-tag-inline">{module}</span>}
        </div>
        <button type="button" className="copy-btn" data-target={id} onClick={handleCopy}>
          {copied ? 'copied!' : 'copy'}
        </button>
      </div>
      <pre id={id}>{playbook}</pre>
    </div>
  );
}
