// 消息流：空态渲染示例同款中央引导区，有消息时渲染气泡列表并自动滚底

import { useEffect, useRef } from 'react';

import type { FeedbackCategory, QuoteCandidate } from '../../api/types';
import type { ChatMessage } from '../../hooks/useChat';
import * as Icons from '../icons';
import MessageBubble from './MessageBubble';

interface MessageListProps {
  messages: ChatMessage[];
  autoCite: boolean;
  onFeedback?: (messageId: string, category: FeedbackCategory, comment?: string | null) => void;
  onResolveConfirm?: (messageId: string, candidate: QuoteCandidate) => void;
}

/** 中央引导区：对应示例 CloudCode + "What should we build?" */
function EmptyGuide() {
  return (
    <div className="flex h-full flex-col items-center justify-center text-muted">
      <div className="mb-4 text-gray-300">
        <Icons.CloudCode />
      </div>
      <h2 className="text-3xl font-light text-muted">想了解点什么？</h2>
      <p className="mt-3 max-w-md text-center text-sm leading-relaxed text-faint">
        回答基于 L1 知识库命中的权威切片
      </p>
    </div>
  );
}

export default function MessageList({
  messages,
  autoCite,
  onFeedback,
  onResolveConfirm,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 流式每帧都会触发跟滚：用 auto 即时定位，smooth 动画在高频更新下会排队掉帧（性能保障点三）
    bottomRef.current?.scrollIntoView({ behavior: 'auto' });
  }, [messages]);

  if (messages.length === 0) {
    return <EmptyGuide />;
  }

  return (
    <div className="space-y-5 pb-4">
      {messages.map((m) => (
        <MessageBubble
          key={m.id}
          message={m}
          autoCite={autoCite}
          onFeedback={onFeedback}
          onResolveConfirm={onResolveConfirm}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
