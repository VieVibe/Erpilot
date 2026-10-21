import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Tag } from 'antd';
import { describeDraftChange, getOrderDraftHistory, type DraftHistoryPage } from '../drafts';

const origins = { user_literal: '用户原话', manual_form: '表单填写', unverified: '来源未核验', legacy_snapshot: '旧版本未记录来源' };
const operations = { create: '创建草稿', revise: '修改或刷新报价', revise_quantity: '修改数量' };
export function DraftHistory({ sessionId, draftId }: { sessionId: string; draftId: string }) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<DraftHistoryPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => { pending.current?.abort(); }, []);
  async function load(before?: number) {
    pending.current?.abort();
    const controller = new AbortController(); pending.current = controller;
    setLoading(true); setError('');
    try {
      const next = await getOrderDraftHistory(sessionId, draftId, before, controller.signal);
      if (controller.signal.aborted) return;
      setPage(previous => before === undefined ? next : { ...next, items: [...(previous?.items ?? []), ...next.items] });
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : String(failure));
    } finally { if (!controller.signal.aborted) setLoading(false); }
  }
  return <section className="draft-history" aria-label="草稿修改记录">
    <Button type="text" aria-expanded={open} onClick={() => {
      setOpen(!open); if (!open && !page) void load();
    }}>{open ? '收起修改记录' : '查看修改记录'}</Button>
    {open && <>
      <p className="secondary-text">以下是草稿报价版本的变化，保存不扣库存；历史请求不会再次执行。</p>
      {error && <Alert type="error" title={error} action={<Button size="small" onClick={() => void load(page?.next_before_revision ?? undefined)}>重试</Button>} />}
      {loading && <p role="status">正在读取修改记录…</p>}
      {page?.items.map(item => <article className="draft-history-item" key={item.revision} aria-label={`草稿版本 ${item.revision} 修改记录`}>
        <p><strong>版本 {item.revision}</strong> · {item.change_record ? operations[item.change_record.operation] : '旧版本记录'}<br />
          <time className="secondary-text" dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('zh-CN')}</time></p>
        {item.change_record ? <>
          {item.change_record.changes.length ? <ul>{item.change_record.changes.map((change, index) => <li key={index}>{describeDraftChange(change)}</li>)}</ul> : <p>明细未变化，已刷新报价有效期。</p>}
          {item.change_record.source.kind === 'agent' ? <details><summary>本次用户请求</summary><blockquote>{item.change_record.source.user_request}</blockquote></details> : <p className="secondary-text">{item.change_record.source.kind === 'manual_form' ? '本次通过草稿表单保存。' : '本次操作未记录可核验的用户原话。'}</p>}
        </> : <p className="secondary-text">此版本未记录修改来源，不能补推原始要求。</p>}
        <dl>{(['customer', 'note'] as const).map(field => {
          const constraint = item.task_constraints.fields[field];
          return <div key={field}><dt>{field === 'customer' ? '客户要求' : '备注要求'} <Tag>{origins[constraint.origin.kind]}</Tag></dt>
            <dd>{constraint.value === null ? '无' : constraint.value || '空文本'}</dd>
            {constraint.origin.quote && <dd className="secondary-text">原话：{constraint.origin.quote}</dd>}
          </div>;
        })}</dl>
        {item.budget_constraint && <div><p>预算上限：{item.budget_constraint.limit == null ? '未设置' : `¥${item.budget_constraint.limit.toFixed(2)}`} · {item.budget_constraint.origin.kind === 'user_literal' ? '用户原话' : item.budget_constraint.origin.kind === 'manual_form' ? '表单填写' : '来源未核验'}</p>
          {item.budget_constraint.origin.quote && <p>预算原话：{item.budget_constraint.origin.quote}</p>}</div>}
      </article>)}
      {page && !page.items.length && <p>暂无修改记录。</p>}
      {page?.has_more && <Button onClick={() => void load(page.next_before_revision!)} loading={loading}>查看更早版本</Button>}
    </>}
  </section>;
}
