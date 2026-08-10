import type { ValidationResult } from '@/lib/types';

interface ValidationCardProps {
  validation: ValidationResult;
}

function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*m/g, '').replace(/\[[0-9;]*m/g, '').trim();
}

function humanizeCheck(text: string): string {
  if (/ansible-lint unavailable/i.test(text) || /wsl_not_configured/i.test(text)) {
    return 'ansible-lint skipped on Windows — set up WSL or Docker (see README)';
  }
  if (/ansible-lint not found inside wsl/i.test(text)) {
    return 'ansible-lint not installed in WSL — run: pip install ansible-lint';
  }
  const missing = text.match(/Missing required params:\s*\[(.*)\]/i);
  if (missing) {
    const params = missing[1]
      .split(',')
      .map((p) => p.trim().replace(/^['"]|['"]$/g, ''))
      .filter(Boolean);
    if (params.length) {
      return `Missing required parameter${params.length > 1 ? 's' : ''}: ${params.join(', ')}`;
    }
  }
  return text;
}

type LintCls = 'passed' | 'failed' | 'skipped' | 'unavailable';

function lintClassify(status: string | undefined): LintCls {
  if (!status || status === 'not_run') return 'unavailable';
  if (status === 'passed') return 'passed';
  if (status === 'violations') return 'failed';
  if (['skipped', 'timeout', 'unsupported_platform', 'wsl_not_configured',
       'docker_not_available', 'not_installed', 'failed_to_run'].includes(status)) {
    return 'skipped';
  }
  return 'unavailable';
}

const LINT_LABELS: Record<LintCls, string> = {
  passed: 'Passed',
  failed: 'Violations',
  skipped: 'Skipped',
  unavailable: 'Not run',
};

const LINT_ICONS: Record<LintCls, string> = {
  passed: '✓',
  failed: '✕',
  skipped: '—',
  unavailable: '?',
};

function AnsibleLintBadge({ validation }: { validation: ValidationResult }) {
  const lint = validation.ansible_lint;
  if (!lint) return null;

  const cls = lintClassify(lint.status);
  const violations = (lint.violations || []).map(stripAnsi).filter((l) => l.length > 0);
  const backend = lint.backend && lint.backend !== 'none' ? lint.backend : null;

  return (
    <div className="val-lint-badge-section">
      <div className="val-lint-badge-row">
        <div className={`val-lint-badge lint-${cls}`}>
          <span className="val-lint-badge-icon" aria-hidden>{LINT_ICONS[cls]}</span>
          <span className="val-lint-badge-label">ansible-lint</span>
          <span className="val-lint-badge-status">{LINT_LABELS[cls]}</span>
          {backend && <span className="val-lint-badge-backend">{backend}</span>}
        </div>
      </div>

      {cls === 'failed' && violations.length > 0 && (
        <details className="val-lint-violations" open={violations.length <= 5}>
          <summary>
            {violations.length} violation{violations.length !== 1 ? 's' : ''} found
          </summary>
          <pre className="val-lint-output">{violations.join('\n')}</pre>
        </details>
      )}

      {cls === 'skipped' && lint.message && (
        <p className="val-lint-skip-note">{lint.message}</p>
      )}
    </div>
  );
}

function GateSummaryRow({ validation }: { validation: ValidationResult }) {
  const passed = validation.passed_msgs?.length ?? validation.passed ?? 0;
  const warnings = validation.warnings?.length ?? 0;
  const errors = validation.errors?.length ?? 0;

  return (
    <div className="val-gate-summary">
      <span className="val-gate-chip gate-passed" title="Checks passed">
        <span aria-hidden>✓</span> {passed}
      </span>
      {warnings > 0 && (
        <span className="val-gate-chip gate-warn" title="Warnings">
          <span aria-hidden>!</span> {warnings}
        </span>
      )}
      {errors > 0 && (
        <span className="val-gate-chip gate-err" title="Errors">
          <span aria-hidden>✕</span> {errors}
        </span>
      )}
    </div>
  );
}

export function ValidationCard({ validation }: ValidationCardProps) {
  const errors = (validation.errors || []).map(humanizeCheck);
  const warnings = (validation.warnings || []).map(humanizeCheck);
  const passed = validation.passed_msgs || [];
  const hasLint = !!validation.ansible_lint;

  let cls: 'ok' | 'warn' | 'bad';
  let label: string;

  if (errors.length) {
    cls = 'bad';
    label = `Invalid · ${errors.length} error${errors.length > 1 ? 's' : ''}`;
  } else if (warnings.length) {
    cls = 'warn';
    label = `Warnings · ${warnings.length}`;
  } else {
    cls = 'ok';
    label = 'Valid';
  }

  return (
    <div className="val-card-inline">
      <div className="val-card-hdr">
        <span className="val-card-title">Production Gate</span>
        <div className="val-card-hdr-right">
          <GateSummaryRow validation={validation} />
          <div className={`val-status ${cls}`}>
            <span className="val-icon" aria-hidden>
              {cls === 'ok' ? '✓' : cls === 'warn' ? '!' : '✕'}
            </span>
            <span>{label}</span>
          </div>
        </div>
      </div>

      {hasLint && <AnsibleLintBadge validation={validation} />}

      {errors.length > 0 && (
        <div className="val-section val-section-err">
          <div className="val-section-title">Fix these</div>
          <ul className="val-issue-list">
            {errors.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="val-section val-section-warn">
          <div className="val-section-title">Warnings</div>
          <ul className="val-issue-list">
            {warnings.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      {passed.length > 0 && (
        <details className="val-passed-details" open={errors.length === 0 && passed.length <= 3}>
          <summary>
            {passed.length} check{passed.length > 1 ? 's' : ''} passed
          </summary>
          <ul className="val-passed-list">
            {passed.map((t, i) => (
              <li key={i}>
                <span className="val-passed-mark" aria-hidden>
                  ✓
                </span>
                <span>{t}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
