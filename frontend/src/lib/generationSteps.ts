import type { GenerationStep } from '@/lib/socket';

export interface PipelineStep {
  key: GenerationStep;
  label: string;
  icon: string;
  /** Short cue shown while this phase is active (keeps long waits informative). */
  hint: string;
}

export const GENERATION_PIPELINE: PipelineStep[] = [
  {
    key: 'planning',
    label: 'Understand',
    icon: '◎',
    hint: 'Reading your request and picking an approach…',
  },
  {
    key: 'retrieving',
    label: 'Search docs',
    icon: '⌕',
    hint: 'Querying indexed Ansible module docs…',
  },
  {
    key: 'generating',
    label: 'Write playbook',
    icon: '{',
    hint: 'Drafting YAML grounded on retrieved docs…',
  },
  {
    key: 'validating',
    label: 'Validate',
    icon: '✓',
    hint: 'Running schema checks and ansible-lint…',
  },
  {
    key: 'synthesizing',
    label: 'Compose reply',
    icon: '✦',
    hint: 'Packaging the playbook and explanation…',
  },
];

const STEP_ORDER = new Map(GENERATION_PIPELINE.map((s, i) => [s.key, i]));

export function stepIndex(step: GenerationStep): number {
  return STEP_ORDER.get(step) ?? 0;
}

export function stepLabel(step: GenerationStep): string {
  return GENERATION_PIPELINE.find((s) => s.key === step)?.label ?? 'Working';
}

export function stepHint(step: GenerationStep): string {
  return GENERATION_PIPELINE.find((s) => s.key === step)?.hint ?? 'Working on your request…';
}

/** Progress 0–1 for the pipeline bar (active step gets a soft mid-fill). */
export function pipelineProgress(step: GenerationStep): number {
  const idx = stepIndex(step);
  const n = GENERATION_PIPELINE.length;
  if (n <= 1) return 0.5;
  return Math.min(0.95, (idx + 0.45) / n);
}

export function formatElapsed(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m === 0) return `${s}s`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export const AMBIENT_THOUGHTS = [
  'Parsing your infrastructure request…',
  'Reviewing conversation context…',
  'Selecting Ansible collections to search…',
  'Preparing agent tool plan…',
  'Consulting indexed module documentation…',
] as const;
