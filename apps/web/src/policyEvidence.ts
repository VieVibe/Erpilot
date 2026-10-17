import type { PolicyReference } from './protocol';

export function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
export function text(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}
export function time(value: unknown): value is string {
  return typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value));
}
export function number(value: unknown, low: number, high = Infinity): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= low && value <= high;
}

export function validReferences(value: unknown, at: string): value is PolicyReference[] {
  if (!Array.isArray(value)) return false;
  const versions = new Map<string, string>();
  for (const citation of value) {
    if (!record(citation) || !text(citation.source) || !text(citation.title) || !text(citation.text) ||
        !text(citation.policy_id) || !text(citation.version) || !number(citation.score, -1, 1) ||
        !time(citation.effective_from) || Date.parse(citation.effective_from) > Date.parse(at)) return false;
    if (citation.effective_until !== null && (!time(citation.effective_until) ||
        Date.parse(citation.effective_until) <= Date.parse(at))) return false;
    const version = JSON.stringify([citation.version, citation.effective_from, citation.effective_until]);
    if (versions.has(citation.policy_id) && versions.get(citation.policy_id) !== version) return false;
    versions.set(citation.policy_id, version);
  }
  return true;
}

const fillers = /请问|一下|告诉|多少|如何|什么|几天|多久|可以|是否|怎么|哪些|需要/gu;

/** Literal query-word matches only. Keep every character of the original evidence. */
export function evidenceSegments(original: string, query: string): {text: string; matched: boolean}[] {
  const candidates = new Set<string>();
  // Chinese dictionaries differ across runtimes. Use shared literal spans of at
  // least two characters, bounded to keep unusually long queries inexpensive.
  for (const token of query.slice(0, 500).replace(fillers, ' ').match(/\p{Script=Han}+|[A-Za-z0-9_]+/gu) ?? []) {
    if (/^\p{Script=Han}+$/u.test(token)) {
      for (let start = 0; start < token.length - 1; start++) {
        for (let length = 2; length <= Math.min(12, token.length - start); length++) {
          const term = token.slice(start, start + length);
          if (original.includes(term)) candidates.add(term);
        }
      }
    } else if (token.length >= 2 || /^\d$/.test(token)) candidates.add(token);
  }
  const terms = [...candidates].sort((a, b) => b.length - a.length).slice(0, 32);
  if (!terms.length) return [{text: original, matched: false}];
  const pattern = terms.map(term => {
    const literal = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return /^[\x00-\x7f]+$/.test(term) ? `(?<![A-Za-z0-9_])${literal}(?![A-Za-z0-9_])` : literal;
  }).join('|');
  const parts: {text: string; matched: boolean}[] = [];
  let cursor = 0;
  for (const match of original.matchAll(new RegExp(pattern, 'giu'))) {
    const start = match.index!;
    if (start > cursor) parts.push({text: original.slice(cursor, start), matched: false});
    parts.push({text: match[0], matched: true});
    cursor = start + match[0].length;
  }
  if (cursor < original.length) parts.push({text: original.slice(cursor), matched: false});
  return parts.length ? parts : [{text: original, matched: false}];
}
