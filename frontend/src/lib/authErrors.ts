import { ApiError } from './api';

/**
 * Map API auth failures to short, actionable copy for the login /
 * register / password-change screens. Prefer the machine `code` so the
 * UI stays stable even if the server wording changes.
 */
const BY_CODE: Record<string, string> = {
  csrf:
    'Your session security token expired. Please try again — if it keeps failing, refresh the page.',
  invalid_credentials: 'Email or password is incorrect. Check both and try again.',
  account_locked:
    'Too many failed sign-in attempts. This account is temporarily locked — wait a few minutes, then try again.',
  account_inactive:
    'This account is not active yet. Ask an administrator to enable it, then sign in.',
  registration_disabled:
    'New accounts cannot be created right now. Ask an administrator for access.',
  weak_password:
    'That password does not meet the security requirements. Choose a longer, less common passphrase.',
  rate_limited: 'Too many attempts from this device. Wait a minute, then try again.',
  unauthenticated: 'Please sign in to continue.',
  forbidden: 'You do not have permission to do that.',
  wrong_current_password: 'Current password is incorrect.',
  password_reuse: 'New password must be different from your current one.',
  missing_fields: 'Fill in every required field and try again.',
  invalid_email: 'That does not look like a valid email address.',
  account_not_found: 'Account not found. Sign in again.',
};

const BY_STATUS: Record<number, string> = {
  400: 'The request was rejected. Check your details and try again.',
  401: 'Email or password is incorrect. Check both and try again.',
  403: 'You are not allowed to do that.',
  404: 'We could not find what you asked for.',
  423: 'This account is temporarily locked. Wait a few minutes, then try again.',
  429: 'Too many attempts. Wait a minute, then try again.',
  500: 'Something went wrong on the server. Try again in a moment.',
  502: 'The server is unreachable. Check that it is running and try again.',
  503: 'The service is temporarily unavailable. Try again shortly.',
};

/** Server messages that are already clear enough to show as-is. */
function isUserFacingServerMessage(message: string): boolean {
  if (!message || message.startsWith('HTTP ')) return false;
  // Raw CSRF / framework noise — never show these verbatim.
  if (/csrf/i.test(message)) return false;
  if (/werkzeug|traceback|internal server/i.test(message)) return false;
  return message.length >= 8 && message.length <= 280;
}

export function formatAuthError(err: unknown, fallback?: string): string {
  if (!(err instanceof ApiError)) {
    return fallback ?? 'Could not reach the server. Check that it is running and try again.';
  }

  if (err.code && BY_CODE[err.code]) {
    // Prefer the server message when it already explains a policy (e.g. weak
    // password / domain allowlist), otherwise use the mapped copy.
    if (
      (err.code === 'weak_password' || err.code === 'registration_disabled') &&
      isUserFacingServerMessage(err.message)
    ) {
      return err.message;
    }
    if (err.code === 'account_locked' && isUserFacingServerMessage(err.message)) {
      return err.message;
    }
    return BY_CODE[err.code];
  }

  if (isUserFacingServerMessage(err.message)) {
    return err.message;
  }

  return BY_STATUS[err.status] ?? fallback ?? 'Something went wrong. Please try again.';
}
