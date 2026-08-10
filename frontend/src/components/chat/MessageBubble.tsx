import type { ChatMessage, ToolTraceEntry } from '@/lib/types';
import { renderMarkdown } from '@/lib/markdown';
import { relTime } from '@/lib/time';
import { CodeBracketsIcon, ChevronIcon } from '@/components/ui/Icons';
import { PlaybookCard } from './PlaybookCard';
import { ValidationCard } from './ValidationCard';
import { RagMetaStrip, SourceStack } from './SourceChip';

const TOOL_LABELS: Record<string, string> = {
  search_docs: 'Search docs',
  generate_playbook: 'Generate playbook',
  draft_playbook: 'Draft playbook',
  validate_yaml: 'Validate YAML',
  gate: 'Production gate',
  get_module_info: 'Module info',
  clarify_decider: 'Clarify',
  local_observability_guard: 'Guardrail',
};

function isAwaitingUser(m: ChatMessage): boolean {
  if (m.rag_meta?.awaiting_user) return true;
  return (
    !m.playbook &&
    Array.isArray(m.tool_trace) &&
    m.tool_trace.some((t) => t.tool === 'search_docs' || t.tool === 'clarify_decider') &&
    /\?\s*$|\?\s*\n|required|provide|need a few|which|what/i.test((m.content || '').slice(-200))
  );
}

function toolTraceSummary(entry: ToolTraceEntry): string {
  const result = entry.result as Record<string, unknown> | undefined;
  if (entry.tool === 'search_docs' && result?.primary_module) {
    return String(result.primary_module);
  }
  if ((entry.tool === 'generate_playbook' || entry.tool === 'draft_playbook') && result) {
    if (result.module) return String(result.module);
    if (result.filename) return String(result.filename);
    return `${result.yaml_chars ?? '?'} chars`;
  }
  if (entry.tool === 'gate' && result) {
    const lint = result.ansible_lint as string | undefined;
    const lintViolations = (result.ansible_lint_violations as number) ?? 0;
    const ready = result.ready as boolean | undefined;
    const parts: string[] = [];
    if (ready) {
      parts.push('passed');
    } else {
      const errs = (result.errors as number) ?? 0;
      parts.push(errs > 0 ? `${errs} error(s)` : 'issues found');
    }
    if (lint === 'passed') parts.push('lint clean');
    else if (lint === 'violations') parts.push(`lint: ${lintViolations} violation(s)`);
    else if (lint) parts.push(`lint: ${lint}`);
    return parts.join(' · ');
  }
  if (entry.tool === 'validate_yaml' && result?.is_valid != null) {
    return result.is_valid ? 'valid' : 'invalid';
  }
  if (entry.tool === 'clarify_decider') {
    return 'questions';
  }
  return '';
}

function ToolTracePanel({ trace }: { trace: ToolTraceEntry[] }) {
  if (!trace.length) return null;

  const preview = trace.map((e) => TOOL_LABELS[e.tool] || e.tool).join(' → ');

  return (
    <details className="tool-trace" open={trace.length <= 3}>
      <summary>
        <span className="tool-trace-label">Agent steps</span>
        <span className="tool-trace-count">{trace.length}</span>
        <span className="tool-trace-preview">{preview}</span>
        <span className="tool-trace-chev" aria-hidden>
          <ChevronIcon />
        </span>
      </summary>
      <ol className="tool-trace-steps">
        {trace.map((entry, i) => {
          const label = TOOL_LABELS[entry.tool] || entry.tool;
          const detail = toolTraceSummary(entry);
          const result = entry.result as Record<string, unknown> | undefined;
          const isGate = entry.tool === 'gate';
          const gateReady = isGate && result?.ready === true;
          const gateFailed = isGate && result?.ready === false;
          return (
            <li
              key={`${entry.tool}-${i}`}
              className={gateReady ? 'gate-step-ok' : gateFailed ? 'gate-step-fail' : ''}
            >
              <span className="tool-step-num">
                {isGate ? (gateReady ? '✓' : '✕') : i + 1}
              </span>
              <div className="tool-step-body">
                <span className="tool-trace-name">{label}</span>
                {detail ? <span className="tool-trace-detail">{detail}</span> : null}
              </div>
            </li>
          );
        })}
      </ol>
    </details>
  );
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="msg msg-user msg-enter">
        <div>
          <div className="msg-bubble user-bubble">{renderMarkdown(message.content)}</div>
          <div className="msg-meta">{relTime(message.ts)}</div>
        </div>
      </div>
    );
  }

  const awaiting = isAwaitingUser(message);

  return (
    <div className="msg msg-agent msg-enter">
      <div className="msg-avatar">
        <CodeBracketsIcon />
      </div>
      <div className="msg-body">
        <div className={`msg-bubble assistant-bubble${awaiting ? ' awaiting' : ''}`}>
          {renderMarkdown(message.content || '')}
        </div>

        {message.playbook ||
        message.validation ||
        message.module_ref ||
        message.rag_meta?.primary_module ||
        (message.tool_trace && message.tool_trace.length > 0) ? (
          <div className="msg-artifacts">
            {message.playbook && (
              <PlaybookCard playbook={message.playbook} filename={message.filename} module={message.module} />
            )}
            {message.validation && <ValidationCard validation={message.validation} />}
            {message.module_ref && Array.isArray(message.module_ref.sources) && message.module_ref.sources.length >= 2 ? (
              <SourceStack moduleRef={message.module_ref} />
            ) : message.module_ref?.found ? (
              <SourceStack
                moduleRef={{
                  ...message.module_ref,
                  sources: message.module_ref.sources || [
                    message.module_ref as import('@/lib/types').ModuleRefSource,
                  ],
                }}
              />
            ) : message.rag_meta?.primary_module ? (
              <RagMetaStrip meta={message.rag_meta} />
            ) : null}
            {Array.isArray(message.tool_trace) && message.tool_trace.length > 0 && (
              <ToolTracePanel trace={message.tool_trace} />
            )}
          </div>
        ) : null}

        <div className="msg-meta">{relTime(message.ts)}</div>
      </div>
    </div>
  );
}

