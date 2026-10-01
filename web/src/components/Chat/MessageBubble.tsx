// 消息气泡：按 role + kind 渲染，浅色风格对齐 codex 示例
// 未来新增消息形态（工具调用过程、卡片消息）只需扩展 kind 分支
// memo：流式每帧只更新当条消息对象，历史气泡跳过重渲染（性能保障点二）

import { memo } from 'react';

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

  // 等待作答骨架
  if (message.kind === 'pending') {
    return (
      <div className="flex items-center gap-2 py-1 text-sm text-faint">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />
        正在检索 L1 知识库并生成回答…
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

  // 拒答：弱化样式；正常回答：正文样式；流式：正文 + 打字光标
  const refused = message.kind === 'refused';
  const streaming = message.kind === 'streaming';
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%]">
        <p
          className={
            refused
              ? 'prose-answer border-l-2 border-line pl-3 text-sm italic leading-relaxed text-faint'
              : 'prose-answer text-sm leading-relaxed text-ink'
          }
        >
          {message.content}
          {streaming && (
            <span className="ml-0.5 inline-block h-[1.05em] w-[2px] translate-y-[0.15em] animate-pulse bg-accent align-middle" />
          )}
        </p>
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
