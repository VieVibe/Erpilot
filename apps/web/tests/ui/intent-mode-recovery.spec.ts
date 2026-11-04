import {test,expect} from '@playwright/test';

for(const width of [320,1440])test(`回退恢复 ${width}px 只能重新核对且无保存入口`,async({page},testInfo)=>{
  await page.setViewportSize({width,height:1000});
  const posts:string[]=[];
  const preview={schema_version:1,pending_id:'a'.repeat(32),state_version:2,status:'needs_recheck',
    expires_at:'2099-01-01T00:00:00Z',can_confirm:false,clarification_attempts:0,
    customer:'',note:null,budget_limit:null,items:[],
    message:'此请求已因模式切换停用，请重新说明完整要求并重新核对，不能沿用旧候选或确认。'};
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(route.request().method()==='POST')posts.push(path);
    if(path==='/api/runtime')return route.fulfill({json:{drafts_enabled:true,intent_recovery_enabled:true,data_source:'mcp',writes_enabled:false}});
    if(path.endsWith('/state'))return route.fulfill({json:{session_id:path.split('/')[3],status:'completed',messages:[],tool_results:[],pending_approvals:[]}});
    if(path==='/api/sessions'||path.endsWith('/order-drafts'))return route.fulfill({json:{items:[],has_more:false}});
    if(path.endsWith('/pending-intent/preview'))return route.fulfill({json:{preview}});
    return route.fulfill({status:404,json:{error:{message:path}}});
  });
  await page.goto('/');
  if(width<1200)await page.getByRole('button',{name:'业务面板',exact:true}).click();
  const section=page.getByRole('region',{name:'待完成请求核对',exact:true});
  await expect(section).toContainText('模式切换停用');
  await expect(section.getByRole('button',{name:'核对完整请求',exact:true})).toBeDisabled();
  await expect(page.getByRole('dialog',{name:'核对待完成请求',exact:true})).not.toBeVisible();
  expect(posts).toHaveLength(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath(`intent-mode-recovery-${width}.png`),fullPage:true,animations:'disabled'});
});
