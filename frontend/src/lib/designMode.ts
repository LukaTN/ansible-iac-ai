/**
 * Frontend-only Design Mode. Compile-time flag from Vite (`VITE_DESIGN_MODE`).
 * Production builds (`vite build`) leave this unset, so the inspector and
 * mocks are never activated for the Flask-served SPA.
 */
/**
 * Frontend-only Design Mode. Compile-time flag from Vite (`VITE_DESIGN_MODE`).
 * Production builds (`vite build`) leave this unset, so the inspector and
 * mocks are never activated for the Flask-served SPA.
 */
export function isDesignMode(): boolean {
  return import.meta.env.VITE_DESIGN_MODE === 'true';
}

