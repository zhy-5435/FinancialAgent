// 消息气泡：按 role + kind 渲染，浅色风格对齐 codex 示例
// 正常回答走 Markdown（行情表/参考编号列表等需要表格、加粗等语法）；
// 拒答仅指知识库低置信拒答，保持纯文本弱化样式不做渲染
// Agent Loop 过程轨迹（plan/tool）以可折叠时间线呈现，流式时展开、定稿后收起
// memo：流式每帧只更新当条消息对象，历史气泡跳过重渲染（性能保障点二）

import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import type { FeedbackCategory, QuoteCandidate } from '../../api/types';
import type { ChatMessage, ChatStep } from '../../hooks/useChat';
import * as Icons from '../icons';
import SourcePanel from './SourcePanel';

interface BubbleProps {
  message: ChatMessage;
  /** 「自动溯源」开启时，回答返回即展开作答依据 */
  autoCite: boolean;
  /** 提交回答反馈（有用/无用/内容纠错）；correction 附带 comment */
  onFeedback?: (messageId: string, category: FeedbackCategory, comment?: string | null) => void;
  /** 行情灰色确认：点选候选标的取数 */
  onResolveConfirm?: (messageId: string, candidate: QuoteCandidate) => void;
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

/** 行情灰色确认条：内联候选标的供点选（候选来自白名单、无 LLM 生成，确认后按取数键确定性查询） */
function ConfirmChips({
  message,
  onResolve,
}: {
  message: ChatMessage;
  onResolve?: (messageId: string, candidate: QuoteCandidate) => void;
}) {
  const candidates = message.confirm?.candidates ?? [];
  const resolved = message.confirmResolved;
  return (
    <div className="mt-2 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2.5">
      <p className="text-xs text-muted">
        {resolved ? '已按所选标的查询：' : '请确认您要查询的标的（点击下方候选，或用准确名称/代码重试）：'}
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {candidates.map((c) => {
          const chosen = resolved === c.code;
          const locked = Boolean(resolved);
          return (
            <button
              key={c.code}
              disabled={locked}
              onClick={() => onResolve?.(message.id, c)}
              className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-colors ${
                chosen
                  ? 'border-accent bg-accent/10 text-accent'
                  : locked
                    ? 'cursor-not-allowed border-line bg-surface text-faint'
                    : 'border-line bg-white text-ink hover:border-accent hover:text-accent'
              }`}
            >
              <span className="font-medium">{c.name}</span>
              {c.market && <span className="text-faint">{c.market}</span>}
              <span className="font-mono text-faint">{c.code}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** 回答反馈条：有用 / 无用 / 内容纠错（纯增量标注，提交后回显选中态；纠错展开输入框） */
function FeedbackBar({
  message,
  onSubmit,
}: {
  message: ChatMessage;
  onSubmit?: (messageId: string, category: FeedbackCategory, comment?: string | null) => void;
}) {
  const [correcting, setCorrecting] = useState(false);
  const [comment, setComment] = useState('');
  const submitted = Boolean(message.feedback);
  const pending = message.feedbackPending;

  const btn = (active: boolean) =>
    `flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors disabled:cursor-not-allowed ${
      active
        ? 'border-accent bg-accent/10 text-accent'
        : 'border-line bg-white text-muted hover:border-accent hover:text-accent'
    }`;

  if (submitted && message.feedback !== 'correction') {
    return (
      <p className="mt-2 text-xs text-faint">
        {message.feedback === 'useful' ? '已记录：感谢反馈 👍' : '已记录：感谢反馈，我们会持续改进'}
      </p>
    );
  }

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2">
        <button
          className={btn(message.feedback === 'useful')}
          disabled={pending}
          onClick={() => onSubmit?.(message.id, 'useful')}
          title="回答有用"
        >
          <Icons.ThumbsUp size={13} />
          有用
        </button>
        <button
          className={btn(message.feedback === 'useless')}
          disabled={pending}
          onClick={() => onSubmit?.(message.id, 'useless')}
          title="回答无用 / 答非所问"
        >
          <Icons.ThumbsDown size={13} />
          无用
        </button>
        <button
          className={btn(message.feedback === 'correction' || correcting)}
          disabled={pending}
          onClick={() => setCorrecting((v) => !v)}
          title="指出内容错误并说明应更正为什么"
        >
          <Icons.Edit size={13} />
          内容纠错
        </button>
        {pending && <span className="text-xs text-faint">提交中…</span>}
      </div>

      {correcting && (
        <div className="mt-2 flex items-start gap-2">
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
            maxLength={1000}
            placeholder="请说明这条回答哪里有误、应更正为什么…"
            className="min-w-0 flex-1 resize-y rounded-md border border-line bg-white px-2 py-1.5 text-xs text-ink outline-none focus:border-accent"
          />
          <button
            disabled={pending || !comment.trim()}
            onClick={() => {
              onSubmit?.(message.id, 'correction', comment.trim());
              setCorrecting(false);
              setComment('');
            }}
            className="rounded-md bg-ink px-3 py-1.5 text-xs text-white transition-colors hover:bg-shade disabled:cursor-not-allowed disabled:opacity-40"
          >
            提交纠错
          </button>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message, autoCite, onFeedback, onResolveConfirm }: BubbleProps) {
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
  const isFinal = !isPending && !streaming;
  const showConfirm = isFinal && (message.confirm?.candidates?.length ?? 0) > 0;
  // 反馈锚点：有 memory 消息 ID 且非「待确认」提示（确认轮尚无实质答案，不收集反馈）
  const showFeedback = isFinal && !!message.serverMessageId && message.answerType !== 'confirm';

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

        {showConfirm && <ConfirmChips message={message} onResolve={onResolveConfirm} />}

        {message.sources && message.sources.length > 0 && (
          <SourcePanel sources={message.sources} defaultOpen={autoCite} />
        )}
        {isFinal && typeof message.elapsedMs === 'number' && (
          <p className="mt-2 font-mono text-xs text-faint">
            {refused ? '低置信拒答' : message.answerType === 'confirm' ? '待确认标的' : '完成'} ·{' '}
            {(message.elapsedMs / 1000).toFixed(1)}s
          </p>
        )}
        {showFeedback && <FeedbackBar message={message} onSubmit={onFeedback} />}
      </div>
    </div>
  );
}

export default memo(MessageBubble);
