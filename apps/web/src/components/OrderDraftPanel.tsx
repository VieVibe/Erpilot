import { useEffect, useState } from 'react';
import { Alert, Button, Input, Modal, Select, Table, Tag, Tooltip } from 'antd';
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { draftBudgetIssue, draftSubmissionIssue, type DraftInput, type OrderDraft } from '../drafts';
import { DraftHistory } from './DraftHistory';

const money = (value: number) => '¥' + value.toFixed(2);
const statuses = { open: '待提交', expired: '已过期', superseded: '旧版本', submitted: '已提交' };
export function DraftSummary({ draft, review = false }: { draft: OrderDraft; review?: boolean }) {
  return <div className={'draft-summary' + (review ? ' draft-review-summary' : '')}>
    <p>{draft.customer} · 版本 {draft.revision} · <Tag>{statuses[draft.status]}</Tag></p>
    {review && <ul className="draft-review-items" aria-label="逐项报价">
      {draft.items.map(line => <li key={line.sku}>
        <strong>{line.name}</strong><span className="secondary-text">SKU：{line.sku}</span>
        <dl>
          <div><dt>数量</dt><dd>{line.quantity}</dd></div>
          <div><dt>原价</dt><dd>{money(line.base_price)}</dd></div>
          <div><dt>折扣</dt><dd>{Math.round(line.discount * 100)}%</dd></div>
          <div><dt>单价</dt><dd>{money(line.unit_price)}</dd></div>
          <div><dt>小计</dt><dd>{money(line.line_total)}</dd></div>
          <div><dt>报价时库存 → 预计提交后</dt><dd>{line.stock_before} → {line.stock_after}</dd></div>
        </dl>
      </li>)}
    </ul>}
    <div className="draft-table" role="region" aria-label="报价与库存影响">
      <Table size="small" pagination={false} rowKey="sku" dataSource={draft.items} scroll={{ x: 660 }} columns={[
        { title: '商品 / SKU', key: 'name', width: 160, render: (_, line) => <span>{line.name}<br /><span className="secondary-text">{line.sku}</span></span> },
        { title: '数量', dataIndex: 'quantity', width: 55 },
        { title: '原价', dataIndex: 'base_price', render: money },
        { title: '折扣', dataIndex: 'discount', render: value => Math.round(value * 100) + '%' },
        { title: '单价', dataIndex: 'unit_price', render: money },
        { title: '小计', dataIndex: 'line_total', render: money },
        { title: '报价时库存 → 预计提交后', key: 'stock', width: 180, render: (_, line) => line.stock_before + ' → ' + line.stock_after },
      ]} />
    </div>
    <p className="secondary-text">库存前后值为报价时测算，保存草稿不预留、不扣减库存；实际变化以订单提交结果为准。</p>
    <p aria-label="商品要求核对">商品要求：{draft.item_constraint?.verified ? (draft.item_constraint.origin.kind === 'manual_form' ? '已按表单逐项核对' : '已按明确清单及后续修改核对') : '尚无完整核验记录，请逐项核对商品与数量'}</p>
    {draft.item_constraint?.origin.kind === 'catalog_resolved' && <details>
      <summary>查看原始商品要求</summary>
      <ul>{draft.item_constraint.origin.requirements?.map(row => <li key={row.sku}>
        {row.quote} → {row.product_name}（{row.sku}），{row.quantity} 件
      </li>)}</ul>
      <p className="secondary-text">名称已与商品目录核对；请继续核对完整清单和报价后再申请审批。</p>
    </details>}
    <p><strong>总金额 {money(draft.total_amount)}</strong></p>
    <div className="draft-budget" aria-label="预算与待确认事项">
      <p>预算上限：{draft.budget_limit == null ? '未设置' : money(draft.budget_limit)}
        {draft.budget_constraint?.origin.kind === 'user_literal' && <Tag>用户原话</Tag>}
        {draft.budget_constraint?.origin.kind === 'manual_form' && <Tag>表单填写</Tag>}</p>
      {draft.budget_constraint?.origin.quote && <p>预算原话：{draft.budget_constraint.origin.quote}</p>}
      {!draftBudgetIssue(draft) && draft.budget_limit != null && <p>当前报价在预算范围内。</p>}
    </div>
    {!!draft.pending_issues?.length ? <div aria-label="待确认事项">{draft.pending_issues.map((issue, index) => <Alert key={`${issue.code}:${issue.sku ?? index}`} type="warning" showIcon title={issue.code === 'budget_exceeded' ? '待确认：报价超出预算' : `待确认：${issue.message}`} description={<>{issue.code === 'budget_exceeded' && <p>{issue.message}</p>}<p>{issue.required_action}</p><span>对应版本 {draft.revision}</span></>} />)}</div> : draftSubmissionIssue(draft) && draft.status !== 'submitted' && <Alert type="warning" showIcon title={draftBudgetIssue(draft) ? '待确认：报价超出预算' : '待确认事项'} description={draftSubmissionIssue(draft)} />}
    {draft.submission_check && <p className="secondary-text">提交条件核对于 <time dateTime={draft.submission_check.checked_at}>{new Date(draft.submission_check.checked_at).toLocaleString('zh-CN')}</time>；申请审批及执行时会再次核对。</p>}
    <p className="secondary-text">报价有效至 <time dateTime={draft.expires_at}>{new Date(draft.expires_at).toLocaleString('zh-CN')}</time></p>
    <p>备注：{draft.note || '无'}</p>{draft.order_id && <p>订单：{draft.order_id}</p>}
  </div>;
}
export function OrderDraftPanel({ sessionId, drafts, loading, error, busy, canSubmit, writesEnabled = true, hasPending, onRefresh, onSave, onSubmit, onIntentPreview }: {
  sessionId?: string;
  drafts: OrderDraft[]; loading: boolean; error: string; busy: boolean; canSubmit: boolean; writesEnabled?: boolean; hasPending: boolean;
  onRefresh: () => void; onSave: (draft: OrderDraft | undefined, input: DraftInput) => Promise<OrderDraft | void>; onSubmit: (draft: OrderDraft) => void;
  onIntentPreview?: (draft: OrderDraft, source: string) => Promise<void>;
}) {
  const [selected, setSelected] = useState<string | null | undefined>(undefined);
  const draft = selected === null ? undefined : drafts.find(item => item.draft_id === selected) ?? drafts[0];
  const [customer, setCustomer] = useState(''); const [note, setNote] = useState('');
  const [budget, setBudget] = useState('');
  const [lines, setLines] = useState([{ sku: '', quantity: '1' }]);
  const [saving, setSaving] = useState(false); const [saveError, setSaveError] = useState('');
  const [revisionSource, setRevisionSource] = useState('');
  const [review, setReview] = useState<OrderDraft | null>(null);
  const [clock, setClock] = useState(Date.now());
  useEffect(() => { setReview(null); }, [sessionId]);
  useEffect(() => {
    setClock(Date.now());
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    setRevisionSource('');
    setCustomer(draft?.customer ?? ''); setNote(draft?.note ?? '');
    setBudget(draft?.budget_limit == null ? '' : String(draft.budget_limit));
    setLines(draft?.items.map(item => ({ sku: item.sku, quantity: String(item.quantity) })) ?? [{ sku: '', quantity: '1' }]); setSaveError('');
  }, [draft?.draft_id, draft?.revision]);
  const original = draft ? JSON.stringify({ customer: draft.customer, note: draft.note ?? '', items: draft.items.map(item => ({ sku: item.sku, quantity: String(item.quantity) })) }) : '';
  const dirty = original !== JSON.stringify({ customer, note, items: lines }) || budget !== (draft?.budget_limit == null ? '' : String(draft.budget_limit));
  const budgetValid = budget === '' || (/^\d+(?:\.\d{1,2})?$/.test(budget) && Number(budget) > 0 && Number(budget) <= 1e12);
  const valid = budgetValid && !!customer.trim() && lines.length > 0 && lines.every(line => !!line.sku.trim() && Number.isSafeInteger(Number(line.quantity)) && Number(line.quantity) > 0);
  const needsSelection = selected === undefined && drafts.filter(item => item.status !== 'submitted').length > 1;
  const locked = busy || saving || needsSelection || draft?.status === 'submitted';
  const reviewedCurrent = review && drafts.find(item => item.draft_id === review.draft_id);
  const reviewStale = !!review && (!reviewedCurrent || reviewedCurrent.revision !== review.revision || reviewedCurrent.status !== 'open' || Date.parse(review.expires_at) <= clock);
  const reviewIssue = review ? draftSubmissionIssue(reviewedCurrent ?? review, clock) : null;
  return <section className="draft-panel" aria-label="订单草稿" aria-busy={loading || saving}>
    <div className="draft-heading"><h2>准备订单</h2><div>
      <Button size="small" icon={<PlusOutlined />} onClick={() => { setSelected(null); setCustomer(''); setNote(''); setBudget(''); setLines([{ sku: '', quantity: '1' }]); setSaveError(''); }} disabled={busy || saving}>新建草稿</Button>
      <Tooltip title="刷新草稿"><Button type="text" size="small" icon={<ReloadOutlined />} aria-label="刷新草稿" onClick={onRefresh} disabled={loading || saving} /></Tooltip>
    </div></div>
    {drafts.length > 0 && <label>选择草稿<Select aria-label="选择草稿" placeholder="请选择目标草稿" value={needsSelection ? undefined : draft?.draft_id ?? ''} onChange={value => setSelected(value || null)} disabled={saving}
      options={[{ value: '', label: '新草稿' }, ...drafts.map(item => ({ value: item.draft_id, label: item.customer + ' · 版本 ' + item.revision }))]} /></label>}
    {needsSelection && <Alert type="info" showIcon title="有多份未提交草稿，请先在上方明确选择目标。" />}
    {loading && <p className="secondary-text" role="status">正在核对草稿…</p>}{error && <Alert type="error" title={error} showIcon />}
    {onIntentPreview && draft && <section aria-label="按原话修改草稿">
      <label>修改原话<Input.TextArea aria-label="修改原话" value={revisionSource} maxLength={32000} placeholder="例如：调整机械键盘到六个，预算改为1200元" disabled={locked || loading || hasPending} onChange={e => setRevisionSource(e.target.value)} /></label>
      <Button disabled={locked || loading || hasPending || dirty || draft.status !== 'open' || Date.parse(draft.expires_at) <= clock || !revisionSource.trim()} onClick={async () => {
        if (locked || dirty || !revisionSource.trim()) return;
        setSaving(true); setSaveError('');
        try {await onIntentPreview(draft, revisionSource); setRevisionSource('');}
        catch(e) {setSaveError(e instanceof Error ? e.message : String(e));}
        finally {setSaving(false);}
      }}>按原话生成修改预览</Button>
    </section>}
    <form onSubmit={async event => {
      event.preventDefault(); if (!valid || locked) return; setSaving(true); setSaveError('');
      try { const saved = await onSave(draft, { customer: customer.trim(), note, budget_limit: budget === '' ? null : Number(budget), items: lines.map(line => ({ sku: line.sku.trim(), quantity: Number(line.quantity) })) }); if (saved) setSelected(saved.draft_id); }
      catch (error) { setSaveError(error instanceof Error ? error.message : String(error)); } finally { setSaving(false); }
    }}>
      <label>客户<Input aria-label="草稿客户" placeholder="输入客户名称" value={customer} onChange={event => setCustomer(event.target.value)} disabled={locked} required /></label>
      {lines.map((line, index) => <div className="draft-line" key={index}>
        <label>商品 SKU<Input aria-label={'第 ' + (index + 1) + ' 行 SKU'} placeholder="商品编码" value={line.sku} onChange={event => setLines(prev => prev.map((item, i) => i === index ? { ...item, sku: event.target.value } : item))} disabled={locked} required /></label>
        <label>数量<Input aria-label={'第 ' + (index + 1) + ' 行数量'} type="number" min="1" step="1" value={line.quantity} onChange={event => setLines(prev => prev.map((item, i) => i === index ? { ...item, quantity: event.target.value } : item))} disabled={locked} required /></label>
        <Button type="text" icon={<DeleteOutlined />} aria-label={'删除第 ' + (index + 1) + ' 行'} onClick={() => setLines(prev => prev.filter((_, i) => i !== index))} disabled={locked || lines.length === 1} />
      </div>)}
      <label>备注<Input.TextArea aria-label="草稿备注" placeholder="补充订单要求（选填）" value={note} onChange={event => setNote(event.target.value)} disabled={locked} autoSize={{ minRows: 2, maxRows: 4 }} /></label>
      <label>预算上限（元，选填）<Input aria-label="预算上限" inputMode="decimal" value={budget} onChange={event => setBudget(event.target.value)} disabled={locked} placeholder="例如 3000.00" status={budgetValid ? undefined : 'error'} /></label>
      <p className="secondary-text">留空表示未设置预算；清空已保存预算后，需要保存新版本。超预算可继续编辑，不能提交。</p>
      <div className="draft-actions"><Button icon={<PlusOutlined />} onClick={() => setLines(prev => [...prev, { sku: '', quantity: '1' }])} disabled={locked}>添加商品</Button>
        <Button type="primary" htmlType="submit" loading={saving} disabled={locked || !valid}>{draft ? '保存 / 刷新报价' : '创建草稿'}</Button></div>
    </form>
    {saveError && <Alert type="error" title={saveError} showIcon />}
    {draft && <><DraftSummary draft={draft} /><Button className="draft-submit" disabled={!writesEnabled || !canSubmit || saving || dirty || loading || needsSelection || !!draftSubmissionIssue(draft, clock)} onClick={() => setReview(structuredClone(draft))}>请求提交版本 {draft.revision} 并审批</Button></>}
    <Modal title={review ? `核对草稿版本 ${review.revision}` : '核对草稿'} open={!!review} width={820}
      okText="确认此版本并申请审批" cancelText="返回核对" onCancel={() => setReview(null)}
      okButtonProps={{disabled: reviewStale || !!reviewIssue || !writesEnabled || !canSubmit || busy || saving || dirty || loading || hasPending}}
      onOk={() => {
        if (!review || reviewStale || reviewIssue || !writesEnabled || !canSubmit || busy || saving || dirty || loading || hasPending) return;
        onSubmit(review); setReview(null);
      }} styles={{body: {maxHeight: '65vh', overflowY: 'auto'}}}>
      <p>请核对客户、备注、全部商品与总金额。确认后申请人工审批；批准前不会建单或扣库存。</p>
      {review && <DraftSummary draft={review} review />}
      {reviewStale && <Alert type="warning" title="当前草稿无法核对、已变化或过期，请返回重新核对。" showIcon />}
      {!reviewStale && reviewIssue && <Alert type="warning" title="提交条件已变化，请返回核对待确认事项。" description={reviewIssue} showIcon />}
    </Modal>
    {draft && sessionId && <DraftHistory key={`${sessionId}:${draft.draft_id}:${draft.revision}`} sessionId={sessionId} draftId={draft.draft_id} />}
    {!writesEnabled && <p className="secondary-text" role="status">草稿可保存，下单未开放。</p>}
    {dirty && draft && <p className="secondary-text" role="status">修改后请先保存，以新的报价版本申请审批。</p>}
    {hasPending && <p className="secondary-text" role="status">已有待处理审批；修改草稿后，请先拒绝旧审批，再提交新版本。</p>}
  </section>;
}
