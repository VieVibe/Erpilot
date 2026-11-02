import { test, expect, type Page } from '@playwright/test';

const original = {draft_id:'review-target',revision:1,customer:'完整客户',note:null,
  items:[{sku:'A',name:'商品甲',quantity:2,base_price:100,discount:1,unit_price:100,line_total:200,stock_before:20,stock_after:18}],
  total_amount:200,expires_at:'2099-01-01T00:00:00Z',status:'open',order_id:null,
  item_constraint:{value:[{sku:'A',quantity:2}],verified:true,origin:{kind:'manual_form',revision:1}},
  pending_issues:[] as {code:string;message:string;required_action:string;blocking:boolean;revision:number}[],
  submission_check:{schema_version:1,revision:1,latest_revision:1,checked_at:'2026-10-27T00:00:00Z',can_submit:true}};
async function mock(page: Page, blocked: boolean, multiple = false, catalogue = false) {
  let current=structuredClone(original);
  if(catalogue)Object.assign(current,{item_constraint:{value:[{sku:'A',quantity:2}],verified:true,
    origin:{kind:'catalog_resolved',revision:1,source_user_sha256:'a'.repeat(64),
      requirements:[{sku:'A',quantity:2,quote:'商品甲两件',product_query:'商品甲',product_name:'商品甲'}]}}});
  const posts:string[]=[];
  const block=()=>{current.pending_issues=[
    {code:'quote_changed',message:'商品 A 的现价已变化',required_action:'请明确刷新报价后重新核对和审批',blocking:true,revision:1},
    {code:'stock_changed',message:'商品 A 库存不足',required_action:'请明确调整商品或数量后保存新版本',blocking:true,revision:1},
  ];current.submission_check.can_submit=false;};
  if(blocked)block();
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(route.request().method()==='POST')posts.push(path);
    if(path==='/api/runtime')return route.fulfill({json:{drafts_enabled:true,writes_enabled:true,data_source:'mcp'}});
    if(path.endsWith('/state'))return route.fulfill({json:{session_id:path.split('/')[3],status:'completed',messages:[],tool_results:[],pending_approvals:[]}});
    if(path==='/api/sessions')return route.fulfill({json:{items:[],has_more:false}});
    if(path.endsWith('/order-drafts'))return route.fulfill({json:{items:multiple ? [current,{...current,draft_id:'other-target',customer:'第二客户'}] : [current]}});
    if(path.endsWith('/review-target'))return route.fulfill({json:current});
    return route.fulfill({status:404,json:{error:{message:'No fixture'}}});
  });
  return {block,posts};
}

for(const width of [320,1440])test(`商品名称与中文件数 ${width}px 可核对原文和实际商品`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const {posts}=await mock(page,false,false,true);
  await page.goto('/');
  if(width<1200)await page.getByRole('button',{name:'业务面板',exact:true}).click();
  const panel=page.getByRole('region',{name:'订单草稿',exact:true});
  await panel.getByText('查看原始商品要求',{exact:true}).click();
  await expect(panel).toContainText('商品甲两件 → 商品甲（A），2 件');
  const mapping=panel.getByText('商品甲两件 → 商品甲（A），2 件',{exact:true});
  await mapping.scrollIntoViewIfNeeded();
  await expect(mapping).toBeInViewport();
  await expect(panel).toContainText('名称已与商品目录核对');
  expect(posts).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:`../../.playwright-mcp/natural-order-${width}.png`,fullPage:true,animations:'disabled'});
});

for(const width of [320,1440])test(`待确认事项 ${width}px 展示全部原因并阻止提交`,async({page})=>{
  await page.setViewportSize({width,height:1000});
  const {posts}=await mock(page,true);
  await page.goto('/');
  if(width<1200)await page.getByRole('button',{name:'业务面板',exact:true}).click();
  const panel=page.getByRole('region',{name:'订单草稿',exact:true});
  if(width<1200)await page.getByRole('dialog',{name:'业务面板',exact:true}).hover();
  await expect(panel.getByLabel('待确认事项',{exact:true})).toContainText('现价已变化');
  await expect(panel.getByLabel('待确认事项',{exact:true})).toContainText('库存不足');
  await expect(panel.getByLabel('商品要求核对')).toContainText('已按表单逐项核对');
  await expect(page.getByRole('button',{name:'请求提交版本 1 并审批',exact:true})).toBeDisabled();
  await panel.getByLabel('待确认事项',{exact:true}).scrollIntoViewIfNeeded();
  await expect(panel.getByLabel('待确认事项',{exact:true})).toBeInViewport();
  expect(posts).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:`../../.playwright-mcp/draft-review-${width}.png`,fullPage:true,animations:'disabled'});
});

test('核对窗口打开后同版本条件变化也会禁用确认',async({page})=>{
  const {block,posts}=await mock(page,false);
  await page.goto('/');
  await page.getByRole('button',{name:'请求提交版本 1 并审批',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'核对草稿版本 1'});
  await expect(dialog.getByRole('button',{name:'确认此版本并申请审批',exact:true})).toBeEnabled();
  block();
  // Trigger the same refresh handler that a newly received server observation uses.
  await page.getByRole('button',{name:'刷新草稿',exact:true}).evaluate((element:HTMLButtonElement)=>element.click());
  await expect(dialog.getByRole('button',{name:'确认此版本并申请审批',exact:true})).toBeDisabled();
  await expect(dialog).toContainText('提交条件已变化');
  expect(posts).toEqual([]);
});

test('多份草稿需明确选择目标后才能编辑和提交',async({page})=>{
  const {posts}=await mock(page,false,true);
  await page.goto('/');
  await expect(page.getByText('有多份未提交草稿，请先在上方明确选择目标。',{exact:true})).toBeVisible();
  await expect(page.getByRole('textbox',{name:'草稿客户',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'请求提交版本 1 并审批',exact:true})).toBeDisabled();
  await page.getByRole('combobox',{name:'选择草稿',exact:true}).click();
  await page.getByText('完整客户 · 版本 1',{exact:true}).click();
  await expect(page.getByRole('textbox',{name:'草稿客户',exact:true})).toBeEnabled();
  await expect(page.getByRole('button',{name:'请求提交版本 1 并审批',exact:true})).toBeEnabled();
  expect(posts).toEqual([]);
});
