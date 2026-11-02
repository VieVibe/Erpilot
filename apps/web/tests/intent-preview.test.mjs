import assert from 'node:assert/strict';
import {test} from 'node:test';
import {parseIntentPreview, getIntentPreview, confirmIntentPreview, requestIntentRevisionPreview} from '../.test-build/intent-preview.js';

const preview = {schema_version:1,pending_id:'a'.repeat(32),state_version:2,status:'pending',
  expires_at:'2099-01-01T00:00:00Z',can_confirm:true,clarification_attempts:2,message:'',
  customer:'完整客户',note:'不要提交',budget_limit:1000,
  items:[{item_number:1,product_query:'标准耳机',sku:null,name:null,quantity:null,
    candidates:[{sku:'R1',name:'标准耳机',status:'在售'},{sku:'R2',name:'标准耳机',status:'在售'}]}]};
const fields = {customer:'完整客户',note:null,budget_limit:null,items:[{sku:'R2',quantity:4}]};
const saved = {draft_id:'saved',revision:1,customer:fields.customer,note:null,
  items:[{sku:'R2',name:'标准耳机',quantity:4,base_price:80,discount:1,unit_price:80,
    line_total:320,stock_before:1000,stock_after:996}],total_amount:320,
  expires_at:preview.expires_at,status:'open',order_id:null,budget_limit:null};

test('rollback recovery is a readable preview with no confirmation capability',()=>{
  const recovery={...preview,status:'needs_recheck',can_confirm:false,customer:'',note:null,budget_limit:null,items:[]};
  assert.deepEqual(parseIntentPreview(recovery),recovery);
  assert.throws(()=>parseIntentPreview({...recovery,can_confirm:true}),/核对信息/);
});

test('preview preserves unresolved rows and requires consistent capabilities and bounded candidates',()=>{
  assert.deepEqual(parseIntentPreview(preview),preview);
  assert.equal(parseIntentPreview(null),null);
  for(const fault of ['version','expired','id','quantity','empty','candidate','duplicate','budget','note']) {
    const value=structuredClone(preview);
    if(fault==='version')value.state_version=true;
    if(fault==='expired')value.status='expired';
    if(fault==='id')value.pending_id='forged';
    if(fault==='quantity')value.items[0].quantity=true;
    if(fault==='empty')value.items=[];
    if(fault==='candidate')value.items[0].candidates[0].sku='';
    if(fault==='duplicate')value.items[0].candidates.push(value.items[0].candidates[0]);
    if(fault==='budget')value.budget_limit=0;
    if(fault==='note')delete value.note;
    assert.throws(()=>parseIntentPreview(value),/核对信息/);
  }
});

test('semantic candidates preserve original and unmapped text and reject incomplete source displays',()=>{
  const semantic={...structuredClone(preview),origin:'semantic_proposal',source_text:'帮客户列份采购清单😀',unmapped_text:'帮  列份采购清单'};
  assert.deepEqual(parseIntentPreview(semantic),semantic);
  for(const change of [{origin:'verified'},{source_text:''},{source_text:'x'.repeat(32001)},{unmapped_text:null}]) {
    assert.throws(()=>parseIntentPreview({...semantic,...change}),/核对信息/);
  }
});

test('GET restore is read only; POST binds the displayed request and all form fields',async()=>{
  const original=globalThis.fetch, calls=[];
  globalThis.fetch=async(url,options)=>{
    calls.push({url,options});return new Response(JSON.stringify(options.method==='POST'?saved:{preview}),{status:options.method==='POST'?201:200});
  };
  try {
    const controller=new AbortController();
    assert.deepEqual(await getIntentPreview('s/foreign',controller.signal),preview);
    assert.equal(calls[0].url,'/api/sessions/s%2Fforeign/pending-intent/preview');
    assert.equal(calls[0].options.method,undefined);
    assert.equal(calls[0].options.signal,controller.signal);
    assert.deepEqual(await confirmIntentPreview('s',preview,{...fields,submit:true,pending_ref:{pending_id:'forged'}}),saved);
    assert.deepEqual(JSON.parse(calls[1].options.body),{...fields,pending_ref:{pending_id:preview.pending_id,state_version:2}});
    assert.equal(calls[1].options.method,'POST');
  } finally {globalThis.fetch=original;}
});

test('expired previews send no writes; mismatching success and stale failures retain explicit errors',async()=>{
  const original=globalThis.fetch;let calls=0;
  globalThis.fetch=async()=>{calls++;return new Response(JSON.stringify({...saved,customer:'其他客户'}),{status:201});};
  try {
    await assert.rejects(confirmIntentPreview('s',{...preview,expires_at:'2000-01-01T00:00:00Z'},fields),/过期/);
    assert.equal(calls,0);
    await assert.rejects(confirmIntentPreview('s',preview,fields),/不一致/);
    globalThis.fetch=async()=>new Response(JSON.stringify({error:{code:'pending_intent_changed',message:'请求已变化'}}),{status:409});
    await assert.rejects(confirmIntentPreview('s',preview,fields),e=>e.status===409&&e.code==='pending_intent_changed'&&/请求已变化/.test(e.message));
  } finally {globalThis.fetch=original;}
});

test('revision preview requires target and base fields and confirmation checks same draft next revision',async()=>{
  const id='c'.repeat(32), source='调整耳机到四个';
  const revision={...preview,origin:'semantic_proposal',source_text:source,unmapped_text:'',operation:'revise',target:{draft_id:id,revision:3},before_fields:{...fields},items:[{...preview.items[0],sku:'R2',quantity:4}]};
  assert.deepEqual(parseIntentPreview(revision),revision);
  for(const change of [{operation:'create'},{target:null},{target:{draft_id:id,revision:true}},{before_fields:null},{before_fields:{...fields,items:[{sku:'R2',quantity:true}]}}])assert.throws(()=>parseIntentPreview({...revision,...change}),/核对信息/);
  const original=globalThis.fetch,calls=[];
  globalThis.fetch=async(url,options)=>{
    calls.push({url,body:JSON.parse(options.body)});
    return new Response(JSON.stringify(url.endsWith('revise-preview')?{preview:revision}:{...saved,draft_id:id,revision:4}),{status:201});
  };
  try{
    assert.deepEqual(await requestIntentRevisionPreview('s',{...saved,draft_id:id,revision:3},source,'e'.repeat(32)),revision);
    assert.deepEqual(calls[0].body,{source,target:revision.target,request_id:'e'.repeat(32)});
    await confirmIntentPreview('s',revision,fields);
    assert.deepEqual(calls[1].body.target,revision.target);
    globalThis.fetch=async()=>new Response(JSON.stringify({...saved,draft_id:'other',revision:4}),{status:201});
    await assert.rejects(confirmIntentPreview('s',revision,fields),/不一致/);
  }finally{globalThis.fetch=original;}
});
