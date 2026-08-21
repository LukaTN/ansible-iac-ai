import { useState } from 'react';

interface PlaybookCardProps {
  playbook: string;
  filename?: string | null;
  module?: string | null;
}

export function PlaybookCard({ playbook, filename, module }: PlaybookCardProps) {
  const [copied, setCopied] = useState(false);
  const name = filename || 'playbook.yml';

  const handleCopy = () => {
    navigator.clipboard.writeText(playbook).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    });
  };

  return (
    <div className="code-card">
      <div className="code-hdr">
        <div className="code-hdr-left">
          <span className="fname">{name}</span>
          {module ? <span className="mod-tag-inline">{module}</span> : null}
        </div>
        <button
          type="button"
          className="ui-btn ui-btn-ghost copy-btn"
          onClick={handleCopy}
          aria-label={copied ? 'Copied playbook to clipboard' : `Copy ${name}`}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre>{playbook}</pre>
    </div>
  );
}
