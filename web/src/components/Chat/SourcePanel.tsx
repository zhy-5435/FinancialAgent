// 溯源面板：折叠展示作答依据切片五要素与原文摘录，浅色卡片风格对齐 codex 示例

import { useState } from 'react';

import type { SourceHit } from '../../api/types';
import * as Icons from '../icons';

interface SourcePanelProps {
  sources: SourceHit[];
  /** 「自动溯源」开启时回答返回即展开 */
  defaultOpen?: boolean;
}

function SimilarityBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-line">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </span>
      <span className="font-mono text-xs text-muted">{value.toFixed(3)}</span>
    </span>
  );
}

function SourceCard({ hit }: { hit: SourceHit }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    const citation = `${hit.doc_name}（${hit.doc_version_id}）${hit.clause_position ?? ''} ${hit.original_text}`;
    try {
      await navigator.clipboard.writeText(citation);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用时静默忽略 */
    }
  };

  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2.5 transition-colors hover:border-gray-300">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-ink" title={hit.doc_name}>
            {hit.doc_name}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
            {hit.doc_type && (
              <span className="rounded border border-line bg-white px-1.5 py-0.5">{hit.doc_type}</span>
            )}
            <span className="font-mono">{hit.doc_version_id}</span>
            {hit.version && <span>v{hit.version}</span>}
          </p>
        </div>
        <button
          onClick={copy}
          className="flex shrink-0 items-center gap-1 rounded border border-line bg-white px-2 py-1 text-xs text-muted transition-colors hover:border-accent hover:text-accent"
        >
          <Icons.Copy size={12} />
          {copied ? '已复制' : '复制引用'}
        </button>
      </div>
      {(hit.clause_position || hit.heading_path) && (
        <p className="mt-1.5 truncate text-xs text-faint" title={hit.heading_path ?? ''}>
          {hit.clause_position ?? hit.heading_path}
        </p>
      )}
      <blockquote className="mt-2 border-l-2 border-line pl-2 text-xs leading-relaxed text-muted">
        {hit.original_text}
      </blockquote>
      <div className="mt-2 flex items-center justify-between">
        <span className="font-mono text-xs text-faint">{hit.knowledge_id}</span>
        <SimilarityBar value={hit.similarity} />
      </div>
    </div>
  );
}

export default function SourcePanel({ sources, defaultOpen = false }: SourcePanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  if (sources.length === 0) return null;

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs text-faint transition-colors hover:text-accent"
      >
        <span className={`inline-block transition-transform ${open ? 'rotate-180' : ''}`}>
          <Icons.ChevronDown />
        </span>
        作答依据 · {sources.length} 条知识切片
      </button>
      {open && (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {sources.map((hit) => (
            <SourceCard key={hit.knowledge_id} hit={hit} />
          ))}
        </div>
      )}
    </div>
  );
}
