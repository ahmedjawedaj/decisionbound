'use strict';
/** Retry and diagnostics for the connected VS Code host runner.
 * Only the setup of the real host can be retried. A resolved run is never repeated,
 * whatever its exit code, so a failing extension test is never hidden by a retry.
 */

function describe(error) {
  const parts = [];
  for (let current = error, depth = 0; current && depth < 4; current = current.cause, depth += 1) {
    const text = current instanceof Error ? current.message : String(current);
    const code = current && current.code ? ` code=${current.code}` : '';
    const name = current && current.name ? current.name : 'Error';
    parts.push(`${name}${code}${text ? ': ' + text : ' (empty message)'}`);
  }
  return parts.length ? parts.join(' <- ') : 'unknown error';
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
