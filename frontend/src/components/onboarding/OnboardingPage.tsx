import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useOnboarding } from '@/app/providers/OnboardingProvider';
import { CodeBracketsIcon } from '@/components/ui/Icons';

/* ────────────────────────────────────────────────────────────────
   Step registry
   ──────────────────────────────────────────────────────────────── */

const STEPS = [
  { id: 'welcome', num: '01', label: 'Welcome' },
  { id: 'loop', num: '02', label: 'The agent loop' },
  { id: 'tour', num: '03', label: 'Workspace tour' },
  { id: 'craft', num: '04', label: 'Prompt craft' },
] as const;

/* ────────────────────────────────────────────────────────────────
   Step 01 — Welcome
   ──────────────────────────────────────────────────────────────── */

const TERM_LINES = [
  { pfx: '$', text: 'ansible-ai run "deploy nginx with helm"', tone: 'cmd' },
  { pfx: '›', text: 'reason · intent = generate_playbook', tone: 'dim' },
  { pfx: '›', text: 'search_docs → kubernetes.core · 6 chunks', tone: 'dim' },
  { pfx: '›', text: 'draft · playbook.yml — 24 lines', tone: 'dim' },
  { pfx: '›', text: 'gate · validator 0 errors · ansible-lint passed', tone: 'ok' },
  { pfx: '✓', text: 'production-ready playbook delivered', tone: 'ok' },
] as const;

const CAPABILITIES = [
  'RAG-grounded',
  'ansible-lint gate',
  'Self-repair loop',
  'Live agent trace',
  'Multi-turn threads',
  'Docs index',
] as const;

function WelcomeStep() {
  return (
    <div className="onb-welcome">
      <div className="onb-welcome-copy">
        <p className="onb-eyebrow onb-anim" style={{ animationDelay: '40ms' }}>
          Mission briefing // 01 — Welcome
        </p>
        <h1 className="onb-title onb-anim" style={{ animationDelay: '120ms' }}>
          Describe the task.
          <br />
          <span className="onb-title-accent">Ship the playbook.</span>
        </h1>
        <p className="onb-lede onb-anim" style={{ animationDelay: '220ms' }}>
          AnsibleAI turns plain-language infrastructure requests into Ansible playbooks
          grounded on indexed official module documentation — no invented parameters —
          then proves every draft against a validation gate before it reaches you.
        </p>
        <ul className="onb-chips onb-anim" style={{ animationDelay: '320ms' }}>
          {CAPABILITIES.map((c) => (
            <li key={c} className="onb-chip">
              <span className="onb-chip-dot" aria-hidden />
              {c}
            </li>
          ))}
        </ul>
      </div>

      <div className="onb-term onb-anim" style={{ animationDelay: '260ms' }} aria-hidden>
        <div className="onb-term-bar">
          <span className="onb-term-dots">
            <i /> <i /> <i />
          </span>
          <span className="onb-term-title">agent session — live trace</span>
          <span className="onb-term-live">REC</span>
        </div>
        <div className="onb-term-body">
          {TERM_LINES.map((line, i) => (
            <div
              key={line.text}
              className={`onb-term-line tone-${line.tone}`}
              style={{ animationDelay: `${500 + i * 420}ms` }}
            >
              <span className="onb-term-pfx">{line.pfx}</span>
              <span className="onb-term-text">{line.text}</span>
            </div>
          ))}
          <span
            className="onb-term-caret"
            style={{ animationDelay: `${500 + TERM_LINES.length * 420}ms` }}
          />
        </div>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────
   Step 02 — The agent loop
   ──────────────────────────────────────────────────────────────── */

interface LoopNode {
  id: string;
  glyph: string;
  label: string;
  title: string;
  desc: string;
  meta: string;
}

const LOOP_NODES: LoopNode[] = [
  {
    id: 'reason',
    glyph: 'R',
    label: 'Reason',
    title: 'Chain-of-thought planner',
    desc: 'Reads your request and decides the next move — search the docs, ask a clarifying question, draft, or answer directly. After a failed gate it writes the fix plan for the redraft.',
    meta: 'fallback: deterministic planner if the LLM is down',
  },
  {
    id: 'search',
    glyph: 'S',
    label: 'Search',
    title: 'Grounded retrieval',
    desc: 'Semantic search over the ChromaDB index of official Ansible module docs. Collection routing votes for the right namespace, with one broadened fallback search.',
    meta: 'tool: search_docs · multi-collection retriever',
  },
  {
    id: 'draft',
    glyph: 'D',
    label: 'Draft',
    title: 'One grounded YAML pass',
    desc: 'The playbook LLM writes — or repairs — a single YAML draft, conditioned on the retrieved docs, previous gate feedback, and the fix plan. Repairs overwrite the same file.',
    meta: 'tool: draft_playbook · docs + feedback + fix plan',
  },
  {
    id: 'gate',
    glyph: 'G',
    label: 'Gate',
    title: 'The production gate',
    desc: 'Full KB-aware validator plus ansible-lint on the saved file. Production-ready means zero validator errors, lint passed, and no placeholders. Anything else loops back for repair.',
    meta: 'repair budget: up to 4 iterations',
  },
  {
    id: 'respond',
    glyph: '✓',
    label: 'Respond',
    title: 'Verdict, streamed live',
    desc: 'The final reply lands in your chat with the playbook, the gate verdict, the exact doc sources used, and the full tool trace — progress streamed over WebSocket as it happens.',
    meta: 'delivers: playbook · validation · sources · trace',
  },
];

function LoopStep() {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) return;
    const t = window.setInterval(() => setActive((a) => (a + 1) % LOOP_NODES.length), 2400);
    return () => window.clearInterval(t);
  }, [paused]);

  const node = LOOP_NODES[active];

  return (
    <div className="onb-loop">
      <p className="onb-eyebrow onb-anim" style={{ animationDelay: '40ms' }}>
        Mission briefing // 02 — The agent loop
      </p>
      <h2 className="onb-h2 onb-anim" style={{ animationDelay: '120ms' }}>
        One agent. Five stations. <span className="onb-title-accent">A gate that bites.</span>
      </h2>
      <p className="onb-lede onb-anim" style={{ animationDelay: '200ms' }}>
        Every request flows through a LangGraph state machine that loops on its own work
        until the production gate passes — or tells you exactly why it could not.
      </p>

      <div className="onb-loop-diagram onb-anim" style={{ animationDelay: '300ms' }}>
        <div className="onb-loop-track" role="tablist" aria-label="Agent pipeline stages">
          {LOOP_NODES.map((n, i) => (
            <div className="onb-loop-cell" key={n.id}>
              {i > 0 ? (
                <span
                  className={`onb-loop-conn${i <= active ? ' lit' : ''}`}
                  aria-hidden
                />
              ) : null}
              <button
                type="button"
                role="tab"
                aria-selected={i === active}
                className={`onb-loop-node${i === active ? ' active' : ''}${i < active ? ' done' : ''}`}
                onClick={() => {
                  setActive(i);
                  setPaused(true);
                }}
              >
                <span className="onb-loop-glyph">{n.glyph}</span>
                <span className="onb-loop-label">{n.label}</span>
              </button>
            </div>
          ))}
        </div>

        <svg
          className="onb-loop-arc"
          viewBox="0 0 100 26"
          preserveAspectRatio="none"
          aria-hidden
        >
          <path
            d="M 90 2 C 90 24, 10 24, 10 4"
            fill="none"
            vectorEffect="non-scaling-stroke"
            markerEnd="url(#onb-arrow)"
          />
          <defs>
            <marker
              id="onb-arrow"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
          </defs>
        </svg>
        <p className="onb-loop-arc-label">
          repair loop — a failed gate sends the draft back with a fix plan
        </p>

        <div className="onb-loop-detail" key={node.id}>
          <div className="onb-loop-detail-hdr">
            <span className="onb-loop-detail-idx">
              {String(active + 1).padStart(2, '0')} / {String(LOOP_NODES.length).padStart(2, '0')}
            </span>
            <h3 className="onb-loop-detail-title">{node.title}</h3>
          </div>
          <p className="onb-loop-detail-desc">{node.desc}</p>
          <p className="onb-loop-detail-meta">{node.meta}</p>
        </div>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────
   Step 03 — Workspace tour
   ──────────────────────────────────────────────────────────────── */

const TOUR_SPOTS = [
  {
    id: 1,
    title: 'Threads',
    desc: 'Every conversation is saved. Search, resume, or delete past sessions from the sidebar — generation continues even if you switch threads.',
  },
  {
    id: 2,
    title: 'Composer',
    desc: 'Describe the task in plain language. Enter sends, Shift+Enter adds a line. Paste broken YAML here and the agent switches to troubleshoot mode.',
  },
  {
    id: 3,
    title: 'Validation card',
    desc: 'Each playbook carries its gate verdict: validator checks, warnings, and the ansible-lint result — expanded inline, nothing hidden.',
  },
  {
    id: 4,
    title: 'Sources',
    desc: 'The exact module documentation chunks the draft was grounded on, with required parameters and links to the official docs.',
  },
  {
    id: 5,
    title: 'Analytics & docs',
    desc: 'The right panel tracks generation stats and validation breakdowns, and shows the documentation index the assistant retrieves from.',
  },
  {
    id: 6,
    title: 'Live agent trace',
    desc: 'While the agent works, a mission-control view shows each pipeline station, elapsed time, and a streaming terminal of its steps.',
  },
] as const;

function TourStep() {
  const [hot, setHot] = useState<number | null>(null);

  const spotProps = (id: number) => ({
    onMouseEnter: () => setHot(id),
    onMouseLeave: () => setHot(null),
    onFocus: () => setHot(id),
    onBlur: () => setHot(null),
  });

  return (
    <div className="onb-tour">
      <p className="onb-eyebrow onb-anim" style={{ animationDelay: '40ms' }}>
        Mission briefing // 03 — Workspace tour
      </p>
      <h2 className="onb-h2 onb-anim" style={{ animationDelay: '120ms' }}>
        Six things worth knowing <span className="onb-title-accent">before you type.</span>
      </h2>

      <div className="onb-tour-grid onb-anim" style={{ animationDelay: '240ms' }}>
        <div className="onb-mock" aria-hidden>
          <div className="onb-mock-bar">
            <span className="onb-term-dots">
              <i /> <i /> <i />
            </span>
            <span className="onb-mock-url">ansibleai — workspace</span>
          </div>
          <div className="onb-mock-body">
            <div className={`onb-mock-side${hot === 1 ? ' hot' : ''}`}>
              <span className="onb-mock-brand" />
              <span className="onb-mock-newchat" />
              <span className="onb-mock-thread active" />
              <span className="onb-mock-thread" />
              <span className="onb-mock-thread" />
              <button type="button" className="onb-hotspot" style={{ top: '42%', left: '50%' }} tabIndex={-1} {...spotProps(1)}>
                1
              </button>
            </div>
            <div className="onb-mock-chat">
              <span className="onb-mock-topbar" />
              <div className="onb-mock-feed">
                <span className="onb-mock-bubble user" />
                <div className={`onb-mock-trace${hot === 6 ? ' hot' : ''}`}>
                  <span className="onb-mock-trace-dot" />
                  <span className="onb-mock-trace-line" />
                  <button type="button" className="onb-hotspot" style={{ top: '-8px', right: '-8px' }} tabIndex={-1} {...spotProps(6)}>
                    6
                  </button>
                </div>
                <div className="onb-mock-bubble agent">
                  <div className={`onb-mock-val${hot === 3 ? ' hot' : ''}`}>
                    <span className="onb-mock-val-chip">GATE PASSED</span>
                    <button type="button" className="onb-hotspot" style={{ top: '-9px', right: '-9px' }} tabIndex={-1} {...spotProps(3)}>
                      3
                    </button>
                  </div>
                  <div className={`onb-mock-src${hot === 4 ? ' hot' : ''}`}>
                    <span /> <span /> <span />
                    <button type="button" className="onb-hotspot" style={{ top: '-9px', right: '-9px' }} tabIndex={-1} {...spotProps(4)}>
                      4
                    </button>
                  </div>
                </div>
              </div>
              <div className={`onb-mock-composer${hot === 2 ? ' hot' : ''}`}>
                <span className="onb-mock-composer-line" />
                <span className="onb-mock-send" />
                <button type="button" className="onb-hotspot" style={{ top: '-10px', left: '50%' }} tabIndex={-1} {...spotProps(2)}>
                  2
                </button>
              </div>
            </div>
            <div className={`onb-mock-panel${hot === 5 ? ' hot' : ''}`}>
              <span className="onb-mock-stat" />
              <span className="onb-mock-stat" />
              <span className="onb-mock-bar-chart" />
              <button type="button" className="onb-hotspot" style={{ top: '30%', left: '50%' }} tabIndex={-1} {...spotProps(5)}>
                5
              </button>
            </div>
          </div>
        </div>

        <ol className="onb-tour-list">
          {TOUR_SPOTS.map((s, i) => (
            <li key={s.id} className="onb-anim" style={{ animationDelay: `${300 + i * 70}ms` }}>
              <button
                type="button"
                className={`onb-tour-item${hot === s.id ? ' hot' : ''}`}
                {...spotProps(s.id)}
              >
                <span className="onb-tour-num">{s.id}</span>
                <span className="onb-tour-body">
                  <span className="onb-tour-title">{s.title}</span>
                  <span className="onb-tour-desc">{s.desc}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      </div>

      <div className="onb-keys onb-anim" style={{ animationDelay: '720ms' }}>
        <span className="onb-keys-label">Shortcuts</span>
        <span className="onb-key-combo">
          <kbd className="kbd">Enter</kbd> send
        </span>
        <span className="onb-key-combo">
          <kbd className="kbd">Shift</kbd> + <kbd className="kbd">Enter</kbd> new line
        </span>
        <span className="onb-key-combo">
          <kbd className="kbd">Esc</kbd> close dialogs
        </span>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────
   Step 04 — Prompt craft
   ──────────────────────────────────────────────────────────────── */

const DOS = [
  'Name the target — module, collection, environment: "kubernetes.core.helm, production".',
  'State constraints up front: replicas, namespaces, image versions, node selectors.',
  'Iterate in the same thread — repairs and refinements reuse the full context.',
  'Paste broken YAML and ask what is wrong; the agent validates and explains it.',
] as const;

const DONTS = [
  'Don’t ask for infrastructure the knowledge base doesn’t cover — check the docs panel first.',
  'Don’t expect live cluster access — the deliverable is a validated playbook file.',
  'Don’t bury the request in a wall of text; one clear intent per message drafts best.',
] as const;

const EXAMPLE_PROMPTS = [
  'Deploy nginx using Helm in production',
  'Drain a node for maintenance',
  'Explain the kubernetes.core.k8s module',
] as const;

function CraftStep({ onLaunch }: { onLaunch: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(null), 1600);
    return () => window.clearTimeout(t);
  }, [copied]);

  const copy = (text: string) => {
    setCopied(text);
    void navigator.clipboard?.writeText(text).catch(() => undefined);
  };

  return (
    <div className="onb-craft">
      <p className="onb-eyebrow onb-anim" style={{ animationDelay: '40ms' }}>
        Mission briefing // 04 — Prompt craft
      </p>
      <h2 className="onb-h2 onb-anim" style={{ animationDelay: '120ms' }}>
        Brief the agent <span className="onb-title-accent">like an engineer.</span>
      </h2>

      <div className="onb-craft-grid">
        <section className="onb-craft-card do onb-anim" style={{ animationDelay: '220ms' }}>
          <h3 className="onb-craft-card-title">Do</h3>
          <ul>
            {DOS.map((d) => (
              <li key={d}>
                <span className="onb-mark ok" aria-hidden>
                  ✓
                </span>
                {d}
              </li>
            ))}
          </ul>
        </section>
        <section className="onb-craft-card dont onb-anim" style={{ animationDelay: '300ms' }}>
          <h3 className="onb-craft-card-title">Don’t</h3>
          <ul>
            {DONTS.map((d) => (
              <li key={d}>
                <span className="onb-mark bad" aria-hidden>
                  ✕
                </span>
                {d}
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="onb-prompts onb-anim" style={{ animationDelay: '400ms' }}>
        <span className="onb-prompts-label">Try one of these — click to copy</span>
        <div className="onb-prompts-row">
          {EXAMPLE_PROMPTS.map((p) => (
            <button
              key={p}
              type="button"
              className="onb-prompt-chip"
              onClick={() => copy(p)}
            >
              <span className="onb-prompt-text">{p}</span>
              <span className="onb-prompt-copy">{copied === p ? 'Copied' : 'Copy'}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="onb-launch onb-anim" style={{ animationDelay: '500ms' }}>
        <div className="onb-launch-copy">
          <h3>Ready for your first draft?</h3>
          <p>
            You can reopen this guide anytime from the account menu or the help button in
            the top bar.
          </p>
        </div>
        <button type="button" className="onb-cta" onClick={onLaunch}>
          Enter the workspace
          <span className="onb-cta-arrow" aria-hidden>
            →
          </span>
        </button>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────
   Shell
   ──────────────────────────────────────────────────────────────── */

export function OnboardingPage() {
  const { open, closeGuide } = useOnboarding();

  if (!open) return null;

  // The overlay remounts each time the guide opens, resetting the step.
  return createPortal(<OnboardingOverlay onClose={closeGuide} />, document.body);
}

const EXIT_MS = 460;

function OnboardingOverlay({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [closing, setClosing] = useState(false);
  const closeTimer = useRef<number | null>(null);

  /* Plays the exit animation before unmounting, so the workspace is
     revealed through a smooth fade/blur instead of a hard cut. */
  const close = useCallback(() => {
    if (closeTimer.current !== null) return;
    setClosing(true);
    closeTimer.current = window.setTimeout(onClose, EXIT_MS);
  }, [onClose]);

  useEffect(() => {
    return () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
      if (e.key === 'ArrowRight') setStep((s) => Math.min(s + 1, STEPS.length - 1));
      if (e.key === 'ArrowLeft') setStep((s) => Math.max(s - 1, 0));
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [close]);

  const last = step === STEPS.length - 1;

  return (
    <div
      className={`onb${closing ? ' leaving' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label="AnsibleAI app guide"
    >
      <div className="onb-frame">
        <header className="onb-head">
          <div className="onb-head-brand">
            <span className="onb-head-logo">
              <CodeBracketsIcon size={15} />
            </span>
            <span className="onb-head-name">
              Ansible<em>AI</em>
            </span>
            <span className="onb-head-tag">App guide</span>
          </div>

          <ol className="onb-steps">
            {STEPS.map((s, i) => (
              <li key={s.id} className="onb-steps-li">
                {i > 0 ? (
                  <span className={`onb-steps-conn${i <= step ? ' lit' : ''}`} aria-hidden />
                ) : null}
                <button
                  type="button"
                  className={`onb-steps-btn${i === step ? ' current' : ''}${i < step ? ' done' : ''}`}
                  onClick={() => setStep(i)}
                  aria-current={i === step ? 'step' : undefined}
                >
                  <span className="onb-steps-num">{i < step ? '✓' : s.num}</span>
                  <span className="onb-steps-label">{s.label}</span>
                </button>
              </li>
            ))}
          </ol>

          <button
            type="button"
            className="onb-close"
            onClick={close}
            aria-label="Close the guide"
            title="Close (Esc)"
          >
            ✕
          </button>
        </header>

        <main className="onb-stage" key={STEPS[step].id}>
          {step === 0 ? <WelcomeStep /> : null}
          {step === 1 ? <LoopStep /> : null}
          {step === 2 ? <TourStep /> : null}
          {step === 3 ? <CraftStep onLaunch={close} /> : null}
        </main>

        <footer className="onb-nav">
          <span className="onb-nav-count">
            {STEPS[step].num} <i>/</i> {STEPS[STEPS.length - 1].num}
          </span>
          <div className="onb-nav-dots" aria-hidden>
            {STEPS.map((s, i) => (
              <span
                key={s.id}
                className={`onb-nav-dot${i === step ? ' active' : ''}${i < step ? ' done' : ''}`}
              />
            ))}
          </div>
          <div className="onb-nav-btns">
            <button type="button" className="onb-btn-ghost" onClick={close}>
              Skip the tour
            </button>
            {step > 0 ? (
              <button type="button" className="onb-btn-ghost" onClick={() => setStep(step - 1)}>
                Back
              </button>
            ) : null}
            <button
              type="button"
              className="onb-btn-primary"
              onClick={() => (last ? close() : setStep(step + 1))}
            >
              {last ? 'Enter the workspace' : 'Continue'}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
