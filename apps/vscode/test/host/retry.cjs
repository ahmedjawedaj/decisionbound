'use strict';
/** Retry and diagnostics for the connected VS Code host runner.
 * Only the lookup and download of the host is retried. The test run itself is started once,
 * because @vscode/test-electron rejects when the tests fail and a retry would repeat them.
 */

function label(value) {
  try {
    if (value === null || value === undefined) return String(value);
    if (typeof value !== 'object') return String(value);
    const name = value.name || 'Error';
    const code = value.code ? ` code=${value.code}` : '';
    const message = typeof value.message === 'string' ? value.message : '';
    return `${name}${code}${message ? ': ' + message : ' (empty message)'}`;
  } catch (_) {
    return 'unprintable error';
  }
}

/** Describe an error with its cause chain and the errors of an AggregateError (a failed
 * connection to several addresses has an empty message and only lists them in `errors`). */
function describe(error) {
  const parts = [];
  const seen = new Set();
  const queue = [error];
  while (queue.length && parts.length < 8) {
    const current = queue.shift();
    if (current !== null && typeof current === 'object') {
      if (seen.has(current)) continue;
      seen.add(current);
    }
    parts.push(label(current));
    try {
      if (current !== null && typeof current === 'object') {
        if (current.cause) queue.push(current.cause);
        if (Array.isArray(current.errors)) queue.push(...current.errors.slice(0, 4));
      }
    } catch (_) { /* a hostile getter must not hide the original error */ }
  }
  return parts.join(' <- ');
}

async function runWithRetry(task, {attempts = 3, delayMs = 5000, sleep = ms => new Promise(r => setTimeout(r, ms)), log = () => {}} = {}) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await task(attempt);
    } catch (error) {
      lastError = error;
      log(`DBOUND_HOST_ATTEMPT ${attempt}/${attempts} failed: ${describe(error)}\n`);
      if (attempt < attempts) await sleep(delayMs * attempt);
    }
  }
  throw lastError;
}

module.exports = {describe, runWithRetry};
