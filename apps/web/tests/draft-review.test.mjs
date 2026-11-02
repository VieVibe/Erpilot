import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseOrderDraft, draftSubmissionIssue, approvalDraftIssue } from '../.test-build/drafts.js';

const draft = {draft_id:'review-target',revision:1,customer:'客户',note:null,
  items:[{sku:'A',name:'商品',quantity:2,base_price:100,discount:1,unit_price:100,line_total:200,stock_before:20,stock_after:18}],
  total_amount:200,expires_at:'2099-01-01T00:00:00Z',status:'open',order_id:null,
  pending_issues:[{code:'stock_changed',revision:1,blocking:true,message:'当前库存已变化',required_action:'请刷新报价后重新核对'}],
  submission_check:{schema_version:1,revision:1,latest_revision:1,checked_at:'2026-10-27T00:00:00Z',can_submit:false}};

test('live blockers disable both submission and approval without changing the quote',()=>{
  assert.deepEqual(parseOrderDraft(draft),draft);
  assert.match(draftSubmissionIssue(draft),/库存已变化/);
  assert.match(approvalDraftIssue({arguments:{draft_id:draft.draft_id,revision:1}},draft,[draft]),/拒绝旧审批/);
  assert.equal(draft.items[0].stock_before,20);
  assert.equal(draftSubmissionIssue({...draft,pending_issues:[],submission_check:{...draft.submission_check,can_submit:true}}),null);
});

test('inconsistent current checks and wrong revision issues cannot appear actionable',()=>{
  const invalid = [
    {...draft,pending_issues:'all clear'},
    {...draft,pending_issues:[{...draft.pending_issues[0],revision:2}]},
    {...draft,pending_issues:[{...draft.pending_issues[0],blocking:'false'}]},
    {...draft,submission_check:{...draft.submission_check,can_submit:true}},
    {...draft,submission_check:{...draft.submission_check,revision:2}},
    {...draft,submission_check:{...draft.submission_check,checked_at:'unknown'}},
    {...draft,submission_check:{...draft.submission_check,latest_revision:0}},
  ];
  for (const value of invalid) assert.throws(()=>parseOrderDraft(value),/事项|检查/);
});

test('item verification must match the actual complete list and valid origin',()=>{
  const items = {value:[{sku:'A',quantity:2}],verified:true,origin:{kind:'manual_form',revision:1}};
  assert.doesNotThrow(()=>parseOrderDraft({...draft,item_constraint:items}));
  assert.throws(()=>parseOrderDraft({...draft,item_constraint:{...items,value:[{sku:'A',quantity:3}]}}),/商品要求/);
  assert.throws(()=>parseOrderDraft({...draft,item_constraint:{...items,origin:{kind:'unverified',revision:1}}}),/商品要求/);
  assert.throws(()=>parseOrderDraft({...draft,item_constraint:{...items,origin:{kind:'user_literal',revision:1}}}),/商品要求/);
});

test('catalogue resolution preserves the original name request and rejects corrupt mappings',()=>{
  const item_constraint={value:[{sku:'A',quantity:2}],verified:true,origin:{kind:'catalog_resolved',revision:1,
    source_user_sha256:'a'.repeat(64),requirements:[{sku:'A',quantity:2,quote:'商品两件',product_query:'商品',product_name:'商品'}]}};
  assert.doesNotThrow(()=>parseOrderDraft({...draft,item_constraint}));
  const changed=structuredClone(item_constraint);changed.origin.requirements[0].product_name='另一商品';
  assert.throws(()=>parseOrderDraft({...draft,item_constraint:changed}),/名称核对/);
  const missing=structuredClone(item_constraint);delete missing.origin.requirements[0].product_query;
  assert.throws(()=>parseOrderDraft({...draft,item_constraint:missing}),/商品要求/);
});
