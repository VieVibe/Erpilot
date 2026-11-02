import {test, expect} from '@playwright/test';

for(const width of [320,1440])test(`原话修改 ${width}px 明确目标版本并完整确认`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const id='c'.repeat(32), source='调整机械键盘到六个，预算改为1200元。';
  const original={draft_id:id,revision:1,customer:'原客户',note:'原备注',budget_limit:1000,
    expires_at:'2099-01-01T00:00:00Z',status:'open',order_id:null,total_amount:400,
    items:[{sku:'K1',name:'机械键盘',quantity:2,base_price:200,discount:1,unit_price:200,
      line_total:400,stock_before:1000,stock_after:998}]};
  const preview={schema_version:1,pending_id:'d'.repeat(32),state_version:1,status:'pending',
    expires_at:original.expires_at,can_confirm:true,clarification_attempts:0,
    operation:'revise',target:{draft_id:id,revision:1},
    before_fields:{customer:'原客户',note:'原备注',budget_limit:1000,items:[{sku:'K1',quantity:2}]},
    origin:'semantic_proposal',source_text:source,unmapped_text:'',message:'请核对目标版本与完整清单。',
    customer:'原客户',note:'原备注',budget_limit:1200,
    items:[{item_number:1,product_query:'机械键盘',sku:'K1',name:'机械键盘',quantity:6,candidates:[]}]};
  let pending:typeof preview|null=null, draft=original;
  const posts:{path:string;body:Record<string,unknown>}[]=[];
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname, method=route.request().method();
    if(method==='POST')posts.push({path,body:route.request().postDataJSON()});
    if(path==='/api/runtime')return route.fulfill({json:{drafts_enabled:true,intent_preview_enabled:true,intent_semantic_revision_enabled:true,data_source:'mcp',writes_enabled:false}});
    if(path.endsWith('/state'))return route.fulfill({json:{session_id:path.split('/')[3],status:'completed',messages:[],tool_results:[],pending_approvals:[]}});
    if(path==='/api/sessions')return route.fulfill({json:{items:[],has_more:false}});
    if(path.endsWith('/order-drafts'))return route.fulfill({json:{items:[draft]}});
    if(path.endsWith('/history'))return route.fulfill({json:{schema_version:1,draft_id:id,latest_revision:draft.revision,items:[],has_more:false,next_before_revision:null}});
    if(path.endsWith('/pending-intent/preview'))return route.fulfill({json:{preview:pending}});
    if(path.endsWith('/pending-intent/revise-preview')){
      pending=preview;return route.fulfill({json:{preview}});
    }
    if(path.endsWith('/pending-intent/confirm')){
      const form=route.request().postDataJSON();pending=null;
      draft={...original,revision:2,customer:form.customer,note:form.note,budget_limit:form.budget_limit,
        total_amount:200*form.items[0].quantity,
        items:[{...original.items[0],quantity:form.items[0].quantity,line_total:200*form.items[0].quantity,stock_after:1000-form.items[0].quantity}]};
      return route.fulfill({status:201,json:draft});
    }
    return route.fulfill({status:404,json:{error:{message:path}}});
  });
  await page.goto('/');
  if(width<1200)await page.getByRole('button',{name:'业务面板',exact:true}).click();
  await page.getByLabel('修改原话',{exact:true}).fill(source);
  await page.getByRole('button',{name:'按原话生成修改预览',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'核对待完成请求',exact:true});
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('region',{name:'修改前清单'})).toContainText('版本 1');
  await expect(dialog).toContainText(id);
  await expect(dialog).toContainText('K1：2 件');
  await expect(dialog.getByLabel('核对第 1 行件数',{exact:true})).toHaveValue('6');
  await dialog.getByLabel('核对第 1 行件数',{exact:true}).fill('5');
  expect(posts).toHaveLength(1);
  expect(posts[0].body).toMatchObject({source,target:{draft_id:id,revision:1}});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`../../reports/acceptance/20261102-intent-revision-${width}.png`,fullPage:true,animations:'disabled'});
  await dialog.getByRole('button',{name:'确认目标并保存新版本',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[1].body).toMatchObject({target:{draft_id:id,revision:1},items:[{sku:'K1',quantity:5}]});
  expect(posts[1].path).toMatch(/pending-intent\/confirm$/);
});
