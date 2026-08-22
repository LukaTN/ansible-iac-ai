import { useEffect, useMemo, useRef, useState } from 'react';
import { CodeBracketsIcon } from '@/components/ui/Icons';
import {
  AMBIENT_THOUGHTS,
  GENERATION_PIPELINE,
  formatElapsed,
  pipelineProgress,
  stepHint,
  stepIndex,
  stepLabel,
} from '@/lib/generationSteps';
import type { ThreadGenerationState } from '@/lib/socket';

const NODE_R = 13;
const NODE_CY = 22;
const NODE_SPACING = 100;
const NODE_PAD = 20;
const NODE_X = GENERATION_PIPELINE.map((_, i) => NODE_PAD + i * NODE_SPACING);
const SVG_W = NODE_X[NODE_X.length - 1] + NODE_PAD;
const SVG_H = 58;
const RING_R = 19;
const RING_C = 2 * Math.PI * RING_R;

function formatTs(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

interface AgentThinkingProps {
  state?: ThreadGenerationState;
}

export function AgentThinking({ state }: AgentThinkingProps) {
  const [ambientIdx, setAmbientIdx] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [mountedAt] = useState(() => Date.now());
  const [thoughtKey, setThoughtKey] = useState(0);
  const termRef = useRef<HTMLDivElement>(null);

  const currentStep = state?.step ?? 'planning';
  const activeIdx = stepIndex(currentStep);
  const thoughts = state?.thoughts ?? [];
  const latestThought = thoughts[thoughts.length - 1];
  const startedAt = thoughts[0]?.at ?? mountedAt;
  const elapsed = formatElapsed(now - startedAt);
  const progress = pipelineProgress(currentStep);
  const phaseLabel = stepLabel(currentStep);
  const phaseHint = stepHint(currentStep);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (thoughts.length > 0) return;
    const id = window.setInterval(() => {
      setAmbientIdx((i) => (i + 1) % AMBIENT_THOUGHTS.length);
      setThoughtKey((k) => k + 1);
    }, 2800);
    return () => window.clearInterval(id);
  }, [thoughts.length]);

  useEffect(() => {
    if (!latestThought) return;
    setThoughtKey((k) => k + 1);
  }, [latestThought?.id, latestThought?.text, latestThought?.detail]);

  useEffect(() => {
    if (termRef.current) {
      termRef.current.scrollTop = termRef.current.scrollHeight;
    }
  }, [thoughts.length, thoughtKey]);

  const displayThought = useMemo(() => {
    if (latestThought) {
      return latestThought.detail
        ? `${latestThought.text} · ${latestThought.detail}`
        : latestThought.text;
    }
    return state?.message ?? AMBIENT_THOUGHTS[ambientIdx];
  }, [latestThought, state?.message, ambientIdx]);

  const visibleThoughts = thoughts.slice(-6);
  const history = visibleThoughts.length > 1 ? visibleThoughts.slice(0, -1) : [];

  return (
    <div className="msg msg-agent agent-thinking msg-enter" aria-live="polite" aria-busy="true">
      {/* Avatar with ring progress */}
      <div className="at-avatar" aria-hidden>
        <svg className="at-avatar-ring" viewBox="0 0 44 44">
          <circle className="at-ring-track" cx="22" cy="22" r={RING_R} />
          <circle
            className="at-ring-fill"
            cx="22"
            cy="22"
            r={RING_R}
            strokeDasharray={RING_C}
            strokeDashoffset={RING_C * (1 - progress)}
          />
        </svg>
        <CodeBracketsIcon size={14} />
      </div>

      <div className="msg-body">
        <div className="at-card">
          <div className="at-hdr">
            <div className="at-hdr-left">
              <span className="at-title">AnsibleAI</span>
              <span className="at-phase">
                <span className="at-phase-dot" />
                {phaseLabel}
              </span>
            </div>
            <time
              className="at-elapsed"
              dateTime={`PT${Math.floor((now - startedAt) / 1000)}S`}
            >
              {elapsed}
            </time>
          </div>

          {/* SVG Pipeline */}
          <svg
            className="at-pipeline"
            viewBox={`0 0 ${SVG_W} ${SVG_H}`}
            role="img"
            aria-label={`Pipeline: ${phaseLabel}`}
          >
            {NODE_X.slice(0, -1).map((x, j) => {
              const x1 = x + NODE_R;
              const x2 = NODE_X[j + 1] - NODE_R;
              const done = j + 1 < activeIdx;
              const active = j + 1 === activeIdx;
              return (
                <line
                  key={`c${j}`}
                  x1={x1}
                  y1={NODE_CY}
                  x2={x2}
                  y2={NODE_CY}
                  className={`at-conn${done ? ' done' : active ? ' active' : ''}`}
                />
              );
            })}

            {/* Nodes */}
            {GENERATION_PIPELINE.map((step, i) => {
              const cx = NODE_X[i];
              const done = i < activeIdx;
              const active = i === activeIdx;
              return (
                <g
                  key={step.key}
                  className={`at-node${done ? ' done' : active ? ' active' : ' pending'}`}
                >
                  {active ? (
                    <circle cx={cx} cy={NODE_CY} r={NODE_R + 5} className="at-node-ring" />
                  ) : null}
                  <circle cx={cx} cy={NODE_CY} r={NODE_R} className="at-node-bg" />
                  {done ? (
                    <text
                      x={cx}
                      y={NODE_CY + 1}
                      textAnchor="middle"
                      dominantBaseline="central"
                      className="at-node-check"
                    >
                      ✓
                    </text>
                  ) : (
                    <text
                      x={cx}
                      y={NODE_CY + 1}
                      textAnchor="middle"
                      dominantBaseline="central"
                      className="at-node-icon"
                    >
                      {step.icon}
                    </text>
                  )}
                  <text
                    x={cx}
                    y={SVG_H - 2}
                    textAnchor="middle"
                    className="at-node-label"
                  >
                    {step.label}
                  </text>
                </g>
              );
            })}
          </svg>

          {/* Hint */}
          <p className="at-hint" key={currentStep}>
            {phaseHint}
          </p>

          {/* Terminal stream */}
          <div className="at-terminal">
            <div className="at-term-bar">
              <span className="at-term-title">Log</span>
              <span className="at-live">Live</span>
            </div>
            <div className="at-term-body" ref={termRef}>
              {history.map((t) => (
                <div key={t.id} className="at-term-line at-term-past">
                  <span className="at-term-ts">{formatTs(t.at)}</span>
                  <span className="at-term-pfx">›</span>
                  <span className="at-term-msg">{t.detail ? `${t.text} · ${t.detail}` : t.text}</span>
                </div>
              ))}
              <div className="at-term-line at-term-now" key={thoughtKey}>
                <span className="at-term-pfx now">›</span>
                <span className="at-cursor" aria-hidden />
                <span className="at-term-msg now">{displayThought}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** @deprecated Use AgentThinking */
export function TypingIndicator({ progressMessage }: { progressMessage?: string }) {
  return (
    <AgentThinking
      state={
        progressMessage
          ? {
              step: 'planning',
              message: progressMessage,
              thoughts: [{ id: 0, step: 'planning', text: progressMessage, at: Date.now() }],
              updatedAt: Date.now(),
            }
          : undefined
      }
    />
  );
}
