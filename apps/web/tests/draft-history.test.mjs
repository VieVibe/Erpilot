import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { describeDraftChange, getOrderDraftHistory, parseDraftHistory } from '../.test-build/drafts.js';

const origin = { kind: 'user_literal', revision: 1, quote: '客户名：完整客户甲', source_user_sha256: 'a'.repeat(64) };
const item = {
  draft_id: 'd1', revision: 2, created_at: '2026-10-21T10:00:00+00:00',
  task_constraints: { schema_version: 1, fields: {
    customer: {value: '完整客户甲', verified: true, origin},
    note: {value: null, verified: false, origin: {kind: 'unverified', revision: 1}},
  }, unverified_fields: ['note', 'items'] },
  change_record: {schema_version: 1, revision: 2, previous_revision: 1, operation: 'revise_quantity',
    confirmed_fields: [], source: {kind: 'agent', user_request: '改成5件', source_user_sha256: 'b'.repeat(64)},
    changes: [{field: 'quantity', sku: 'A', before: 2, after: 5}]},
};
const page = {schema_version: 1, draft_id: 'd1', latest_revision: 2, items: [item], has_more: true, next_before_revision: 2};
const originalFetch = globalThis.fetch;
afterEach(() => {globalThis.fetch = originalFetch;});

test('history validates version order, bound ownership, source labels and exclusive cursor', () => {
  assert.deepEqual(parseDraftHistory(page), page);
  const clone = () => structuredClone(page);
  const invalids = [];
  let invalid = clone(); invalid.items[0].draft_id = 'foreign'; invalids.push(invalid);
  invalid = clone(); invalid.items[0].revision = 3; invalids.push(invalid);
  invalid = clone(); invalid.items.push(structuredClone(item)); invalids.push(invalid);
  invalid = clone(); invalid.next_before_revision = 1; invalids.push(invalid);
  invalid = clone(); invalid.items[0].task_constraints.fields.customer.verified = false; invalids.push(invalid);
  invalid = clone(); invalid.items[0].task_constraints.fields.customer.origin.revision = 3; invalids.push(invalid);
  invalid = clone(); invalid.items[0].change_record.changes[0].after = NaN; invalids.push(invalid);
  invalid = clone(); invalid.items[0].change_record.changes[0].after = true; invalids.push(invalid);
  invalid = clone(); invalid.items[0].change_record.changes[0].sku = ''; invalids.push(invalid);
  invalid = clone(); invalid.items[0].change_record.source.source_user_sha256 = 'bad'; invalids.push(invalid);
  for (const value of invalids) assert.throws(() => parseDraftHistory(value), /修改记录/);
});

test('legacy snapshots remain readable without claiming a verified source', () => {
  const legacy = structuredClone(item); legacy.revision = 1; legacy.change_record = null;
  for (const field of ['customer', 'note']) {
    legacy.task_constraints.fields[field].verified = false;
    legacy.task_constraints.fields[field].origin = {kind: 'legacy_snapshot', revision: 1};
  }
  legacy.task_constraints.unverified_fields = ['customer', 'note', 'items'];
  const payload = {...page, items: [legacy], has_more: false, next_before_revision: null};
  assert.deepEqual(parseDraftHistory(payload), payload);
});

test('history client encodes session and draft, uses only GET, passes cancellation and checks target', async () => {
  const signal = new AbortController().signal;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/sessions/s%2Fa/order-drafts/d1/history?limit=5&before_revision=3');
    assert.equal(options.signal, signal); assert.equal(options.method, undefined);
    return Response.json(page);
  };
  assert.deepEqual(await getOrderDraftHistory('s/a', 'd1', 3, signal), page);
  globalThis.fetch = async () => Response.json(page);
  await assert.rejects(getOrderDraftHistory('s/a', 'other'), /不匹配/);
  await assert.rejects(getOrderDraftHistory('s/a', 'd1', 2), /不匹配/);
});

test('history failures preserve domain messages and invalid cursors send no request', async () => {
  globalThis.fetch = async () => Response.json({error: {code: 'not_found', message: '草稿不存在'}}, {status: 404});
  await assert.rejects(getOrderDraftHistory('s1', 'd1'), error => error.code === 'not_found');
  globalThis.fetch = () => {throw new Error('must not request');};
  await assert.rejects(getOrderDraftHistory('s1', 'd1', 0), /游标/);
});

test('business differences distinguish quantity, order, quote projections and added items', () => {
  assert.equal(describeDraftChange({field: 'quantity', sku: 'A', before: 2, after: 5}), 'A · 数量：2 → 5');
  assert.equal(describeDraftChange({field: 'stock_after', sku: 'A', before: 998, after: 995}), 'A · 预计提交后库存：998 → 995');
  assert.equal(describeDraftChange({field: 'item_order', before: ['A', 'B'], after: ['B', 'A']}), '商品顺序：A、B → B、A');
  assert.equal(describeDraftChange({field: 'item', sku: 'A', before: null, after: {name: '商品', quantity: 2}}), 'A · 商品明细：无 → 商品 × 2');
});
