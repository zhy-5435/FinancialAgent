// 消息气泡：按 role + kind 渲染，浅色风格对齐 codex 示例
// 正常回答走 Markdown（行情表/参考编号列表等需要表格、加粗等语法）；
// 拒答仅指知识库低置信拒答，保持纯文本弱化样式不做渲染
// 未来新增消息形态（工具调用过程、卡片消息）只需扩展 kind 分支
// memo：流式每帧只更新当条消息对象，历史气泡跳过重渲染（性能保障点二）

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import type { ChatMessage } from '../../hooks/useChat';
import SourcePanel from './SourcePanel';

interface BubbleProps {
  message: ChatMessage;
  /** 「自动溯源」开启时，回答返回即展开作答依据 */
  autoCite: boolean;
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

  // 等待作答骨架（意图分类后可能走行情/闲聊等分支，文案不预设知识库检索）
  if (message.kind === 'pending') {
    return (
      <div className="flex items-center gap-2 py-1 text-sm text-faint">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />
        正在理解问题并组织回答…
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

  // 拒答：弱化纯文本样式；正常回答（含行情/闲聊）：Markdown 正文；流式：正文 + 打字光标
  const refused = message.kind === 'refused';
  const streaming = message.kind === 'streaming';
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%]">
        {refused ? (
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
        {!streaming && typeof message.elapsedMs === 'number' && (
          <p className="mt-2 font-mono text-xs text-faint">
            {refused ? '低置信拒答' : '完成'} · {(message.elapsedMs / 1000).toFixed(1)}s
          </p>
        )}
      </div>
    </div>
  );
}

export default memo(MessageBubble);
