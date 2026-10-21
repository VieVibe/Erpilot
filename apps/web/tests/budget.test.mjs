import assert from 'node:assert/strict';
import { test } from 'node:test';
import { draftBudgetIssue, parseOrderDraft, approvalDraftIssue, describeDraftChange } from '../.test-build/drafts.js';

const draft = {draft_id:'d',revision:1,customer:'客户',note:null,items:[{sku:'A',name:'商品',quantity:1,base_price:3200,discount:1,unit_price:3200,line_total:3200,stock_before:2,stock_after:1}],total_amount:3200,expires_at:'2099-01-01T00:00:00Z',status:'open',order_id:null,budget_limit:3000,budget_constraint:{currency:'CNY',limit:3000,origin:{kind:'manual_form',revision:1}}};

test('budget checks use the exact displayed amount, preserve legacy and enforce approval',()=>{
  assert.equal(parseOrderDraft(draft).budget_limit,3000);
  assert.match(draftBudgetIssue(draft),/200.00/);
  assert.equal(draftBudgetIssue({...draft,budget_limit:3200}),null);
  assert.equal(draftBudgetIssue({...draft,budget_limit:null}),null);
  assert.match(approvalDraftIssue({arguments:{draft_id:'d',revision:1}},draft,[draft]),/超出预算/);
  assert.equal(describeDraftChange({field:'budget_limit',before:3000,after:3500}),'预算上限：3000 → 3500');
});

test('corrupt budget values and mismatching origins cannot appear as valid quotes',()=>{
  for(const value of [true,'3000',0,-1,Infinity]) assert.throws(()=>parseOrderDraft({...draft,budget_limit:value}),/预算/);
  assert.throws(()=>parseOrderDraft({...draft,budget_constraint:{...draft.budget_constraint,limit:3500}}),/不一致/);
  assert.throws(()=>parseOrderDraft({...draft,budget_constraint:{...draft.budget_constraint,origin:{kind:'user_literal',revision:1}}}),/来源/);
});
