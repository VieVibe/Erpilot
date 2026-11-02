import {test, expect, type Page} from '@playwright/test';

const original = {schema_version:1,pending_id:'a'.repeat(32),state_version:2,status:'pending',
  expires_at:'2099-01-01T00:00:00Z',can_confirm:true,clarification_attempts:2,message:'',
  customer:'完整客户',note:'不要提交，预算文字只是备注',budget_limit:1000,
  items:[{item_number:1,product_query:'标准耳机',sku:null,name:null,quantity:null,
    candidates:[{sku:'R1',name:'标准耳机',status:'在售'},{sku:'R2',name:'标准耳机',status:'在售'}]},
    {item_number:2,product_query:'机械键盘',sku:'K1',name:'机械键盘',quantity:2,candidates:[]}]};

async function mock(page: Page, attempts = 2) {
  let preview: typeof original | null = {...structuredClone(original),clarification_attempts:attempts};
  const posts:{path:string;body:Record<string,unknown>}[]=[];
  const drafts:Record<string,unknown>[]=[];
  let reject=false;let owner:string|undefined;
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(route.request().method()==='POST')posts.push({path,body:route.request().postDataJSON()});
    if(path==='/api/runtime')return route.fulfill({json:{drafts_enabled:true,writes_enabled:false,intent_preview_enabled:true,data_source:'mcp'}});
    if(path.endsWith('/state'))return route.fulfill({json:{session_id:path.split('/')[3],status:'completed',messages:[],tool_results:[],pending_approvals:[]}});
    if(path==='/api/sessions')return route.fulfill({json:{items:[],has_more:false}});
    if(path.endsWith('/order-drafts'))return route.fulfill({json:{items:drafts}});
    if(path.endsWith('/pending-intent/preview')){
      owner??=path.split('/')[3];
      return route.fulfill({json:{preview:path.split('/')[3]===owner?preview:null}});
    }
    if(path.endsWith('/pending-intent/confirm')){
      if(reject)return route.fulfill({status:409,json:{error:{code:'pending_intent_changed',message:'请求已变化，请重新核对'}}});
      const fields=route.request().postDataJSON();
      const saved={draft_id:'confirmed',revision:1,customer:fields.customer,note:fields.note,budget_limit:fields.budget_limit,
        items:fields.items.map((r:{sku:string;quantity:number})=>({...r,name:r.sku,base_price:80,discount:1,unit_price:80,line_total:80*r.quantity,stock_before:1000,stock_after:1000-r.quantity})),
        total_amount:fields.items.reduce((sum:number,r:{quantity:number})=>sum+80*r.quantity,0),expires_at:original.expires_at,status:'open',order_id:null};
      preview=null;drafts.push(saved);return route.fulfill({status:201,json:saved});
    }
    return route.fulfill({status:404,json:{error:{message:path}}});
  });
  return {posts,change:()=>{if(preview)preview={...preview,state_version:3};},reject:()=>{reject=true;},
    expire:(expires_at:string)=>{if(preview)preview={...preview,expires_at};}};
}

for(const width of [320,1440])test(`完整请求预览 ${width}px 可恢复、补齐并一次保存全部字段`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const {posts}=await mock(page);
  await page.goto('/');
  if(width<1200)await page.getByRole('button',{name:'业务面板',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'核对待完成请求',exact:true});
  await expect(dialog).toBeVisible();
  expect(posts).toEqual([]);
  const save=dialog.getByRole('button',{name:'按此完整清单保存草稿',exact:true});
  await expect(save).toBeDisabled();
  await expect(dialog.getByLabel('核对客户',{exact:true})).toHaveValue('完整客户');
  await expect(dialog.getByLabel('核对备注',{exact:true})).toHaveValue(original.note);
  await dialog.getByRole('combobox',{name:'核对第 1 项候选',exact:true}).click();
  await page.getByText('标准耳机 · R2 · 在售',{exact:true}).click();
  await dialog.getByLabel('核对第 1 行件数',{exact:true}).fill('4');
  await dialog.getByLabel('核对客户',{exact:true}).fill('表单核对客户');
  await expect(save).toBeEnabled();
  await expect(dialog.getByLabel('核对预算',{exact:true})).toHaveValue('1000');
  await dialog.getByLabel('核对备注',{exact:true}).scrollIntoViewIfNeeded();
  await expect(dialog.getByLabel('核对备注',{exact:true})).toBeInViewport();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:`../../reports/acceptance/20261031-intent-preview-${width}.png`,fullPage:true,animations:'disabled'});
  await save.click();
  await expect(dialog).not.toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0].path).toMatch(/\/pending-intent\/confirm$/);
  expect(posts[0].body).toEqual({pending_ref:{pending_id:original.pending_id,state_version:2},customer:'表单核对客户',note:original.note,budget_limit:1000,
    items:[{sku:'R2',quantity:4},{sku:'K1',quantity:2}]});
});

test('请求改版后旧核对窗口保持原输入并禁止确认',async({page})=>{
  const fixture=await mock(page,0);
  await page.goto('/');
  await page.getByRole('button',{name:'核对完整请求',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'核对待完成请求',exact:true});
  await dialog.getByLabel('核对客户',{exact:true}).fill('尚未保存的编辑');
  fixture.change();
  await page.getByRole('button',{name:'刷新草稿',exact:true}).evaluate((button:HTMLButtonElement)=>button.click());
  await expect(dialog).toContainText('请求已变化或过期');
  await expect(dialog.getByLabel('核对客户',{exact:true})).toHaveValue('尚未保存的编辑');
  await expect(dialog.getByRole('button',{name:'按此完整清单保存草稿',exact:true})).toBeDisabled();
  expect(fixture.posts).toEqual([]);
});

test('保存冲突不自动重试、不覆盖编辑内容或提交订单',async({page})=>{
  const fixture=await mock(page);fixture.reject();
  await page.goto('/');
  const dialog=page.getByRole('dialog',{name:'核对待完成请求',exact:true});
  await dialog.getByLabel('核对第 1 行 SKU',{exact:true}).fill('R2');
  await dialog.getByLabel('核对第 1 行件数',{exact:true}).fill('4');
  await dialog.getByLabel('核对客户',{exact:true}).fill('保留编辑');
  await dialog.getByRole('button',{name:'按此完整清单保存草稿',exact:true}).click();
  await expect(dialog).toContainText('请求已变化，请重新核对');
  await expect(dialog.getByLabel('核对客户',{exact:true})).toHaveValue('保留编辑');
  expect(fixture.posts).toHaveLength(1);
});

test('过期核对窗口不能保存，关闭和恢复不会发送变更',async({page})=>{
  await page.clock.install({time:new Date('2026-10-31T00:00:00Z')});
  const fixture=await mock(page);fixture.expire('2026-10-31T00:00:02Z');
  await page.goto('/');
  const dialog=page.getByRole('dialog',{name:'核对待完成请求',exact:true});
  await expect(dialog).toBeVisible();
  await page.clock.fastForward(3000);
  await expect(dialog).toContainText('请求已变化或过期');
  await expect(dialog.getByRole('button',{name:'按此完整清单保存草稿',exact:true})).toBeDisabled();
  await dialog.getByRole('button',{name:'继续补充',exact:true}).click();
  expect(fixture.posts).toEqual([]);
});

test('移除商品后仍按原商品展示来源，确认只保存可见完整清单',async({page})=>{
  const fixture=await mock(page);
  await page.goto('/');
  const dialog=page.getByRole('dialog',{name:'核对待完成请求',exact:true});
  await dialog.getByRole('button',{name:'移除核对第 1 行',exact:true}).click();
  await expect(dialog).toContainText('原第 2 项：机械键盘');
  await expect(dialog.getByLabel('核对第 1 行 SKU',{exact:true})).toHaveValue('K1');
  await expect(dialog.getByRole('combobox')).toHaveCount(0);
  await dialog.getByRole('button',{name:'按此完整清单保存草稿',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  expect(fixture.posts[0].body.items).toEqual([{sku:'K1',quantity:2}]);
});

test('切换会话清除旧请求编辑，旧来源不会显示或保存到新会话',async({page})=>{
  const fixture=await mock(page,0);
  await page.goto('/');
  await page.getByRole('button',{name:'核对完整请求',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'核对待完成请求',exact:true});
  await dialog.getByLabel('核对客户',{exact:true}).fill('旧会话未保存内容');
  await dialog.getByRole('button',{name:'继续补充',exact:true}).click();
  await page.getByRole('button',{name:'新会话',exact:true}).click();
  await expect(page.getByRole('button',{name:'核对完整请求',exact:true})).toHaveCount(0);
  await expect(dialog).not.toBeVisible();
  expect(fixture.posts).toEqual([]);
});
