import { Card, Tag } from 'antd';
import type { CancellationAssessmentDisplay } from '../protocol';
import { record, text, time, number, validReferences } from '../policyEvidence';
import { PolicyReferences } from './PolicyReferences';

export function isUsableAssessmentDisplay(value: unknown): value is CancellationAssessmentDisplay {
  if (!record(value) || value.version !== 1 || value.kind !== 'cancellation_assessment' ||
      value.outcome !== 'succeeded') return false;
  const proof = value.assessment;
  if (!record(proof) || proof.schema_version !== 1 || !time(proof.assessed_at) ||
      !text(proof.message) || typeof proof.business_allowed !== 'boolean') return false;
  const order = proof.order;
  if (!record(order) || !text(order.order_id) || !text(order.customer) || !text(order.status) ||
      !number(order.total_amount, 0) ||
      !['待付款','待发货','已发货','已签收','已取消','已退款'].includes(order.status)) return false;
  if (proof.business_allowed !== ['待付款','待发货'].includes(order.status)) return false;
  const reason = proof.business_reason;
  if (!record(reason) || !text(reason.message) ||
      reason.code !== (proof.business_allowed ? 'cancellable_status' : 'invalid_transition')) return false;
  if (typeof proof.policy_state !== 'string' ||
      !['matched','no_match','unavailable','inactive','conflict'].includes(proof.policy_state)) return false;
  const expected = !proof.business_allowed ? 'ineligible' :
    proof.policy_state === 'matched' ? 'eligible' : 'needs_review';
  if (proof.decision !== expected || !Array.isArray(proof.references) ||
      (proof.references.length > 0) !== (proof.policy_state === 'matched')) return false;
  return validReferences(proof.references, proof.assessed_at);
}

const labels = {eligible: '可发起取消申请', ineligible: '当前不可取消', needs_review: '政策待核对'};
const policyLabels = {matched: '政策依据有效', no_match: '未找到相关依据', unavailable: '政策检索暂不可用',
  inactive: '没有当前生效政策', conflict: '政策版本或规则冲突'};

export function CancellationAssessmentCard({display}: {display: CancellationAssessmentDisplay}) {
  if (!isUsableAssessmentDisplay(display)) return null;
  const proof = display.assessment;
  return <Card size="small" className="card cancellation-assessment" title="取消条件查询"
    extra={<Tag>查询完成</Tag>}>
    <div className="card-body">
      <Tag color={proof.decision === 'eligible' ? 'processing' : proof.decision === 'needs_review' ? 'warning' : undefined}>
        {labels[proof.decision]}</Tag>
      <p>订单 <strong>{proof.order.order_id}</strong> · {proof.order.customer} · {proof.order.status}</p>
      <p>原单金额 ¥{proof.order.total_amount.toFixed(2)}</p>
      <p>{proof.business_reason.message} · {policyLabels[proof.policy_state]}</p>
      <p>{proof.message}</p>
      <p>查询时间 <time dateTime={proof.assessed_at}>{proof.assessed_at}</time></p>
      {proof.references.length > 0 && <details><summary>查看政策依据</summary>
        <PolicyReferences references={proof.references} query="订单取消条件"/>
      </details>}
    </div>
  </Card>;
}
