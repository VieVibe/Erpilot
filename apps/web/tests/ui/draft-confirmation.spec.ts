import { test, expect, type Page } from '@playwright/test';

const unsafe = '<script>window.erpilotConfirmationUnsafe=true</script>';
const original = {draft_id: 'confirmed-target', revision: 2, customer: '完整确认客户甲', note: unsafe,
  items: [{sku:'A',name:'商品甲',quantity:5,base_price:100,discount:1,unit_price:100,line_total:500,stock_before:1000,stock_after:995},
    {sku:'B',name:'商品乙',quantity:3,base_price:20,discount:1,unit_price:20,line_total:60,stock_before:1000,stock_after:997}],
  total_amount:560, expires_at:'2099-01-01T00:00:00Z',status:'open',order_id:null};
function sse(event: string, data: unknown) {return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;}
async function mock(page: Page, stale = false) {
  let current = structuredClone(original);
  const posts: {path:string; body:Record<string,unknown>}[] = [];
  let pending = false;
  const approval = () => ({pending_id:'confirmed-pending',call_id:'application-confirmation-test',
    tool:'submit_order_draft',risk:'single_confirm',arguments:{draft_id:original.draft_id,revision:2},draft:original});
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (request.method() === 'POST') posts.push({path,body:request.postDataJSON()});
    const json = (body:unknown, status = 200) => route.fulfill({status,json:body});
    if (path === '/api/runtime') return json({drafts_enabled:true,writes_enabled:true,data_source:'mcp'});
    if (path.endsWith('/state')) return json({session_id:path.split('/')[3],status:pending?'waiting_approval':'completed',messages:[],tool_results:[],pending_approvals:pending?[approval()]:[]});
    if (path === '/api/sessions') return json({items:[],has_more:false});
    if (path.endsWith('/order-drafts')) return json({items:[current]});
    if (request.method() === 'GET' && path.endsWith('/'+original.draft_id)) return json(url.searchParams.has('revision')?original:current);
    if (path.endsWith('/confirm/stream')) {
      if (stale) {
        current = {...current,revision:3};
        return json({error:{code:'draft_changed',message:'草稿已有新版本，请重新核对后确认'}},409);
      }
      pending = true;
      return route.fulfill({contentType:'text/event-stream',body:
        sse('start',{session_id:path.split('/')[3],model:'configured',execution_mode:'confirmed_action'})+
        sse('tool_started',{id:'application-confirmation-test',name:'submit_order_draft',arguments:JSON.stringify(approval().arguments)})+
        sse('approval_pending',approval())});
    }
    if (path === '/api/chat/approve/stream') {
      pending=false;
      return route.fulfill({contentType:'text/event-stream',body:
        sse('approval_resolved',{call_id:'application-confirmation-test',pending_id:'confirmed-pending',tool:'submit_order_draft',approved:false})+
        sse('delta',{text:'审批已拒绝，操作未执行。'})+
        sse('done',{steps:0,completed:true,usage:null,cost:null})});
    }
    return json({error:{message:'Unexpected route '+path}},404);
  });
  return posts;
}

for (const width of [320,1440]) {
  test(`确认 ${width}px 的具体草稿版本和全部报价后才申请审批`, async ({page}) => {
    await page.setViewportSize({width,height:1000});
    const posts = await mock(page);
    await page.goto('/');
    if (width < 1200) await page.getByRole('button',{name:'业务面板',exact:true}).click();
    await page.getByRole('button',{name:'请求提交版本 2 并审批',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'核对草稿版本 2'});
    await expect(dialog).toContainText('完整确认客户甲');
    await expect(dialog).toContainText('商品甲'); await expect(dialog).toContainText('商品乙');
    await expect(dialog).toContainText('总金额 ¥560.00'); await expect(dialog).toContainText(unsafe);
    await expect(dialog).toContainText('批准前不会建单或扣库存');
    if (width < 600) {
      const items = dialog.getByRole('list',{name:'逐项报价'});
      await expect(items).toBeVisible();
      await expect(items.getByRole('listitem').first()).toContainText('单价¥100.00');
      await expect(items.getByRole('listitem').first()).toContainText('小计¥500.00');
      expect(await items.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    }
    expect(posts).toEqual([]);
    expect(await page.evaluate(() => (window as unknown as Record<string,unknown>).erpilotConfirmationUnsafe)).toBeUndefined();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({path:`../../.playwright-mcp/draft-confirmation-${width}.png`});
    await dialog.getByRole('button',{name:'返回核对',exact:true}).click();
    expect(posts).toEqual([]);
    await page.getByRole('button',{name:'请求提交版本 2 并审批',exact:true}).click();
    await dialog.getByRole('button',{name:'确认此版本并申请审批',exact:true}).click();
    const deny = page.getByRole('button',{name:'拒绝',exact:true});
    await expect(deny).toBeEnabled();
    expect(posts).toHaveLength(1);
    expect(posts[0].path).toMatch(/\/order-drafts\/confirmed-target\/confirm\/stream$/);
    expect(posts[0].body).toEqual({expected_revision:2});
    expect(posts.some(post=>post.path==='/api/chat/stream')).toBe(false);
    await deny.click();
    await expect(page.locator('.md').last()).toContainText('操作未执行');
  });
}

test('确认后发现版本已变化时停下并展示新版本，不自动重提', async ({page}) => {
  const posts = await mock(page,true);
  await page.goto('/');
  await page.getByRole('button',{name:'请求提交版本 2 并审批',exact:true}).click();
  await page.getByRole('dialog',{name:'核对草稿版本 2'}).getByRole('button',{name:'确认此版本并申请审批',exact:true}).click();
  await expect(page.getByText('草稿已有新版本，请重新核对后确认',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'请求提交版本 3 并审批',exact:true})).toBeEnabled();
  expect(posts).toHaveLength(1); expect(posts[0].body).toEqual({expected_revision:2});
  await expect(page.getByRole('button',{name:'批准',exact:true})).toHaveCount(0);
});
