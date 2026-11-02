import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Select } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { confirmIntentPreview, getIntentPreview, type IntentPreview } from '../intent-preview';

export function IntentPreviewPanel({sessionId, refresh, busy, onBusy, onSaved}: {
  sessionId: string; refresh: number; busy: boolean;
  onBusy: (value: boolean) => void; onSaved: () => void;
}) {
  const [preview, setPreview] = useState<IntentPreview | null>(null);
  const [editor, setEditor] = useState<IntentPreview | null>(null);
  const [customer, setCustomer] = useState('');
  const [note, setNote] = useState(''); const [budget, setBudget] = useState('');
  const [items, setItems] = useState<{sku: string; quantity: string; original?: number}[]>([]);
  const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false);
  const [error, setError] = useState(''); const [saveError, setSaveError] = useState('');
  const [now, setNow] = useState(Date.now()); const [retry, setRetry] = useState(0);
  const mounted = useRef(true); const autoOpened = useRef(new Set<string>());
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const open = (value: IntentPreview) => {
    setEditor(structuredClone(value)); setCustomer(value.customer); setNote(value.note ?? '');
    setBudget(value.budget_limit == null ? '' : String(value.budget_limit));
    setItems(value.items.map((r, index) => ({sku: r.sku ?? '', quantity: r.quantity == null ? '' : String(r.quantity), original: index})));
    setSaveError('');
  };
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    void getIntentPreview(sessionId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setPreview(value); setError('');
      if (value && value.can_confirm && (value.clarification_attempts >= 2 || value.operation === 'revise') && !editor) {
        const key = `${value.pending_id}:${value.state_version}`;
        if (!autoOpened.current.has(key)) {autoOpened.current.add(key); open(value);}
      }
    }).catch(e => {if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e));})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [sessionId, refresh, retry]);
  const stale = !editor || !preview || preview.pending_id !== editor.pending_id || preview.state_version !== editor.state_version || !preview.can_confirm || Date.parse(editor.expires_at) <= now;
  const budgetValid = budget === '' || (/^\d+(?:\.\d{1,2})?$/.test(budget) && Number(budget) > 0 && Number(budget) <= 1e12);
  const valid = !!customer.trim() && customer.trim().length <= 64 && note.length <= 255 && budgetValid && items.length > 0 && items.length <= 100 && items.every(r => !!r.sku.trim() && r.sku.trim().length <= 32 && /^[1-9][0-9]{0,8}$/.test(r.quantity)) && new Set(items.map(r => r.sku.trim())).size === items.length;
  const locked = busy || saving;
  if (!preview && !editor && !error) return null;
  return <section className="draft-panel intent-preview" aria-label="待完成请求核对">
    <h2>待完成的订单请求</h2>
    {error ? <Alert type="error" showIcon title={error} action={<Button onClick={() => setRetry(r => r + 1)}>重新核对</Button>} /> : preview && <>
      <p>{preview.status === 'needs_recheck' ? '原请求需要重新核对，请重新说明完整要求。' : preview.origin === 'semantic_proposal' ? '已生成待核对的候选字段。请打开表单，对照完整原话逐项确认。' : '已保留客户、备注、预算和完整商品清单。可继续回复补充，也可打开表单逐项核对。'}</p>
      {preview.message && <Alert type="warning" showIcon title={preview.message} />}
      <Button onClick={() => open(preview)} disabled={locked || loading || !preview.can_confirm || Date.parse(preview.expires_at) <= now}>核对完整请求</Button>
    </>}
    <Modal title="核对待完成请求" open={!!editor} width={780} cancelText="继续补充" okText={editor?.operation === 'revise' ? '确认目标并保存新版本' : '按此完整清单保存草稿'}
      onCancel={() => {if (!saving) setEditor(null);}} maskClosable={!saving} closable={!saving}
      cancelButtonProps={{disabled: saving}} okButtonProps={{disabled: locked || loading || !!error || stale || !valid, loading: saving}}
      styles={{body: {maxHeight: '65vh', overflowY: 'auto'}}}
      onOk={async () => {
        if (!editor || locked || loading || error || stale || !valid) return;
        setSaving(true); setSaveError(''); onBusy(true);
        try {
          await confirmIntentPreview(sessionId, editor, {customer: customer.trim(), note: note || null, budget_limit: budget === '' ? null : Number(budget), items: items.map(r => ({sku: r.sku.trim(), quantity: Number(r.quantity)}))});
          if (mounted.current) {setEditor(null); setPreview(null); onSaved();}
        } catch (e) {if (mounted.current) {setSaveError(e instanceof Error ? e.message : String(e)); setRetry(r => r + 1);}}
        finally {if (mounted.current) {setSaving(false); onBusy(false);}}
      }}>
      <div className="draft-panel">
        {editor?.origin === 'semantic_proposal' && <>
          <Alert type="warning" showIcon title="以下候选尚未确认，请对照原话核对全部字段和未展开要求。" />
          <p>完整原话</p><blockquote style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{editor.source_text}</blockquote>
          {editor.unmapped_text?.trim() && <details><summary>尚未展开为字段的原文</summary><p style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{editor.unmapped_text}</p></details>}
        </>}
        {editor?.operation === 'revise' && editor.before_fields && <section aria-label="修改前清单">
          <h3>目标草稿：{editor.before_fields.customer} · 版本 {editor.target?.revision}</h3>
          <p style={{overflowWrap: 'anywhere'}}>草稿标识：{editor.target?.draft_id}</p>
          <p>原客户：{editor.before_fields.customer}；原备注：{editor.before_fields.note ?? '无'}；原预算：{editor.before_fields.budget_limit ?? '未设置'}</p>
          <ul>{editor.before_fields.items.map(r => <li key={r.sku}>{r.sku}：{r.quantity} 件</li>)}</ul>
          <h3>候选完整清单</h3>
        </section>}
        <p>{editor?.operation === 'revise' ? '请核对目标版本、原清单与以下全部字段。确认后保存为同一草稿的新版本并重新报价。' : '请逐项核对以下全部字段。确认后按表单创建新草稿并重新报价；'}保存不会提交订单或扣减库存。</p>
        <form onSubmit={event => event.preventDefault()}>
          <label>客户<Input aria-label="核对客户" value={customer} maxLength={64} onChange={e => setCustomer(e.target.value)} disabled={locked} /></label>
          {items.map((row, index) => {
            const source = row.original === undefined ? undefined : editor?.items[row.original];
            return <div key={index}>
            {source && <p className="secondary-text">原第 {source.item_number} 项：{source.product_query}</p>}
            {!!source?.candidates.length && <label>选择商品<Select aria-label={`核对第 ${index + 1} 项候选`} value={row.sku || undefined} placeholder="选择实际商品" disabled={locked}
              onChange={value => setItems(rows => rows.map((r, i) => i === index ? {...r, sku: value} : r))}
              options={source.candidates.map(r => ({value: r.sku, label: `${r.name} · ${r.sku} · ${r.status}`, disabled: r.status !== '在售'}))} /></label>}
            <div className="draft-line">
              <label>商品 SKU<Input aria-label={`核对第 ${index + 1} 行 SKU`} value={row.sku} maxLength={32} disabled={locked} onChange={e => setItems(rows => rows.map((r, i) => i === index ? {...r, sku: e.target.value} : r))} /></label>
              <label>最终件数<Input aria-label={`核对第 ${index + 1} 行件数`} type="number" min="1" step="1" value={row.quantity} disabled={locked} onChange={e => setItems(rows => rows.map((r, i) => i === index ? {...r, quantity: e.target.value} : r))} /></label>
              <Button type="text" icon={<DeleteOutlined />} aria-label={`移除核对第 ${index + 1} 行`} disabled={locked || items.length === 1} onClick={() => setItems(rows => rows.filter((_, i) => i !== index))} />
            </div>
          </div>;})}
          <Button icon={<PlusOutlined />} disabled={locked || items.length >= 100} onClick={() => setItems(rows => [...rows, {sku: '', quantity: ''}])}>添加核对商品</Button>
          <label>预算上限（元，留空表示未设置）<Input aria-label="核对预算" inputMode="decimal" value={budget} disabled={locked} onChange={e => setBudget(e.target.value)} status={budgetValid ? undefined : 'error'} /></label>
          <label>备注（留空表示无备注）<Input.TextArea aria-label="核对备注" value={note} maxLength={255} disabled={locked} onChange={e => setNote(e.target.value)} autoSize={{minRows: 2, maxRows: 4}} /></label>
        </form>
        {stale && <Alert type="warning" title="请求已变化或过期，请关闭窗口后重新核对。" showIcon />}
        {saveError && <Alert type="error" title={saveError} showIcon />}
      </div>
    </Modal>
  </section>;
}
