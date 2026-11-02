import {test, expect} from '@playwright/test';

for(const width of [320,1440])test(`语义候选 ${width}px 显示原话且仅完整表单确认会保存`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const source='帮客户甲列份采购清单：机械键盘要四个，备注『不要提交😀』，另一个商品待确定。';
  const preview={schema_version:1,pending_id:'b'.repeat(32),state_version:1,status:'pending',
    expires_at:'2099-01-01T00:00:00Z',can_confirm:true,clarification_attempts:0,
    origin:'semantic_proposal',source_text:source,unmapped_text:'另一个商品待确定',
    message:'以下是待核对候选，请核对完整原话。',customer:'客户甲',note:'不要提交😀',budget_limit:null,
    items:[{item_number:1,product_query:'机械键盘',sku:'K1',name:'机械键盘',quantity:4,candidates:[]}]};
  let current: typeof preview|null=preview;
  const posts:{path:string;body:Record<string,unknown>}[]=[];
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(route.request().method()==='POST')posts.push({path,body:route.request().postDataJSON()});
    if(path==='/api/runtime')return route.fulfill({json:{drafts_enabled:true,intent_preview_enabled:true,data_source:'mcp',writes_enabled:false}});
    if(path.endsWith('/state'))return route.fulfill({json:{session_id:path.split('/')[3],status:'completed',messages:[],tool_results:[],pending_approvals:[]}});
    if(path==='/api/sessions'||path.endsWith('/order-drafts'))return route.fulfill({json:{items:[],has_more:false}});
    if(path.endsWith('/pending-intent/preview'))return route.fulfill({json:{preview:current}});
    if(path.endsWith('/pending-intent/confirm')){
      const fields=route.request().postDataJSON();current=null;
      return route.fulfill({status:201,json:{draft_id:'saved',revision:1,...fields,
        items:fields.items.map((r:{sku:string;quantity:number})=>({...r,name:'机械键盘',base_price:200,discount:1,unit_price:200,line_total:200*r.quantity,stock_before:1000,stock_after:1000-r.quantity})),
        total_amount:200*fields.items[0].quantity,expires_at:preview.expires_at,status:'open',order_id:null}});
    }
    return route.fulfill({status:404,json:{error:{message:path}}});
  });
  await page.goto('/');
  if(width<1200)await page.getByRole('button',{name:'业务面板',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'核对待完成请求',exact:true})).not.toBeVisible();
  await page.getByRole('button',{name:'核对完整请求',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'核对待完成请求',exact:true});
  await expect(dialog).toContainText(source);
  await expect(dialog).toContainText('以下候选尚未确认');
  await dialog.getByText('尚未展开为字段的原文',{exact:true}).click();
  await expect(dialog).toContainText('另一个商品待确定');
  await dialog.getByLabel('核对第 1 行件数',{exact:true}).fill('5');
  expect(posts).toHaveLength(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`../../reports/acceptance/${process.env.ERPILOT_UI_EVIDENCE_PREFIX ?? '20261102-intent-semantic'}-${width}.png`,fullPage:true,animations:'disabled'});
  await dialog.getByRole('button',{name:'按此完整清单保存草稿',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0].path).toMatch(/\/pending-intent\/confirm$/);
  expect(posts[0].body).toEqual({pending_ref:{pending_id:preview.pending_id,state_version:1},customer:'客户甲',note:preview.note,budget_limit:null,items:[{sku:'K1',quantity:5}]});
});
