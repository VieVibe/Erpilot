import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { Alert, Button, Card, Form, Input, Space, Spin, Tag } from 'antd';
import { apiFetch, createOwnedSession, sessionStorageKeys, setCsrfToken, type AuthUser } from './auth-client.js';
import './auth.css';

const IdentityContext = createContext<AuthUser | null>(null);
export function useIdentity() { return useContext(IdentityContext); }

export function AuthBoundary({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function accept(result: { user: AuthUser; csrf_token: string }) {
    const value = result.user;
    if (!value || typeof value.user_id !== 'string' || !/^[a-f0-9]{32}$/.test(value.user_id) ||
        typeof value.username !== 'string' || typeof result.csrf_token !== 'string' || !result.csrf_token ||
        !['operator', 'approver'].includes(value.role)) throw new Error('登录响应无效');
    setCsrfToken(result.csrf_token);
    const keys = sessionStorageKeys(value);
    if (!localStorage.getItem(keys.current)) {
      const sid = await createOwnedSession();
      localStorage.setItem(keys.current, sid);
      localStorage.setItem(keys.fresh, sid);
    }
    setUser(value); setReady(true);
  }

  async function initialize() {
    setError(''); setBusy(true);
    try {
      const status = await fetch('/api/runtime');
      if (status.status === 401) { setEnabled(true); return; }
      if (!status.ok) throw new Error('无法确认访问状态，请重试');
      const settings = await status.json();
      if (settings.auth_enabled !== undefined && typeof settings.auth_enabled !== 'boolean') {
        throw new Error('访问状态响应无效');
      }
      setEnabled(settings.auth_enabled === true);
      if (settings.auth_enabled === true) {
        const me = await fetch('/api/auth/me');
        if (me.ok) await accept(await me.json());
        else if (me.status !== 401) throw new Error('读取登录状态失败');
      }
    } catch (e) { setError(e instanceof Error ? e.message : '连接失败'); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    void initialize();
    const expired = () => { setCsrfToken(''); setUser(null); setReady(false); setError('登录已失效，请重新登录'); };
    window.addEventListener('erpilot-login-required', expired);
    return () => window.removeEventListener('erpilot-login-required', expired);
  }, []);

  async function login(fields: { username: string; password: string }) {
    setBusy(true); setError('');
    try {
      const response = await fetch('/api/auth/login', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error?.message ?? '登录失败');
      await accept(result);
    } catch (e) { setError(e instanceof Error ? e.message : '登录失败'); }
    finally { setBusy(false); }
  }

  async function logout() {
    setBusy(true);
    try {
      const result = await apiFetch('/api/auth/logout', { method: 'POST' });
      if (!result.ok && result.status !== 401) throw new Error('退出失败，请重试');
      setCsrfToken(''); setUser(null); setReady(false); setError('');
    } catch (e) { setError(e instanceof Error ? e.message : '退出失败'); }
    finally { setBusy(false); }
  }

  if (enabled === false) return <>{children}</>;
  if (enabled && user && ready) return <IdentityContext.Provider value={user}>
    <div className="authenticated-workspace">
      <div className="account-bar"><Space wrap><strong>{user.username}</strong>
        <Tag>{user.role === 'approver' ? '可审批自己的任务' : '查询与准备草稿'}</Tag>
        <Button size="small" onClick={() => void logout()} loading={busy}>退出登录</Button></Space>
        {error && <span role="alert">{error}</span>}</div>
      <div className="authenticated-content" key={user.user_id}>{children}</div>
    </div>
  </IdentityContext.Provider>;
  return <main className="login-page"><Card className="login-card" title="Erpilot · 掌柜助手">
    {enabled === null ? <><Spin spinning={busy}/>{error && <Alert type="error" title={error}/>}<Button onClick={() => void initialize()}>重新连接</Button></> : <>
      <p>登录后查看自己的会话、草稿与审批。访问账号由项目负责人创建。</p>
      {error && <Alert type="error" title={error} showIcon/>}
      <Form layout="vertical" onFinish={login} autoComplete="off">
        <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入账号' }]}>
          <Input aria-label="账号" autoComplete="username" maxLength={64}/>
        </Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
          <Input.Password aria-label="密码" autoComplete="current-password" maxLength={256}/>
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={busy}>登录工作台</Button>
      </Form>
    </>}
  </Card></main>;
}
