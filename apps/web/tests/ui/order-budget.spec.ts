import { test, expect } from '@playwright/test';

for (const width of [320,1440]) {
  test(`预算在 ${width}px 跨刷新保留，超额阻止提交，明确调整后重新核对`, async ({page}) => {
    await page.setViewportSize({width,height:1000});
    let current = {draft_id:'budget-draft',revision:1,customer:'完整预算客户',note:'保留原要求',
      items:[{sku:'A',name:'预算测试商品',quantity:4,base_price:800,discount:1,unit_price:800,line_total:3200,stock_before:100,stock_after:96}],
      total_amount:3200,budget_limit:3000,budget_constraint:{currency:'CNY',limit:3000,origin:{kind:'manual_form',revision:1}},
      expires_at:'2099-01-01T00:00:00Z',status:'open',order_id:null};
    const writes: {path:string;body:Record<string,unknown>}[]=[];
    await page.route('**/api/**', async route=>{
      const req=route.request(), path=new URL(req.url()).pathname;
      if (req.method()==='PATCH' || req.method()==='POST') writes.push({path,body:req.postDataJSON()});
      const json=(body:unknown)=>route.fulfill({json:body});
      if(path==='/api/runtime')return json({drafts_enabled:true,writes_enabled:true,data_source:'mcp'});
      if(path.endsWith('/state'))return json({session_id:path.split('/')[3],status:'completed',messages:[],tool_results:[],pending_approvals:[]});
      if(path==='/api/sessions')return json({items:[],has_more:false});
      if(path.endsWith('/order-drafts'))return json({items:[current]});
      if(path.endsWith('/budget-draft')){
        if(req.method()==='PATCH'){
          const body=req.postDataJSON();
          current={...current,budget_limit:body.budget_limit,revision:2,
            budget_constraint:{currency:'CNY',limit:body.budget_limit,origin:{kind:'manual_form',revision:2}}};
        }
        return json(current);
      }
      return route.fulfill({status:404,json:{error:{message:'Unexpected '+path}}});
    });
    const open=async()=>{if(width<1200)await page.getByRole('button',{name:'业务面板',exact:true}).click();};
    await page.goto('/'); await open();
    await expect(page.getByRole('textbox',{name:'预算上限',exact:true})).toHaveValue('3000');
    await expect(page.getByText('待确认：报价超出预算',{exact:true})).toBeVisible();
    await expect(page.getByRole('button',{name:'请求提交版本 1 并审批',exact:true})).toBeDisabled();
    await page.getByText('待确认：报价超出预算',{exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:`../../.playwright-mcp/order-budget-blocked-${width}.png`,animations:'disabled'});
    await page.reload(); await open();
    await expect(page.getByRole('textbox',{name:'预算上限',exact:true})).toHaveValue('3000');
    expect(writes).toEqual([]);
    await page.getByRole('textbox',{name:'预算上限',exact:true}).fill('3500');
    await page.getByRole('button',{name:'保存 / 刷新报价',exact:true}).click();
    await expect(page.getByRole('button',{name:'请求提交版本 2 并审批',exact:true})).toBeEnabled();
    expect(writes).toHaveLength(1);expect(writes[0].body.budget_limit).toBe(3500);
    expect(writes[0].body.expected_revision).toBe(1);
    await page.getByRole('button',{name:'请求提交版本 2 并审批',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'核对草稿版本 2'});
    await expect(dialog).toContainText('预算上限：¥3500.00');
    await expect(dialog).toContainText('当前报价在预算范围内。');
    await expect(dialog).toHaveCSS('transform','none');
    await expect(dialog).toHaveCSS('opacity','1');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`../../.playwright-mcp/order-budget-${width}.png`});
    await dialog.getByRole('button',{name:'返回核对',exact:true}).click();
    expect(writes).toHaveLength(1);
  });
}
