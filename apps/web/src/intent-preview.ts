import { apiFetch } from './auth-client.js';
import { parseOrderDraft, type OrderDraft } from './drafts.js';

export interface IntentPreview {
  operation?: 'revise'; target?: {draft_id: string; revision: number}; before_fields?: IntentFormFields;
  origin?: 'semantic_proposal'; source_text?: string; unmapped_text?: string;
  schema_version: 1; pending_id: string; state_version: number;
  status: 'pending' | 'ready' | 'expired' | 'needs_recheck'; expires_at: string;
  can_confirm: boolean; clarification_attempts: number; message: string;
  customer: string; note: string | null; budget_limit: number | null;
  items: {item_number: number; product_query: string; sku: string | null; name: string | null;
    quantity: number | null; candidates: {sku: string; name: string; status: string}[]}[];
}
export interface IntentFormFields {
  customer: string; note: string | null; budget_limit: number | null;
  items: {sku: string; quantity: number}[];
}
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const text = (v: unknown): v is string => typeof v === 'string' && !!v.trim();
const sku = (v: unknown): v is string => text(v) && v.length <= 32;
export function parseIntentPreview(value: unknown): IntentPreview | null {
  if (value === null) return null;
  const invalid = () => new Error('待完成请求核对信息无效，请刷新后重试');
  if (!record(value) || value.schema_version !== 1 || typeof value.pending_id !== 'string' || !/^[0-9a-f]{32}$/.test(value.pending_id) || !integer(value.state_version) || value.state_version < 1 || !['pending','ready','expired','needs_recheck'].includes(String(value.status)) || typeof value.expires_at !== 'string' || !Number.isFinite(Date.parse(value.expires_at)) || typeof value.can_confirm !== 'boolean' || (value.can_confirm && !['pending','ready'].includes(String(value.status))) || !integer(value.clarification_attempts) || typeof value.message !== 'string' || typeof value.customer !== 'string' || !(value.note === null || typeof value.note === 'string') || !(value.budget_limit === null || typeof value.budget_limit === 'number' && Number.isFinite(value.budget_limit) && value.budget_limit > 0 && value.budget_limit <= 1e12) || !Array.isArray(value.items) || value.items.length > 100 || (value.can_confirm && !value.items.length)) throw invalid();
  if (value.origin !== undefined && (value.origin !== 'semantic_proposal' || !text(value.source_text) || value.source_text.length > 32000 || typeof value.unmapped_text !== 'string' || value.unmapped_text.length > 32000)) throw invalid();
  if (value.operation !== undefined) {
    const before = value.before_fields, target = value.target;
    if (value.operation !== 'revise' || value.origin !== 'semantic_proposal' || !record(target) || typeof target.draft_id !== 'string' || !/^[0-9a-f]{32}$/.test(target.draft_id) || !integer(target.revision) || target.revision < 1 || !record(before) || !text(before.customer) || !(before.note === null || typeof before.note === 'string') || !(before.budget_limit === null || typeof before.budget_limit === 'number' && Number.isFinite(before.budget_limit) && before.budget_limit > 0) || !Array.isArray(before.items) || !before.items.length || before.items.length > 100 || before.items.some(r => !record(r) || !sku(r.sku) || !integer(r.quantity) || r.quantity < 1) || new Set(before.items.map(r => (r as {sku: string}).sku)).size !== before.items.length) throw invalid();
  } else if (value.target !== undefined || value.before_fields !== undefined) throw invalid();
  for (const [index, row] of value.items.entries()) {
    if (!record(row) || row.item_number !== index + 1 || !text(row.product_query) || !(row.sku === null || sku(row.sku)) || !(row.name === null || text(row.name)) || !(row.quantity === null || integer(row.quantity) && row.quantity > 0) || !Array.isArray(row.candidates) || row.candidates.length > 20 || row.candidates.some(c => !record(c) || !sku(c.sku) || !text(c.name) || !text(c.status)) || new Set(row.candidates.map(c => (c as {sku: string}).sku)).size !== row.candidates.length) throw invalid();
  }
  return value as unknown as IntentPreview;
}
async function request(url: string, options: RequestInit) {
  const response = await apiFetch(url, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = record(data) && record(data.error) ? data.error : {};
    throw Object.assign(new Error([detail.message ?? `HTTP ${response.status}`, detail.hint].filter(Boolean).join('；')), {status: response.status, code: detail.code});
  }
  return data;
}
const base = (sessionId: string) => `/api/sessions/${encodeURIComponent(sessionId)}/pending-intent`;
export async function getIntentPreview(sessionId: string, signal?: AbortSignal) {
  const data = await request(`${base(sessionId)}/preview`, {signal});
  if (!record(data) || !('preview' in data)) throw new Error('待完成请求核对信息缺失');
  return parseIntentPreview(data.preview);
}
export async function confirmIntentPreview(sessionId: string, preview: IntentPreview, fields: IntentFormFields): Promise<OrderDraft> {
  if (!preview.can_confirm || Date.parse(preview.expires_at) <= Date.now()) throw new Error('请求已变化或过期，请重新核对');
  const saved = parseOrderDraft(await request(`${base(sessionId)}/confirm`, {
    method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({
      customer: fields.customer, note: fields.note, budget_limit: fields.budget_limit,
      items: fields.items, pending_ref: {pending_id: preview.pending_id, state_version: preview.state_version},
      ...(preview.operation === 'revise' ? {target: preview.target} : {}),
    }),
  }));
  if (saved.revision !== (preview.operation === 'revise' ? Number(preview.target?.revision) + 1 : 1) || (preview.operation === 'revise' && saved.draft_id !== preview.target?.draft_id) || saved.customer !== fields.customer || saved.note !== fields.note || (saved.budget_limit ?? null) !== fields.budget_limit || JSON.stringify(saved.items.map(r => ({sku: r.sku, quantity: r.quantity}))) !== JSON.stringify(fields.items)) throw new Error('保存结果与核对清单不一致，请刷新草稿后核对');
  return saved;
}

export async function requestIntentRevisionPreview(sessionId: string, draft: OrderDraft, source: string, requestId: string) {
  const data = await request(`${base(sessionId)}/revise-preview`, {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({target: {draft_id: draft.draft_id, revision: draft.revision}, source, request_id: requestId})});
  if (!record(data)) throw new Error('修改候选信息缺失');
  const preview = parseIntentPreview(data.preview);
  if (!preview || !preview.can_confirm || preview.operation !== 'revise' || preview.target?.draft_id !== draft.draft_id || preview.target.revision !== draft.revision || preview.source_text !== source) throw new Error('修改候选与目标草稿不一致，请刷新后重新核对');
  return preview;
}
