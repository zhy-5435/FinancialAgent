// 消息气泡：按 role + kind 渲染，浅色风格对齐 codex 示例
// 正常回答走 Markdown（行情表/参考编号列表等需要表格、加粗等语法）；
// 拒答仅指知识库低置信拒答，保持纯文本弱化样式不做渲染
// Agent Loop 过程轨迹（plan/tool）以可折叠时间线呈现，流式时展开、定稿后收起
// memo：流式每帧只更新当条消息对象，历史气泡跳过重渲染（性能保障点二）

import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import type { ChatMessage, ChatStep } from '../../hooks/useChat';
import * as Icons from '../icons';
import SourcePanel from './SourcePanel';

interface BubbleProps {
  message: ChatMessage;
  /** 「自动溯源」开启时，回答返回即展开作答依据 */
  autoCite: boolean;
}

/** 工具名 → 中文展示（未知工具回落原名） */
const TOOL_LABELS: Record<string, string> = {
  search_knowledge: '知识库检索',
  web_search: '联网资讯检索',
  search_realtime_quote: '实时行情',
};

function toolLabel(name?: string): string {
  if (!name) return '工具';
  return TOOL_LABELS[name] ?? name;
}

/** 单条过程轨迹 */
function StepRow({ step }: { step: ChatStep }) {
  if (step.kind === 'plan') {
    const tools = step.tools && step.tools.length > 0 ? step.tools.map(toolLabel).join('、') : '';
    return (
      <div className="flex items-start gap-2">
        <span className="mt-0.5 text-accent">
          <Icons.Compass size={13} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="text-ink">规划</span>
          {tools && <span className="text-muted"> → 调用 {tools}</span>}
          {step.text && <p className="mt-0.5 truncate text-faint" title={step.text}>{step.text}</p>}
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-2">
      <span className={`mt-0.5 ${step.ok ? 'text-accent' : 'text-err'}`}>
        {step.ok ? <Icons.Check size={13} /> : <Icons.Alert size={13} />}
      </span>
      <div className="min-w-0 flex-1">
        <span className="text-ink">{toolLabel(step.name)}</span>
        <span className={step.ok ? 'text-faint' : 'text-err'}>{step.ok ? ' · 完成' : ' · 失败/降级'}</span>
        {step.summary && (
          <p className="mt-0.5 truncate text-faint" title={step.summary}>
            {step.summary}
          </p>
        )}
      </div>
    </div>
  );
}

/** Agent Loop 推理过程时间线：live（流式）时默认展开，定稿后默认折叠 */
function ProcessTimeline({ steps, live }: { steps: ChatStep[]; live: boolean }) {
  const [open, setOpen] = useState(live);
  // 定稿后从折叠展开时，随步骤数更新标题；live 期间保持展开呈现进度
  const [lastLive, setLastLive] = useState(live);
  if (live !== lastLive) {
    setLastLive(live);
    setOpen(live); // live 结束（定稿）时自动收起
  }
  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs text-faint transition-colors hover:text-accent"
      >
        <span className={`inline-block transition-transform ${open ? 'rotate-180' : ''}`}>
          <Icons.ChevronDown />
        </span>
        推理过程 · {steps.length} 步{live ? '（进行中…）' : ''}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5 border-l-2 border-line pl-3 text-xs leading-relaxed">
          {steps.map((s, i) => (
            <StepRow key={i} step={s} />
          ))}
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message, autoCite }: BubbleProps) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] rounded-2xl rounded-br-md bg-raised px-4 py-2.5 text-sm leading-relaxed text-ink">
          {message.content}
        </div>
      </div>
    );
  }

  // 链路错误提示
  if (message.kind === 'error') {
    return (
      <div className="rounded-xl border border-err/30 bg-err/5 px-4 py-3 text-sm leading-relaxed text-err">
        {message.content}
      </div>
    );
  }

  const isPending = message.kind === 'pending';
  const streaming = message.kind === 'streaming';
  const refused = message.kind === 'refused';
  const hasSteps = !!message.steps && message.steps.length > 0;

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%]">
        {hasSteps && (
          <ProcessTimeline steps={message.steps!} live={isPending || streaming} />
        )}

        {isPending ? (
          <div className="flex items-center gap-2 py-1 text-sm text-faint">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />
            {hasSteps ? '正在推理与查证…' : '正在理解问题并组织回答…'}
          </div>
        ) : refused ? (
          <p className="prose-answer border-l-2 border-line pl-3 text-sm italic leading-relaxed text-faint">
            {message.content}
          </p>
        ) : (
          <div className="md-body text-sm leading-relaxed text-ink">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            {streaming && (
              <span className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[0.15em] animate-pulse bg-accent align-middle" />
            )}
          </div>
        )}

        {message.sources && message.sources.length > 0 && (
          <SourcePanel sources={message.sources} defaultOpen={autoCite} />
        )}
        {!isPending && !streaming && typeof message.elapsedMs === 'number' && (
          <p className="mt-2 font-mono text-xs text-faint">
            {refused ? '低置信拒答' : '完成'} · {(message.elapsedMs / 1000).toFixed(1)}s
          </p>
        )}
      </div>
    </div>
  );
}

export default memo(MessageBubble);
