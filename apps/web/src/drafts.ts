export interface DraftLine { sku: string; name: string; quantity: number; base_price: number; discount: number; unit_price: number; line_total: number; stock_before: number; stock_after: number }
export interface BudgetConstraint { currency: 'CNY'; limit: number | null; origin: {kind: 'user_literal' | 'manual_form' | 'unattributed' | 'not_set'; revision: number; quote?: string; source_user_sha256?: string} }
export interface DraftIssue {code: string; message: string; required_action: string; revision?: number; blocking?: boolean; sku?: string}
export interface DraftSourceSpan {message_id: string; start: number; end: number; quote: string}
export interface DraftItemsConstraint {value: {sku: string; quantity: number}[]; verified: boolean; origin: {kind: 'user_literal' | 'catalog_resolved' | 'intent_verified' | 'manual_form' | 'unverified'; revision: number; source_user_sha256?: string; requirements?: {sku: string; quantity: number; quote?: string; product_query?: string; product_name?: string; evidence_refs?: DraftSourceSpan[]}[]; previous_revision?: number; contract?: string; source_messages?: {message_id: string; sha256: string}[]}}
export interface OrderDraft { draft_id: string; revision: number; customer: string; note: string | null; items: DraftLine[]; total_amount: number; expires_at: string; status: 'open' | 'expired' | 'superseded' | 'submitted'; order_id: string | null; budget_limit?: number | null; budget_constraint?: BudgetConstraint; budget_check?: {currency: 'CNY'; limit: number | null; total_amount: number; over_amount: number; status: 'not_set' | 'within_budget' | 'over_budget'}; pending_issues?: DraftIssue[]; submission_check?: {schema_version: 1; revision: number; latest_revision: number; checked_at: string; can_submit: boolean}; item_constraint?: DraftItemsConstraint }
export interface DraftInput { customer: string; items: {sku: string; quantity: number}[]; note?: string; budget_limit?: number | null }
export function draftBudgetIssue(draft: OrderDraft): string | null {
  if (draft.budget_limit == null) return null;
  const excess = Math.round(draft.total_amount * 100) - Math.round(draft.budget_limit * 100);
  return excess > 0 ? `当前报价超过预算 ¥${(excess / 100).toFixed(2)}，请明确调整预算或商品后保存新版本。` : null;
}
export function draftSubmissionIssue(draft: OrderDraft, now = Date.now()): string | null {
  const issue = draft.pending_issues?.find(item => item.blocking !== false);
  if (issue) return `${issue.message} ${issue.required_action}`;
  if (draft.status !== 'open') return '草稿已过期、已提交或已有新版本，请重新核对。';
  if (Date.parse(draft.expires_at) <= now) return '此版本报价已过期，请明确刷新报价后重新核对和审批。';
  if (draft.submission_check?.can_submit === false) return '此版本暂不能提交，请刷新草稿并核对待确认事项。';
  return draftBudgetIssue(draft);
}
export function draftConfirmationText(draft: OrderDraft): string {
  return `已核对并确认草稿版本 ${draft.revision}（总金额 ¥${draft.total_amount.toFixed(2)}），申请人工审批。`;
}
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const number = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const integer = (v: unknown) => number(v) && Number.isSafeInteger(v);
export function parseOrderDraft(value: unknown): OrderDraft {
  if (!record(value) || typeof value.draft_id !== 'string' || !value.draft_id || !integer(value.revision) || Number(value.revision) < 1 || typeof value.customer !== 'string' || !(value.note === null || typeof value.note === 'string') || !number(value.total_amount) || typeof value.expires_at !== 'string' || !Number.isFinite(Date.parse(value.expires_at)) || !['open','expired','superseded','submitted'].includes(String(value.status)) || !(value.order_id === null || typeof value.order_id === 'string') || !Array.isArray(value.items) || !value.items.length) throw new Error('订单草稿格式无效');
  for (const line of value.items) {
    if (!record(line) || typeof line.sku !== 'string' || !line.sku || typeof line.name !== 'string' || !integer(line.quantity) || Number(line.quantity) < 1 || !['base_price','discount','unit_price','line_total'].every(key => number(line[key])) || !integer(line.stock_before) || !integer(line.stock_after)) throw new Error('订单草稿明细格式无效');
  }
  if (value.budget_limit != null && (!number(value.budget_limit) || Number(value.budget_limit) <= 0 || Number(value.budget_limit) > 1e12)) throw new Error('订单预算格式无效');
  if (value.budget_constraint !== undefined && !validBudgetConstraint(value.budget_constraint, Number(value.revision))) throw new Error('订单预算来源格式无效');
  if (value.budget_constraint !== undefined && (value.budget_constraint as BudgetConstraint).limit !== (value.budget_limit ?? null)) throw new Error('订单预算来源不一致');
  if (value.pending_issues !== undefined && (!Array.isArray(value.pending_issues) || value.pending_issues.length > 205 || value.pending_issues.some(issue => !record(issue) || typeof issue.code !== 'string' || !issue.code || typeof issue.message !== 'string' || !issue.message || typeof issue.required_action !== 'string' || !issue.required_action || (issue.revision !== undefined && issue.revision !== value.revision) || (issue.blocking !== undefined && typeof issue.blocking !== 'boolean') || (issue.sku !== undefined && typeof issue.sku !== 'string')))) throw new Error('待确认事项格式无效');
  if (value.submission_check !== undefined) {
    const check = value.submission_check;
    if (!record(check) || check.schema_version !== 1 || check.revision !== value.revision || !positiveInteger(check.latest_revision) || Number(check.latest_revision) < Number(value.revision) || typeof check.checked_at !== 'string' || !Number.isFinite(Date.parse(check.checked_at)) || typeof check.can_submit !== 'boolean' || !Array.isArray(value.pending_issues) || (check.can_submit && (value.status !== 'open' || check.latest_revision !== value.revision || value.pending_issues.some(issue => (issue as DraftIssue).blocking !== false)))) throw new Error('草稿提交检查格式无效');
  }
  if (value.item_constraint !== undefined && (!validItemConstraint(value.item_constraint, Number(value.revision)) || JSON.stringify((value.item_constraint as DraftItemsConstraint).value) !== JSON.stringify(value.items.map(line => ({sku: (line as DraftLine).sku, quantity: (line as DraftLine).quantity}))))) throw new Error('商品要求核验记录无效');
  const itemOrigin = (value.item_constraint as DraftItemsConstraint | undefined)?.origin;
  if (itemOrigin?.kind === 'catalog_resolved' && itemOrigin.requirements?.some(row => !(value.items as DraftLine[]).some(line => line.sku === row.sku && line.name === row.product_name))) throw new Error('商品名称核对记录不一致');
  return value as unknown as OrderDraft;
}
function base(sessionId: string) { return `/api/sessions/${encodeURIComponent(sessionId)}/order-drafts`; }
async function request(url: string, options?: RequestInit): Promise<unknown> {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = record(body) && record(body.error) ? body.error : {};
    throw Object.assign(new Error([detail.message ?? `HTTP ${response.status}`, detail.hint].filter(Boolean).join('；')), {status:response.status, code:detail.code});
  }
  return body;
}
export async function listOrderDrafts(sessionId: string, signal?: AbortSignal): Promise<OrderDraft[]> {
  const body = await request(base(sessionId), {signal});
  if (!record(body) || !Array.isArray(body.items)) throw new Error('订单草稿列表格式无效');
  return body.items.map(parseOrderDraft);
}
export async function getOrderDraft(sessionId: string, draftId: string, revision?: number, signal?: AbortSignal) {
  return parseOrderDraft(await request(`${base(sessionId)}/${encodeURIComponent(draftId)}${revision === undefined ? '' : `?revision=${revision}`}`, {signal}));
}
export async function createOrderDraft(sessionId: string, input: DraftInput) {
  return parseOrderDraft(await request(base(sessionId), {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(input)}));
}
export async function reviseOrderDraft(sessionId: string, draftId: string, revision: number, input: DraftInput) {
  return parseOrderDraft(await request(`${base(sessionId)}/${encodeURIComponent(draftId)}`, {method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({...input,expected_revision:revision})}));
}
export function approvalDraftIssue(pending: {arguments: Record<string, unknown>}, snapshot: OrderDraft | undefined, latest: OrderDraft[], now = Date.now()): string | null {
  if (!snapshot) return '正在核对审批明细；无法核对时请拒绝后重新提交。';
  if (snapshot.draft_id !== pending.arguments.draft_id || snapshot.revision !== pending.arguments.revision) return '审批版本不匹配，请拒绝后重新提交。';
  const current = latest.find(item => item.draft_id === snapshot.draft_id);
  if (!current) return '当前草稿未核对，请刷新或拒绝后重新提交。';
  if (current.revision !== snapshot.revision) return '草稿已修改，请拒绝旧审批后重新提交。';
  if (draftBudgetIssue(current)) return '当前草稿超出预算，请拒绝旧审批，明确调整后重新提交。';
  if (draftSubmissionIssue(current, now)) return `${draftSubmissionIssue(current, now)} 请拒绝旧审批后重新核对。`;
  if (current.status !== 'open' || Date.parse(snapshot.expires_at) <= now) return '草稿已过期或已提交，请拒绝旧审批后刷新。';
  return null;
}

export interface DraftConstraint {
  value: string | null; verified: boolean;
  origin: { kind: 'user_literal' | 'manual_form' | 'unverified' | 'legacy_snapshot'; revision: number; quote?: string; source_user_sha256?: string };
}
export interface DraftChange { field: string; sku?: string; before: unknown; after: unknown }
export interface DraftHistoryItem {
  draft_id: string; revision: number; created_at: string;
  task_constraints: { schema_version: 1; fields: { customer: DraftConstraint; note: DraftConstraint }; unverified_fields: string[] };
  item_constraint?: DraftItemsConstraint | null;
  budget_constraint?: BudgetConstraint | null;
  change_record: null | {
    schema_version: 1; revision: number; previous_revision: number | null;
    operation: 'create' | 'revise' | 'revise_quantity'; confirmed_fields: string[];
    source: { kind: 'agent' | 'manual_form' | 'unattributed'; user_request?: string; source_user_sha256?: string };
    changes: DraftChange[];
  };
}
export interface DraftHistoryPage { schema_version: 1; draft_id: string; latest_revision: number; items: DraftHistoryItem[]; has_more: boolean; next_before_revision: number | null }
const positiveInteger = (v: unknown) => integer(v) && Number(v) > 0;
const digest = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const changeLabels: Record<string, string> = {
  customer: '客户', note: '备注', budget_limit: '预算上限', total_amount: '报价总额', item: '商品明细', item_order: '商品顺序',
  name: '商品名称', quantity: '数量', base_price: '原价', discount: '折扣', unit_price: '单价',
  line_total: '小计', stock_before: '报价时库存', stock_after: '预计提交后库存',
};
function validHistoryLine(value: unknown, sku: string): boolean {
  return record(value) && value.sku === sku && typeof value.name === 'string' &&
    positiveInteger(value.quantity) && ['base_price', 'discount', 'unit_price', 'line_total'].every(key => number(value[key])) &&
    integer(value.stock_before) && integer(value.stock_after);
}
function validHistoryChange(value: unknown): boolean {
  if (!record(value) || typeof value.field !== 'string' || !Object.hasOwn(changeLabels, value.field) || !Object.hasOwn(value, 'before') || !Object.hasOwn(value, 'after')) return false;
  const pair = [value.before, value.after];
  if (['customer', 'note'].includes(value.field)) return value.sku === undefined && pair.every(v => v === null || typeof v === 'string');
  if (['total_amount', 'budget_limit'].includes(value.field)) return value.sku === undefined && pair.every(v => v === null || number(v));
  if (value.field === 'item_order') return value.sku === undefined && pair.every(v => Array.isArray(v) && v.length <= 100 && v.every(sku => typeof sku === 'string' && !!sku) && new Set(v).size === v.length);
  if (typeof value.sku !== 'string' || !value.sku) return false;
  if (value.field === 'item') return pair.some(v => v !== null) && pair.every(v => v === null || validHistoryLine(v, value.sku as string));
  if (value.field === 'name') return pair.every(v => typeof v === 'string');
  if (value.field === 'quantity') return pair.every(positiveInteger);
  if (['stock_before', 'stock_after'].includes(value.field)) return pair.every(integer);
  return pair.every(number);
}
function validConstraint(value: unknown, revision: number, nullable: boolean): boolean {
  if (!record(value) || !(typeof value.value === 'string' || (nullable && value.value === null)) || typeof value.verified !== 'boolean' || !record(value.origin)) return false;
  const origin = value.origin;
  if (!positiveInteger(origin.revision) || Number(origin.revision) > revision || !['user_literal', 'manual_form', 'unverified', 'legacy_snapshot'].includes(String(origin.kind))) return false;
  if (value.verified !== ['user_literal', 'manual_form'].includes(String(origin.kind))) return false;
  return origin.kind !== 'user_literal' || (typeof origin.quote === 'string' && !!origin.quote && digest(origin.source_user_sha256));
}
function validBudgetConstraint(value: unknown, revision: number): boolean {
  if (!record(value) || value.currency !== 'CNY' || !(value.limit === null || (number(value.limit) && Number(value.limit) > 0 && Number(value.limit) <= 1e12)) || !record(value.origin)) return false;
  const origin = value.origin;
  return positiveInteger(origin.revision) && Number(origin.revision) <= revision && ['user_literal','manual_form','unattributed','not_set'].includes(String(origin.kind)) && (origin.kind !== 'user_literal' || (typeof origin.quote === 'string' && !!origin.quote && digest(origin.source_user_sha256)));
}
function validItemConstraint(value: unknown, revision: number): boolean {
  if (!record(value) || typeof value.verified !== 'boolean' || !record(value.origin) || !positiveInteger(value.origin.revision) || Number(value.origin.revision) > revision || !['user_literal', 'catalog_resolved', 'intent_verified', 'manual_form', 'unverified'].includes(String(value.origin.kind)) || value.verified !== (value.origin.kind !== 'unverified') || !Array.isArray(value.value) || !value.value.length || value.value.length > 100) return false;
  const items = value.value;
  if (items.some(row => !record(row) || typeof row.sku !== 'string' || !row.sku || !positiveInteger(row.quantity)) || new Set(items.map(row => row.sku)).size !== items.length) return false;
  if (value.origin.kind === 'intent_verified') return validIntentItems(value.origin, items);
  if (value.origin.kind === 'user_literal' || value.origin.kind === 'catalog_resolved') {
    const requirements = value.origin.requirements;
    if (!digest(value.origin.source_user_sha256) || !Array.isArray(requirements) || !requirements.length || requirements.length > 100 || requirements.some(row => !record(row) || typeof row.quote !== 'string' || !row.quote || !items.some(line => line.sku === row.sku && line.quantity === row.quantity)) || new Set(requirements.map(row => row.sku)).size !== requirements.length) return false;
    if (value.origin.kind === 'catalog_resolved' && (requirements.length !== items.length || requirements.some(row => typeof row.product_query !== 'string' || !row.product_query || typeof row.product_name !== 'string' || !row.product_name))) return false;
  }
  return true;
}
function validIntentItems(origin: Record<string, unknown>, items: {sku: unknown; quantity: unknown}[]): boolean {
  const sources = origin.source_messages, requirements = origin.requirements;
  if (origin.contract !== 'draft-intent-2' || !Array.isArray(sources) || !sources.length || sources.length > 102 || sources.some(s => !record(s) || typeof s.message_id !== 'string' || !s.message_id || !digest(s.sha256)) || new Set(sources.map(s => s.message_id)).size !== sources.length) return false;
  const ids = new Set(sources.map(s => s.message_id));
  if (!Array.isArray(requirements) || !requirements.length || requirements.length > 100 || new Set(requirements.map(r => record(r) ? r.sku : undefined)).size !== requirements.length) return false;
  if (origin.previous_revision === undefined ? requirements.length !== items.length : !positiveInteger(origin.previous_revision) || origin.previous_revision !== Number(origin.revision) - 1) return false;
  return requirements.every(r => record(r) && items.some(i => i.sku === r.sku && i.quantity === r.quantity) && Array.isArray(r.evidence_refs) && r.evidence_refs.length > 0 && r.evidence_refs.length <= 204 && r.evidence_refs.every(ref => record(ref) && ids.has(ref.message_id) && integer(ref.start) && integer(ref.end) && Number(ref.start) < Number(ref.end) && Number(ref.end) <= 32000 && typeof ref.quote === 'string' && Array.from(ref.quote).length === Number(ref.end) - Number(ref.start)));
}
export function parseDraftHistory(value: unknown): DraftHistoryPage {
  const invalid = () => new Error('草稿修改记录格式无效');
  if (!record(value) || value.schema_version !== 1 || typeof value.draft_id !== 'string' || !value.draft_id || !positiveInteger(value.latest_revision) || !Array.isArray(value.items) || value.items.length > 20 || typeof value.has_more !== 'boolean') throw invalid();
  let previous = Number(value.latest_revision) + 1;
  for (const item of value.items) {
    if (!record(item) || item.draft_id !== value.draft_id || !positiveInteger(item.revision) || Number(item.revision) >= previous || typeof item.created_at !== 'string' || !Number.isFinite(Date.parse(item.created_at))) throw invalid();
    previous = Number(item.revision);
    if (item.budget_constraint != null && !validBudgetConstraint(item.budget_constraint, previous)) throw invalid();
    const ledger = item.task_constraints;
    if (!record(ledger) || ledger.schema_version !== 1 || !record(ledger.fields) || !validConstraint(ledger.fields.customer, previous, false) || !validConstraint(ledger.fields.note, previous, true) || !Array.isArray(ledger.unverified_fields) || ledger.unverified_fields.some(field => !['customer', 'note', 'items'].includes(String(field)))) throw invalid();
    if (item.item_constraint != null && (!validItemConstraint(item.item_constraint, previous) || ledger.unverified_fields.includes('items') === (item.item_constraint as DraftItemsConstraint).verified)) throw invalid();
    for (const field of ['customer', 'note']) {
      const constraint = ledger.fields[field] as unknown as DraftConstraint;
      if (ledger.unverified_fields.includes(field) === constraint.verified) throw invalid();
    }
    const change = item.change_record;
    if (change === null) continue;
    if (!record(change) || change.schema_version !== 1 || change.revision !== item.revision || change.previous_revision !== (previous === 1 ? null : previous - 1) || !['create', 'revise', 'revise_quantity'].includes(String(change.operation)) || !record(change.source) || !['agent', 'manual_form', 'unattributed'].includes(String(change.source.kind)) || !Array.isArray(change.confirmed_fields) || change.confirmed_fields.some(field => !['customer', 'note', 'budget_limit'].includes(String(field))) || !Array.isArray(change.changes)) throw invalid();
    if (change.source.kind === 'agent' && (typeof change.source.user_request !== 'string' || !digest(change.source.source_user_sha256))) throw invalid();
    for (const diff of change.changes) {
      if (!validHistoryChange(diff)) throw invalid();
    }
  }
  const cursor = value.next_before_revision;
  if (value.has_more ? (!value.items.length || cursor !== previous) : cursor !== null) throw invalid();
  return value as unknown as DraftHistoryPage;
}
export async function getOrderDraftHistory(sessionId: string, draftId: string, beforeRevision?: number, signal?: AbortSignal): Promise<DraftHistoryPage> {
  if (beforeRevision !== undefined && !positiveInteger(beforeRevision)) throw new Error('草稿版本游标无效');
  const suffix = beforeRevision === undefined ? '' : `&before_revision=${beforeRevision}`;
  const page = parseDraftHistory(await request(`${base(sessionId)}/${encodeURIComponent(draftId)}/history?limit=5${suffix}`, {signal}));
  if (page.draft_id !== draftId || (beforeRevision !== undefined && page.items.some(item => item.revision >= beforeRevision))) throw new Error('草稿修改记录与当前请求不匹配');
  return page;
}
function historyValue(value: unknown): string {
  if (value === null) return '无';
  if (typeof value === 'string') return value || '空文本';
  if (record(value)) return `${value.name ?? value.sku ?? '商品'} × ${value.quantity ?? '?'}`;
  if (Array.isArray(value)) return value.join('、');
  return String(value);
}
export function describeDraftChange(change: DraftChange): string {
  const label = changeLabels[change.field] ?? '变更';
  return `${change.sku ? change.sku + ' · ' : ''}${label}：${historyValue(change.before)} → ${historyValue(change.after)}`;
}
