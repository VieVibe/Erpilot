import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import {buildSync} from 'esbuild';
import {createRequire} from 'node:module';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
createRequire(import.meta.url).extensions['.css'] = () => {};
const proof = JSON.parse(readFileSync(new URL('./replenishment-fixture.json', import.meta.url), 'utf8'));
buildSync({entryPoints:['src/components/BusinessResultCard.tsx'],bundle:true,platform:'node',
  format:'esm',outfile:'.test-build/replenishment-result.mjs',packages:'external',jsx:'automatic'});
const {BusinessResultCard,isUsableDisplay} = await import('../.test-build/replenishment-result.mjs');
const display = suggestion => ({version:1,kind:'replenishment_suggestion',outcome:'succeeded',suggestion});
const html = suggestion => renderToStaticMarkup(React.createElement(BusinessResultCard,{display:display(suggestion)})).replace(/<!--.*?-->/g,'');

test('replenishment card shows quantities, time, assumptions and no mutation controls',()=>{
  const result = html(proof);
  for(const text of ['只读测算','青瓷茶杯','5 件','60 件','2.5 天','37 件','采购周期 7 天','未采购','未调整库存']) assert.ok(result.includes(text),text);
  assert.match(result,/<summary>查看计算依据与限制<\/summary>/);
  assert.doesNotMatch(result,/<button|采购成功|库存已调整/);
});

test('unknown lead and zero sales do not appear as zero advice or infinite coverage',()=>{
  const suggestion = structuredClone(proof);
  suggestion.lead_time_days = null;
  suggestion.assumptions = ['采购周期未知：未计入采购等待需求，不能判断到货前断货风险。'];
  Object.assign(suggestion.items[0], {sold_quantity:0,daily_sales:0,decision:'no_sales',
    estimated_days_remaining:null,target_quantity:null,suggested_quantity:null,stockout_before_arrival:null});
  const result = html(suggestion);
  for(const text of ['采购周期未知','销量依据不足','未给出数量','无法测算','未判断']) assert.ok(result.includes(text),text);
  assert.doesNotMatch(result,/Infinity|无限|NaN/);
});

test('malformed suggestion falls back and data strings are escaped',()=>{
  for(const suggestion of [{...proof,schema_version:2},{...proof,has_more:true},
    {...proof,days:0},{...proof,items:[{...proof.items[0],suggested_quantity:NaN}]},
    {...proof,items:[{...proof.items[0],decision:'no_sales'}]},
    {...proof,items:[{...proof.items[0],stock_quantity:-1}]}]) assert.equal(isUsableDisplay(display(suggestion)),false);
  const suggestion = structuredClone(proof);
  suggestion.items[0].name = '<img src=x onerror=alert(1)>';
  suggestion.limitations.push('<script>alert(1)</script>');
  const result = html(suggestion);
  assert.match(result,/&lt;img/); assert.doesNotMatch(result,/<img|<script/);
});
