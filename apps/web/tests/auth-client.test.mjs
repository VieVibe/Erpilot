import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { apiFetch, createOwnedSession, sessionStorageKeys, setCsrfToken } from '../.test-build/auth-client.js';

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
  setCsrfToken('');
});

test('CSRF is attached only to local mutations and preserves caller headers', async () => {
  setCsrfToken('test-synchronizer-token');
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return Response.json({}); };
  await apiFetch('/api/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
  assert.equal(calls[0][1].headers.get('X-CSRF-Token'), 'test-synchronizer-token');
  assert.equal(calls[0][1].headers.get('Content-Type'), 'application/json');
  await apiFetch('/api/sessions');
  assert.equal(calls[1][1], undefined);
  await assert.rejects(apiFetch('https://attacker.invalid/api/sessions', { method: 'POST' }), /本站/);
  await assert.rejects(apiFetch('//attacker.invalid/api/sessions', { method: 'POST' }), /本站/);
  assert.equal(calls.length, 2);
});

test('clearing identity removes its CSRF token from subsequent requests', async () => {
  setCsrfToken('previous-account-token');
  setCsrfToken('');
  globalThis.fetch = async (_url, options) => {
    assert.equal(new Headers(options?.headers).has('X-CSRF-Token'), false);
    return Response.json({});
  };
  await apiFetch('/api/auth/logout', { method: 'POST' });
});

test('expired protected requests notify the login boundary without recursive login errors', async () => {
  const events = [];
  globalThis.window = { dispatchEvent: event => events.push(event.type) };
  globalThis.fetch = async () => new Response('', { status: 401 });
  await apiFetch('/api/sessions');
  await apiFetch('/api/auth/login', { method: 'POST' });
  assert.deepEqual(events, ['erpilot-login-required']);
});

test('owned sessions accept only server-issued IDs and propagate failed creation', async () => {
  const sid = 'a'.repeat(32);
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/sessions');
    assert.equal(options.method, 'POST');
    assert.equal(options.body, undefined);
    return Response.json({ session_id: sid });
  };
  assert.equal(await createOwnedSession(), sid);
  for (const value of [null, '../other', 'client-chosen']) {
    globalThis.fetch = async () => Response.json({ session_id: value });
    await assert.rejects(createOwnedSession(), /无效/);
  }
  globalThis.fetch = async () => new Response('', { status: 401 });
  await assert.rejects(createOwnedSession(), /重新登录/);
});

test('a delayed 401 from the previous account cannot log out a newly accepted identity', async () => {
  const events = [];
  globalThis.window = { dispatchEvent: event => events.push(event.type) };
  let complete;
  globalThis.fetch = () => new Promise(resolve => { complete = resolve; });
  setCsrfToken('old-account');
  const old = apiFetch('/api/sessions');
  setCsrfToken('new-account');
  complete(new Response('', { status: 401 }));
  await old;
  assert.deepEqual(events, []);
});

test('account storage namespaces preserve each owner and the trusted-local legacy path', () => {
  assert.equal(sessionStorageKeys().current, 'erpilot-session-id');
  const alice = { user_id: 'a'.repeat(32), username: 'alice', role: 'approver' };
  const bob = { ...alice, user_id: 'b'.repeat(32) };
  assert.notEqual(sessionStorageKeys(alice).current, sessionStorageKeys(bob).current);
  assert.notEqual(sessionStorageKeys(alice).fresh, sessionStorageKeys(bob).fresh);
  assert.equal(sessionStorageKeys(alice).current, `erpilot-session-id:${alice.user_id}`);
});
