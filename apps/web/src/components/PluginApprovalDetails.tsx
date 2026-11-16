import type { ApprovalPresentation } from "../protocol";

export function PluginApprovalDetails({ presentation }: { presentation: ApprovalPresentation }) {
  return (
    <div className="plugin-approval-details">
      <strong>{presentation.title}</strong>
      {presentation.summary && <p>{presentation.summary}</p>}
      {presentation.fields.length > 0 && <dl>
        {presentation.fields.map(({ label, value }, index) => (
          <div key={index}><dt>{label}</dt><dd>{String(value)}</dd></div>
        ))}
      </dl>}
    </div>
  );
}
