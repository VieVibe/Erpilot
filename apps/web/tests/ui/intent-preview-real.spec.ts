import {test, expect} from '@playwright/test';

const api = process.env.ERPILOT_PREVIEW_TEST_API;
test.skip(!api, 'Run explicitly against the isolated preview acceptance server.');

for(const width of [320,1440])test(`实际 API ${width}px 恢复并核对完整请求，无模型建单调用`,async({page,request},testInfo)=>{
  const sid=`preview-browser-${width}`;
  const source='给客户甲准备订单：标准耳机，机械键盘两件；备注原文『不要提交』；预算不超过1000元；只保存草稿';
  for(const message of [source,'选第九个','选第八个']){
    const response=await request.post(`${api}/api/chat/stream`,{data:{session_id:sid,message}});
    expect(response.status()).toBe(200);expect(await response.text()).toContain('event: done');
  }
  const original=(await(await request.get(`${api}/api/sessions/${sid}/pending-intent/preview`)).json()).preview;
  expect(original.clarification_attempts).toBe(2);
  const posts:{path:string;body:unknown}[]=[];
  await page.route('**/api/**',async route=>{
    const url=new URL(route.request().url());
    if(route.request().method()==='POST')posts.push({path:url.pathname,body:route.request().postDataJSON()});
    const response=await route.fetch({url:`${api}${url.pathname}${url.search}`});
    await route.fulfill({response});
  });
  await page.addInitScript(id=>{localStorage.setItem('erpilot-session-id',id);},sid);
  await page.setViewportSize({width,height:1000});
  await page.goto('/');
  if(width<1200)await page.getByRole('button',{name:'业务面板',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'核对待完成请求',exact:true});
  await expect(dialog).toBeVisible();expect(posts).toEqual([]);
  await dialog.getByLabel('核对第 1 行 SKU',{exact:true}).fill('R2');
  await dialog.getByLabel('核对第 1 行件数',{exact:true}).fill('4');
  await dialog.getByLabel('核对客户',{exact:true}).fill(`核对完整客户${width}`);
  await dialog.getByLabel('核对预算',{exact:true}).fill('1500');
  await page.screenshot({path:testInfo.outputPath(`intent-preview-real-${width}.png`),fullPage:true,animations:'disabled'});
  await dialog.getByRole('button',{name:'按此完整清单保存草稿',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  expect(posts).toHaveLength(1);expect(posts[0].path).toMatch(/\/pending-intent\/confirm$/);
  const state=await(await request.get(`${api}/api/sessions/${sid}/state`)).json();
  expect(state.pending_intent).toBeNull();expect(state.pending_approvals).toEqual([]);
  const draft=state.order_task.draft;
  expect(draft.customer).toBe(`核对完整客户${width}`);expect(draft.budget_limit).toBe(1500);
  expect(draft.items.map((r:{sku:string;quantity:number})=>({sku:r.sku,quantity:r.quantity}))).toEqual([{sku:'R2',quantity:4},{sku:'K1',quantity:2}]);
  expect(draft.item_constraint.origin.kind).toBe('manual_form');expect(draft.order_id).toBeNull();
  const duplicate=await request.post(`${api}/api/sessions/${sid}/pending-intent/confirm`,{data:posts[0].body});
  expect(duplicate.status()).toBe(409);
  const list=await(await request.get(`${api}/api/sessions/${sid}/order-drafts`)).json();
  expect(list.items).toHaveLength(1);
  await page.reload();
  if(width<1200)await page.getByRole('button',{name:'业务面板',exact:true}).click();
  await expect(page.getByRole('region',{name:'订单草稿',exact:true})).toContainText(`核对完整客户${width}`);
  await expect(page.getByRole('button',{name:'核对完整请求',exact:true})).toHaveCount(0);
});
