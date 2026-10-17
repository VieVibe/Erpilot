import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {buildSync} from 'esbuild';
import {createRequire} from 'node:module';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
createRequire(import.meta.url).extensions['.css'] = () => {};
const proof = JSON.parse(readFileSync(new URL('./policy-search-fixture.json', import.meta.url), 'utf8'));
buildSync({entryPoints:['src/components/BusinessResultCard.tsx'],bundle:true,platform:'node',
  format:'esm',outfile:'.test-build/policy-search-result.mjs',packages:'external',jsx:'automatic'});
const {BusinessResultCard,isUsableDisplay} = await import('../.test-build/policy-search-result.mjs');
buildSync({entryPoints:['src/policyEvidence.ts'],bundle:true,platform:'node',
  format:'esm',outfile:'.test-build/policy-evidence.mjs'});
const {evidenceSegments} = await import('../.test-build/policy-evidence.mjs');
const display = search => ({version:1,kind:'policy_search',outcome:'succeeded',query:search.query,search});
const render = value => renderToStaticMarkup(React.createElement(BusinessResultCard,{display:value})).replace(/<!--.*?-->/g,'');

test('retrieved evidence shows query, numbered sources, version, time, score and literal highlights',()=>{
  const result = render(display(proof));
  for(const text of ['政策检索证据',proof.query,'[1]','[2]','return-policy.md','版本 2026-10-12.1',
    proof.queried_at,'0.8512','2 条依据','不代表结论可信度']) assert.ok(result.includes(text),text);
  assert.match(result,/<mark>退货<\/mark>/);
  assert.doesNotMatch(result,/<button|85\.12%|置信度/);
});

test('highlights preserve source exactly, handle repeated Chinese terms and bound English and numeric terms',()=>{
  for(const [original,query,expected] of [
    ['退货需签收，退货需审批。','怎么退货',['退货','退货']],
    ['SKU A100 与 A1001；7 天与 17 天','A100 7',['A100','7']],
    ['\n**退款** <script>退款</script>\n','退款',['退款','退款']],
    ['支持退货但不确定期限','几天可以',[]],
    ['7 天可退；退货需审核','天气',[]],
    ['🙂 SKU sku İ\n','SKU',['SKU','sku']],
    ['原文 a+b [x] (退款)','a+b [x] (退款)',['退款']],
  ]) {
    const segments = evidenceSegments(original,query);
    assert.equal(segments.map(part=>part.text).join(''),original);
    assert.deepEqual(segments.filter(part=>part.matched).map(part=>part.text),expected);
  }
});

test('no-match and unavailable states never manufacture sources',()=>{
  for(const policy_state of ['no_match','inactive','conflict','unavailable']) {
    const result = render(display({...proof,policy_state,matches:[],note:'请如实说明，不要臆造政策'}));
    assert.ok(result.includes('没有可引用'));
    assert.doesNotMatch(result,/<blockquote|<mark|return-policy.md|不要臆造/);
  }
  const result = render({version:1,kind:'policy_search',outcome:'failed',query:proof.query,message:'政策检索暂不可用'});
  assert.ok(result.includes('政策检索未完成'));
  assert.doesNotMatch(result,/<blockquote|找到政策依据/);
});

test('unsafe evidence is escaped; malformed, stale, conflicting or mismatched evidence falls back',()=>{
  const unsafe = structuredClone(proof);
  unsafe.matches[0].text = '<script>alert(1)</script> 退货 <img src=x onerror=alert(1)>';
  unsafe.matches[0].source = 'javascript:alert(1)';
  const result = render(display(unsafe));
  assert.ok(result.includes('&lt;script&gt;'));
  assert.doesNotMatch(result,/<script|<img|href=/);
  for(const search of [{...proof,schema_version:true},{...proof,queried_at:'2026-10-17'},
    {...proof,matches:[]},{...proof,policy_state:'future'},
    {...proof,matches:[{...proof.matches[0],score:Infinity}]},
    {...proof,matches:[{...proof.matches[0],effective_until:proof.queried_at}]},
    {...proof,matches:[{...proof.matches[0],effective_from:'2027-01-01T00:00:00Z'}]},
    {...proof,matches:[proof.matches[0],{...proof.matches[1],version:'other'}]},
  ]) assert.equal(isUsableDisplay(display(search)),false);
  assert.equal(isUsableDisplay({...display(proof),query:'折扣'}),false);
  assert.equal(isUsableDisplay({...display(proof),version:2}),false);
});
