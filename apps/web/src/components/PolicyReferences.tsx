import type { PolicyReference } from '../protocol';
import { evidenceSegments } from '../policyEvidence';

export function PolicyReferences({references, query}: {references: PolicyReference[]; query: string}) {
  return <div className="policy-references">
    <p className="secondary-text">高亮为查询词在原文中的匹配；检索相似度不代表结论可信度。</p>
    {references.map((citation, index) => <section className="policy-reference" key={index}
      aria-label={`政策依据 ${index + 1}`}>
      <div className="policy-reference-heading">
        <strong><span className="policy-reference-number">[{index + 1}]</span> {citation.title}</strong>
        <span className="secondary-text">检索相似度 {citation.score.toFixed(4)}</span>
      </div>
      <p className="policy-reference-source">{citation.source} · 版本 {citation.version}</p>
      <blockquote className="policy-excerpt">{evidenceSegments(citation.text, query).map((part, i) =>
        part.matched ? <mark key={i}>{part.text}</mark> : part.text)}</blockquote>
      <p className="secondary-text">政策 {citation.policy_id} · 生效 <time dateTime={citation.effective_from}>{citation.effective_from}</time>
        {citation.effective_until ? <> · 截至 <time dateTime={citation.effective_until}>{citation.effective_until}</time></> : ' · 未设截止时间'}</p>
    </section>)}
  </div>;
}
