// Cookies authenticate; the in-memory synchronizer token authorizes browser mutations.
let csrfToken = '';
export type AuthUser = { user_id: string; username: string; role: 'operator' | 'approver' };
export function setCsrfToken(value: string) { csrfToken = value; }

export async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  if (!input.startsWith('/api/')) throw new Error('API 请求须使用本站接口');
  const issuedFor = csrfToken;
  const path = input;
  let options = init;
  if (csrfToken && path.startsWith('/api/') &&
      !['GET', 'HEAD'].includes((init?.method ?? 'GET').toUpperCase())) {
    const headers = new Headers(init?.headers);
    headers.set('X-CSRF-Token', csrfToken);
    options = { ...init, headers };
  }
  const response = await fetch(input, options);
  if (response.status === 401 && issuedFor === csrfToken && !path.startsWith('/api/auth/')) {
    if (typeof window !== 'undefined') window.dispatchEvent(new Event('erpilot-login-required'));
  }
  return response;
}

export async function createOwnedSession(): Promise<string> {
  const response = await apiFetch('/api/sessions', { method: 'POST' });
  if (!response.ok) throw new Error('创建会话失败，请重新登录后再试');
  const result = await response.json();
  if (typeof result.session_id !== 'string' || !/^[a-f0-9]{32}$/.test(result.session_id)) {
    throw new Error('服务返回了无效的会话');
  }
  return result.session_id;
}

export function sessionStorageKeys(user?: AuthUser | null) {
  const suffix = user ? `:${user.user_id}` : '';
  return { current: `erpilot-session-id${suffix}`, fresh: `erpilot-fresh-session-id${suffix}` };
}
