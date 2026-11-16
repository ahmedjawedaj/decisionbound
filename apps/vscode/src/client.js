'use strict';
/**
 * Security-reviewed local client transport. No VS Code dependency, so it is
 * directly contract-testable in Node with deterministic mocked HTTP replies.
 * Session and event objects come from the canonical DecisionBound SQLite API.
 */

const ID = /^prj_[0-9a-f]{32}$/;
const SESSION = /^wf_[0-9a-f]{32}$/;

/** @param {string} input */
function normalizeLoopbackUrl(input) {
  let url;
  try { url = new URL(input); } catch { throw new Error('Invalid local runtime URL'); }
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
      url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Only plain HTTP loopback root URLs without credentials are supported');
  }
  return url.origin;
}

/** @param {number} n @param {number} max */
function positiveLimit(n, max = 100) {
  if (!Number.isSafeInteger(n) || n < 1 || n > max) throw new Error('Invalid page limit');
  return n;
}

/** Local-only authenticated transport, never send credentials on redirects. */
class LocalControlClient {
  /**
   * @param {object} options
   * @param {string} options.baseUrl Reviewed 127.0.0.1/localhost/::1 URL.
   * @param {string} options.token Ephemeral in-memory copy of VS Code SecretStorage.
   * @param {typeof fetch} [options.fetchImpl] Injectable test transport.
   * @param {number} [options.timeoutMs] Hard timeout for each request.
   */
  constructor({ baseUrl, token, fetchImpl = globalThis.fetch, timeoutMs = 5000 }) {
    this.baseUrl = normalizeLoopbackUrl(baseUrl);
    if (typeof token !== 'string' || token.length < 16 || /\s/.test(token)) throw new Error('Invalid local pairing token');
    if (typeof fetchImpl !== 'function') throw new Error('No HTTP client available');
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 30000) throw new Error('Invalid request timeout');
    this.token = token;
    this.fetch = fetchImpl;
    this.timeoutMs = timeoutMs;
  }
  /** @param {string} path Local, prevalidated protocol path. */
  async request(path) {
    if (!(path.startsWith('/api/client/v1/') || path === '/api/setup/v1/status') ||
        path.includes('..') || /[\r\n]/.test(path)) {
      throw new Error('Unsafe local API path');
    }
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), this.timeoutMs);
    try {
      const response = await this.fetch(this.baseUrl + path, {
        method: 'GET', redirect: 'error', cache: 'no-store', signal: abort.signal,
        headers: {Authorization: 'Bearer ' + this.token, Accept: 'application/json'}
      });
      if (!response.ok) throw new Error('DecisionBound local API returned HTTP ' + response.status);
      const json = await response.json();
      if (json === null || typeof json !== 'object' || Array.isArray(json)) throw new Error('Invalid local API response');
      return json;
    } catch (error) {
      // Do not include raw HTTP bodies, request URLs, Authorization headers or
      // the token in error messages/log output, even for malicious transports.
      if (error.name === 'AbortError') throw new Error('DecisionBound local API request timed out');
      if (/^DecisionBound local API|^Invalid local API/.test(error.message)) throw error;
      throw new Error('DecisionBound local API is unavailable');
    } finally { clearTimeout(timer); }
  }
  /** Fail closed on unknown or incompatible protocol versions. */
  async capabilities() {
    const data = await this.request('/api/client/v1/capabilities');
    if (data.protocol !== 'decisionbound-local-client' || data.version !== 1 ||
        data.transport !== 'authenticated-loopback-http') throw new Error('Incompatible DecisionBound local runtime protocol');
    return data;
  }
  /** Return reviewed setup readiness without provider calls or secret values. */
  async setupStatus() {
    const value = await this.request('/api/setup/v1/status');
    if (typeof value.profile !== 'string' || typeof value.ready !== 'boolean' ||
        typeof value.integration_tested !== 'boolean' ||
        typeof value.next_action !== 'string' || !Array.isArray(value.checks)) {
      throw new Error('Invalid local setup report');
    }
    return value;
  }
  /** @param {string} projectId @param {number} [limit] @param {number|null} [before] */
  sessions(projectId, limit = 25, before = null) {
    if (!ID.test(projectId)) throw new Error('Invalid project ID');
    positiveLimit(limit);
    if (before !== null && (!Number.isSafeInteger(before) || before < 1)) throw new Error('Invalid session cursor');
    return this.request(`/api/client/v1/sessions?project_id=${projectId}&limit=${limit}` + (before === null ? '' : `&before=${before}`));
  }
  /** @param {string} projectId @param {string} sessionId */
  snapshot(projectId, sessionId) {
    if (!ID.test(projectId) || !SESSION.test(sessionId)) throw new Error('Invalid session or project ID');
    return this.request(`/api/client/v1/sessions/${sessionId}?project_id=${projectId}`);
  }
  /** Read current export approval and evidence metadata, never patch bytes. */
  review(projectId, sessionId) {
    if (!ID.test(projectId) || !SESSION.test(sessionId)) throw new Error('Invalid session or project ID');
    return this.request(`/api/client/v1/sessions/${sessionId}/review?project_id=${projectId}`);
  }
  /** @param {string} projectId @param {string} sessionId @param {number} [after] */
  events(projectId, sessionId, after = 0) {
    if (!ID.test(projectId) || !SESSION.test(sessionId) || !Number.isSafeInteger(after) || after < 0)
      throw new Error('Invalid session/event cursor');
    return this.request(`/api/client/v1/sessions/${sessionId}/events?project_id=${projectId}&after=${after}&limit=100`);
  }
  /** @param {string} projectId @param {number} [after] */
  changes(projectId, after = 0) {
    if (!ID.test(projectId) || !Number.isSafeInteger(after) || after < 0) throw new Error('Invalid project/event cursor');
    return this.request(`/api/client/v1/changes?project_id=${projectId}&after=${after}&limit=100`);
  }
}

/** Parse the local project manifest; the server still verifies registration. */
function parseProjectId(manifest) {
  const lines = String(manifest).split(/\r?\n/);
  const matched = lines.filter(line => /^project_id\s*:/.test(line));
  if (matched.length !== 1) throw new Error('Missing or duplicated DecisionBound project ID');
  const id = matched[0].split(':').slice(1).join(':').trim().replace(/^['"]|['"]$/g, '');
  if (!ID.test(id)) throw new Error('Invalid DecisionBound project ID');
  return id;
}

module.exports = {LocalControlClient, normalizeLoopbackUrl, parseProjectId};
