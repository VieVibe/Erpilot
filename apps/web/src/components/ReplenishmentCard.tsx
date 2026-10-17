import { Card, Tag } from 'antd';
import type { ReplenishmentDisplay } from '../protocol';

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
const integer = (value: unknown, low = 0, high = Number.MAX_SAFE_INTEGER): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= low && value <= high;
const number = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0;
const time = (value: unknown): value is string => typeof value === 'string' &&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+08:00$/.test(value) && Number.isFinite(Date.parse(value));

export function isUsableReplenishmentDisplay(value: unknown): value is ReplenishmentDisplay {
  if (!record(value) || value.version !== 1 || value.kind !== 'replenishment_suggestion' ||
      value.outcome !== 'succeeded') return false;
  const proof = value.suggestion;
  if (!record(proof) || proof.schema_version !== 1 || proof.timezone !== 'Asia/Shanghai' ||
      !time(proof.assessed_at) || !time(proof.window_start) || !time(proof.window_end) ||
      proof.assessed_at !== proof.window_end || !integer(proof.days, 1, 365) ||
      !integer(proof.coverage_days, 1, 180) || !integer(proof.limit, 1, 50) ||
      !integer(proof.offset) || !integer(proof.total) || !integer(proof.returned) ||
      !text(proof.sales_basis) || !text(proof.formula) ||
      Date.parse(proof.window_end) - Date.parse(proof.window_start) !== proof.days * 86400000 ||
      (proof.lead_time_days !== null && !integer(proof.lead_time_days, 0, 180))) return false;
  for (const key of ['assumptions', 'limitations']) {
    const values = proof[key];
    if (!Array.isArray(values) || values.length === 0 || !values.every(text)) return false;
  }
  if (!Array.isArray(proof.items) || proof.returned !== proof.items.length ||
      proof.returned !== Math.min(proof.limit, Math.max(0, proof.total - proof.offset)) ||
      typeof proof.has_more !== 'boolean' || proof.has_more !== (proof.offset + proof.returned < proof.total)) return false;
  const seen = new Set<string>();
  for (const item of proof.items) {
    if (!record(item) || !text(item.sku) || !text(item.name) || !text(item.product_status) ||
        !text(item.reason) || seen.has(item.sku)) return false;
    seen.add(item.sku);
    const stock = item.stock_quantity, sold = item.sold_quantity;
    if ((stock !== null && !integer(stock)) || (sold !== null && !integer(sold))) return false;
    if (sold === null ? item.daily_sales !== null : !number(item.daily_sales) ||
        Math.abs(item.daily_sales - (sold as number) / proof.days) > 0.000051) return false;
    if (!['reorder', 'sufficient', 'no_sales', 'off_sale', 'data_issue'].includes(String(item.decision))) return false;
    if (item.decision === 'reorder' || item.decision === 'sufficient') {
      if (!integer(stock) || !integer(sold, 1) || item.product_status !== '在售' ||
          !integer(item.target_quantity) || !integer(item.suggested_quantity) ||
          !number(item.estimated_days_remaining)) return false;
      if ((item.decision === 'reorder') !== (item.suggested_quantity > 0) ||
          item.suggested_quantity !== Math.max(0, item.target_quantity - stock)) return false;
      if (proof.lead_time_days === null ? item.stockout_before_arrival !== null :
          typeof item.stockout_before_arrival !== 'boolean') return false;
    } else {
      if (['target_quantity', 'suggested_quantity', 'estimated_days_remaining', 'stockout_before_arrival']
          .some(key => item[key] !== null)) return false;
      if (item.decision === 'no_sales' && (sold !== 0 || stock === null || item.product_status !== '在售')) return false;
      if (item.decision === 'off_sale' && item.product_status !== '已下架') return false;
    }
  }
  return true;
}

const labels = {reorder: '建议补货', sufficient: '覆盖目标量', no_sales: '销量依据不足',
  off_sale: '商品已下架', data_issue: '数据待核对'};
const quantity = (value: number | null) => value === null ? '待核对' : `${value} 件`;

export function ReplenishmentCard({display}: {display: ReplenishmentDisplay}) {
  if (!isUsableReplenishmentDisplay(display)) return null;
  const proof = display.suggestion;
  return <Card size="small" className="card replenishment-suggestion" title="补货建议"
    extra={<Tag>只读测算</Tag>}>
    <div className="card-body">
      <p>最近 {proof.days} 天 · 到货后覆盖 {proof.coverage_days} 天 · 采购周期{proof.lead_time_days === null ? '未知' : ` ${proof.lead_time_days} 天`}</p>
      <p>共 {proof.total} 个商品，当前展示 {proof.returned === 0 ? '0' : `${proof.offset + 1}–${proof.offset + proof.returned}`} 项{proof.has_more ? '；还有更多，可继续查询下一页。' : '。'}</p>
      {proof.assumptions.map((assumption, index) => <p key={index}>{assumption}</p>)}
      {proof.items.length === 0 && <p>当前页没有商品。</p>}
      <ul className="replenishment-items">{proof.items.map(item => <li key={item.sku}>
        <p><strong>{item.name}</strong> · {item.sku} <Tag color={item.decision === 'reorder' ? 'warning' : undefined}>{labels[item.decision]}</Tag></p>
        <dl className="replenishment-facts">
          <div><dt>当前库存</dt><dd>{quantity(item.stock_quantity)}</dd></div>
          <div><dt>有效销量</dt><dd>{quantity(item.sold_quantity)}</dd></div>
          <div><dt>日均销量</dt><dd>{item.daily_sales === null ? '待核对' : `${item.daily_sales} 件/天`}</dd></div>
          <div><dt>预计可售</dt><dd>{item.estimated_days_remaining === null ? '无法测算' : `${item.estimated_days_remaining} 天`}</dd></div>
          <div><dt>目标数量</dt><dd>{item.target_quantity === null ? '未测算' : `${item.target_quantity} 件`}</dd></div>
          <div><dt>建议补货</dt><dd>{item.suggested_quantity === null ? '未给出数量' : `${item.suggested_quantity} 件`}</dd></div>
        </dl>
        <p>{item.reason}</p>
        <p>到货前断货风险：{item.stockout_before_arrival === null ? '未判断' : item.stockout_before_arrival ? '按当前均值存在风险' : '按当前均值未见风险'}</p>
      </li>)}</ul>
      <details><summary>查看计算依据与限制</summary>
        <p>统计区间 [{proof.window_start}, {proof.window_end}) · 北京时间</p>
        <p>{proof.sales_basis}</p><p>{proof.formula}</p>
        {proof.limitations.map((limitation, index) => <p key={index}>{limitation}</p>)}
        <p>查询时间 <time dateTime={proof.assessed_at}>{proof.assessed_at}</time></p>
      </details>
      <p>本次仅提供建议，未采购、未调整库存。</p>
    </div>
  </Card>;
}
