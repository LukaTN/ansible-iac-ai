import { useState, type ReactNode } from 'react';
import type { ModuleRef, ModuleRefSource } from '@/lib/types';
import { ChevronIcon } from '@/components/ui/Icons';

function formatSubtitle(ref: ModuleRefSource) {
  const cat = (ref.category || 'other').toUpperCase();
  const params = ref.total_params != null ? ref.total_params : '?';
  const bits: string[] = [];
  if (ref.retrieval_rank != null) bits.push(`#${ref.retrieval_rank}`);
  if (ref.retrieval_top_score != null) bits.push(`score ${ref.retrieval_top_score}`);
  if (ref.is_playbook_module) bits.push('in playbook');
  if (ref.is_rag_primary) bits.push('RAG primary');
  const prefix = bits.length ? `${bits.join(' · ')} · ` : '';
  return `${prefix}${cat} · ${params} parameters`;
}

function SourceChipItem({ ref, badge }: { ref: ModuleRefSource; badge?: string }) {
  const [open, setOpen] = useState(false);
  const id = `src-${ref.module.replace(/\W/g, '-')}`;

  return (
    <div className="source-chip">
      <div
        className="source-head"
        role="button"
        tabIndex={0}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') setOpen(!open);
        }}
      >
        <div className="source-head-left">
          <div className="source-icon">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
            </svg>
          </div>
          <div>
            <div className="source-title">
              <b>{ref.module}</b>
              {badge ? <span className="source-badge">{badge}</span> : null}
            </div>
            <div className="source-sub">{formatSubtitle(ref)}</div>
          </div>
        </div>
        <ChevronIcon open={open} />
      </div>
      <div className={`source-body${open ? ' open' : ''}`} id={id}>
        {ref.description && <p className="source-desc">{ref.description}</p>}
        {ref.doc_url && (
          <a className="source-link" href={ref.doc_url} target="_blank" rel="noreferrer">
            Open official documentation →
          </a>
        )}
        {(ref.required_params || []).length > 0 && (
          <ParamRow label="Required" params={ref.required_params!} />
        )}
        {(ref.optional_params || []).length > 0 && (
          <ParamRow label="Optional" params={ref.optional_params!} optional />
        )}
      </div>
    </div>
  );
}

function ParamRow({
  label,
  params,
  optional,
}: {
  label: string;
  params: { name: string; type?: string }[];
  optional?: boolean;
}) {
  return (
    <div className="src-row">
      <div className="src-row-label">{label}</div>
      <div className="src-row-pills">
        {params.slice(0, 6).map((p) => (
          <span key={p.name} className={`src-pill${optional ? ' opt' : ''}`}>
            <b>{p.name}</b>
            <span>{p.type || 'any'}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

export function SourceStack({ moduleRef }: { moduleRef: ModuleRef }) {
  const sources = moduleRef.sources || [];
  const primary = sources.find((s) => s.is_rag_primary);
  const playbookModule = moduleRef.module && moduleRef.module !== 'unknown' ? moduleRef.module : null;
  const singleSourceMatches =
    sources.length === 1 && playbookModule && sources[0]?.module === playbookModule;
  const showHint =
    playbookModule &&
    !singleSourceMatches &&
    (sources.length > 1 || (primary?.module && primary.module !== playbookModule));

  return (
    <div className="source-stack">
      <div className="source-stack-hdr">
        <span className="source-stack-title">Documentation</span>
        {singleSourceMatches && playbookModule ? (
          <span className="source-stack-module">{playbookModule}</span>
        ) : null}
      </div>
      {showHint && (
        <div className="source-stack-hint">
          <span className="source-stack-label">Playbook module</span>
          <code>{playbookModule}</code>
          {primary?.module && primary.module !== playbookModule && (
            <span className="source-stack-note">
              RAG primary: <code>{primary.module}</code>
            </span>
          )}
        </div>
      )}
      {sources.map((s, i) => (
        <SourceChipItem
          key={`${s.module}-${i}`}
          ref={s}
          badge={singleSourceMatches ? 'Used in playbook' : undefined}
        />
      ))}
    </div>
  );
}

export function RagMetaStrip({ meta }: { meta: NonNullable<import('@/lib/types').RagMeta> }) {
  const parts: ReactNode[] = [];
  if (meta.primary_module)
    parts.push(
      <span key="m">
        <b>Module</b> {meta.primary_module}
      </span>,
    );
  if (meta.primary_collection)
    parts.push(
      <span key="c">
        <b>Collection</b> {meta.primary_collection}
      </span>,
    );
  if (meta.primary_score != null)
    parts.push(
      <span key="s">
        <b>Score</b> {meta.primary_score}
      </span>,
    );
  if (meta.chunks != null)
    parts.push(
      <span key="ch">
        <b>Chunks</b> {meta.chunks}
      </span>,
    );
  if (!parts.length) return null;

  return (
    <div className="rag-meta-strip">
      {parts.map((p, i) => (
        <span key={i}>
          {i > 0 && <span className="dotsep"> · </span>}
          {p}
        </span>
      ))}
      {meta.source_url && (
        <>
          <span className="dotsep"> · </span>
          <a className="rag-meta-link" href={meta.source_url} target="_blank" rel="noreferrer">
            docs
          </a>
        </>
      )}
    </div>
  );
}
