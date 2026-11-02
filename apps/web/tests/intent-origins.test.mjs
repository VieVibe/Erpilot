import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseOrderDraft } from '../.test-build/drafts.js';

const draft = {draft_id:'intent',revision:1,customer:'客户甲',note:null,
  items:[{sku:'R2',name:'标准耳机',quantity:3,base_price:80,discount:1,unit_price:80,line_total:240,stock_before:1000,stock_after:997}],
  total_amount:240,expires_at:'2099-01-01T00:00:00Z',status:'open',order_id:null,
  item_constraint:{value:[{sku:'R2',quantity:3}],verified:true,origin:{kind:'intent_verified',contract:'draft-intent-2',revision:1,
    source_messages:[{message_id:'initial',sha256:'a'.repeat(64)},{message_id:'reply',sha256:'b'.repeat(64)}],
    requirements:[{sku:'R2',quantity:3,evidence_refs:[{message_id:'initial',start:0,end:7,quote:'标准耳机两件😀'},
      {message_id:'reply',start:0,end:5,quote:'数量改三件'}]}]}}};

test('multi-message item provenance preserves Unicode offsets and final quantities', () => {
  assert.deepEqual(parseOrderDraft(draft), draft);
});

test('invalid message references and incomplete provenance cannot appear verified', () => {
  for (const fault of ['message', 'offset', 'quantity', 'hash', 'duplicate', 'contract', 'missing']) {
    const value = structuredClone(draft);
    const origin = value.item_constraint.origin;
    if (fault === 'message') origin.requirements[0].evidence_refs[0].message_id = 'unknown';
    if (fault === 'offset') origin.requirements[0].evidence_refs[0].end = 8;
    if (fault === 'quantity') origin.requirements[0].quantity = 2;
    if (fault === 'hash') origin.source_messages[0].sha256 = 'fake';
    if (fault === 'duplicate') origin.source_messages[1].message_id = 'initial';
    if (fault === 'contract') origin.contract = 'draft-form-1';
    if (fault === 'missing') origin.requirements = [];
    assert.throws(() => parseOrderDraft(value), /商品要求/);
  }
});

test('a bounded multi-turn source can include both a choice and quantity span per reply', () => {
  const value = structuredClone(draft);
  const refs = value.item_constraint.origin.requirements[0].evidence_refs;
  value.item_constraint.origin.requirements[0].evidence_refs = Array.from({length:103}, () => refs[0]);
  assert.doesNotThrow(() => parseOrderDraft(value));
  value.item_constraint.origin.requirements[0].evidence_refs = Array.from({length:205}, () => refs[0]);
  assert.throws(() => parseOrderDraft(value), /商品要求/);
});
