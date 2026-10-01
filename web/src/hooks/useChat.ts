// 会话状态机：idle → sending → idle；消息流生命周期管理
// 问答主链路走 SSE 流式（打印机效果），404 时自动回退整包 /chat
// session_id 由前端生成并随请求上送，后端将来做多轮历史时直接复用该字段

import { useCallback, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import { sendChat, streamChat } from '../api/agent';
import type { AnswerType, ChatResponse, SourceHit } from '../api/types';

export type MessageRole = 'user' | 'assistant';
/** normal=正常回答；refused=拒答话术；error=链路错误提示；pending=等待首包骨架；streaming=流式打字中 */
export type MessageKind = 'normal' | 'refused' | 'error' | 'pending' | 'streaming';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  kind: MessageKind;
  content: string;
  sources?: SourceHit[];
  elapsedMs?: number;
  serverMessageId?: string;
}

export type ChatStatus = 'idle' | 'sending';

function genId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

/** 后端成功响应 → 助手消息 */
function toAssistantMessage(res: ChatResponse): ChatMessage {
  return {
    id: genId('m'),
    role: 'assistant',
    kind: res.answer_type === 'generated' ? 'normal' : 'refused',
    content: res.answer,
    sources: res.sources,
    elapsedMs: res.elapsed_ms,
    serverMessageId: res.message_id,
  };
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<ChatStatus>('idle');
  // 最近提问标题（侧栏 Recents 用：去重、最多保留 10 条，新建会话不清空）
  const [recents, setRecents] = useState<string[]>([]);
  // 会话 ID 存 ref：不参与渲染，新建会话时重置
  const sessionIdRef = useRef<string>(genId('sess'));

  const newSession = useCallback(() => {
    setMessages([]);
    setStatus('idle');
    sessionIdRef.current = genId('sess');
  }, []);

  const send = useCallback(async (question: string) => {
    const text = question.trim();
    if (!text || status === 'sending') return;

    setRecents((prev) => [text, ...prev.filter((r) => r !== text)].slice(0, 10));

    const pendingId = genId('p');
    setMessages((prev) => [
      ...prev,
      { id: genId('m'), role: 'user', kind: 'normal', content: text },
      { id: pendingId, role: 'assistant', kind: 'pending', content: '' },
    ]);
    setStatus('sending');

    // ---- 打印机效果：token 只累积到局部变量，rAF 合帧后每帧最多一次 setState，
    // 高频 token 不会逐条触发 React 重渲染（性能保障点一）----
    let streamId: string | null = null; // 首个 token 到达时由 pending 气泡转来的流式消息 id
    let answerType: AnswerType = 'generated';
    let sources: SourceHit[] = [];
    let serverMessageId: string | undefined;
    let rendered = ''; // 已上屏文本
    let pendingBuf = ''; // 本帧新增增量
    let raf = 0;

    /** 惰性把 pending 骨架原位转为流式气泡（首个 token / 定稿时调用） */
    const ensureStreamBubble = () => {
      if (streamId) return;
      streamId = genId('m');
      const sid = streamId;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? { ...m, id: sid, role: 'assistant', kind: 'streaming', content: '', sources }
            : m,
        ),
      );
    };

    const flush = () => {
      raf = 0;
      if (!streamId || !pendingBuf) return;
      rendered += pendingBuf;
      pendingBuf = '';
      const snapshot = rendered;
      const sid = streamId;
      setMessages((prev) => prev.map((m) => (m.id === sid ? { ...m, content: snapshot } : m)));
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(flush);
    };

    /** 把流式气泡定稿为 normal/refused 终态 */
    const finalize = (elapsedMs?: number) => {
      if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
      // 全程无 token（如异常空流）：也要把 pending 转成气泡再定稿，避免骨架残留
      ensureStreamBubble();
      rendered += pendingBuf;
      pendingBuf = '';
      const sid = streamId;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === sid
            ? {
                ...m,
                kind: answerType === 'generated' ? 'normal' : 'refused',
                content: rendered,
                sources,
                elapsedMs,
                serverMessageId,
              }
            : m,
        ),
      );
    };

    const dropPending = (detail: string) => {
      const removeId = streamId ?? pendingId;
      setMessages((prev) =>
        prev
          .filter((m) => m.id !== removeId)
          .concat({ id: genId('m'), role: 'assistant', kind: 'error', content: detail }),
      );
    };

    try {
      await streamChat(text, sessionIdRef.current, {
        onMeta: (meta) => {
          // 仅暂存元信息，不切换气泡：sources / answer_type 供首 token 上屏与定稿使用
          answerType = meta.answer_type;
          sources = meta.sources;
          serverMessageId = meta.message_id;
        },
        onToken: ({ text: token }) => {
          ensureStreamBubble(); // 首 token 到达才把「正在检索…」换成流式打字气泡
          pendingBuf += token;
          schedule();
        },
        onDone: ({ elapsed_ms }) => finalize(elapsed_ms),
      });
    } catch (e) {
      // 旧版后端无 /chat/stream：回退整包问答，保持功能可用
      if (e instanceof ApiError && e.status === 404 && !streamId) {
        try {
          const res = await sendChat(text, sessionIdRef.current);
          answerType = res.answer_type;
          setMessages((prev) => prev.filter((m) => m.id !== pendingId).concat(toAssistantMessage(res)));
          return;
        } catch (inner) {
          e = inner;
        }
      }
      if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
      const detail =
        e instanceof ApiError
          ? e.status === 502
            ? `检索服务不可用：${e.detail}（请先启动 L2 服务 :8000）`
            : e.detail
          : '发生未知错误，请重试';
      dropPending(detail);
    } finally {
      setStatus('idle');
    }
  }, [status]);

  return { messages, status, recents, sessionId: sessionIdRef.current, send, newSession };
}
