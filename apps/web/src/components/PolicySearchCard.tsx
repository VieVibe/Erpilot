import { Card, Tag } from 'antd';
import type { PolicySearchDisplay } from '../protocol';
import { record, text, time, validReferences } from '../policyEvidence';
import { PolicyReferences } from './PolicyReferences';

export function isUsablePolicySearchDisplay(value: unknown): value is PolicySearchDisplay {
  if (!record(value) || value.version !== 1 || value.kind !== 'policy_search' || !text(value.query)) return false;
  if (value.outcome === 'failed') return text(value.message) &&
    (value.hint === undefined || typeof value.hint === 'string');
  if (value.outcome !== 'succeeded') return false;
  const proof = value.search;
  return record(proof) && proof.schema_version === 1 && proof.query === value.query &&
    time(proof.queried_at) && typeof proof.note === 'string' &&
    typeof proof.policy_state === 'string' && ['matched','no_match','unavailable','inactive','conflict'].includes(proof.policy_state) &&
    validReferences(proof.matches, proof.queried_at) && proof.matches.length <= 5 &&
    (proof.matches.length > 0) === (proof.policy_state === 'matched');
}

const labels = {matched: '找到政策依据', no_match: '未找到相关依据', unavailable: '政策检索暂不可用',
  inactive: '没有当前生效政策', conflict: '政策版本冲突'};

export function PolicySearchCard({display}: {display: PolicySearchDisplay}) {
  if (!isUsablePolicySearchDisplay(display)) return null;
  const proof = display.outcome === 'succeeded' ? display.search : undefined;
  return <Card size="small" className="card policy-search" title="政策检索证据"
    extra={<Tag color={proof?.policy_state === 'matched' ? 'processing' : 'warning'}>
      {proof ? labels[proof.policy_state] : '政策检索未完成'}</Tag>}>
    <div className="card-body">
      <p className="policy-query">查询：<strong>{display.query}</strong></p>
      {proof ? <>
        <p className="secondary-text">查询时间 <time dateTime={proof.queried_at}>{proof.queried_at}</time>
          {' · '}{proof.matches.length} 条依据</p>
        {proof.matches.length ? <PolicyReferences references={proof.matches} query={proof.query}/> :
          <p>本次检索没有可引用的政策依据，需核对政策或补充问题，不能据此作出政策结论。</p>}
      </> : <><p>{display.message}</p>{display.hint && <p>{display.hint}</p>}</>}
    </div>
  </Card>;
}
