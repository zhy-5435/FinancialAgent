// 会话状态机：idle → sending → idle；消息流生命周期管理
// 问答主链路走 SSE 流式（打印机效果），404 时自动回退整包 /chat
// session_id 由前端生成并随请求上送，后端按其持久化多轮历史（列表/恢复/删除见 useSessions）

import { useCallback, useState } from 'react';

import { ApiError } from '../api/client';
import { sendChat, streamChat } from '../api/agent';
import type { AnswerType, ChatResponse, RestoredMessage, SourceHit } from '../api/types';

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

/**
 * answer_type → 气泡样式：拒答弱化样式仅限知识库低置信拒答（refused），
 * 闲聊（chatted）、行情（quoted）、资讯占位（searched）等均按正常回答展示；
 * 存档中的旧值/新增枚举（string|null）同样回落到 normal 渲染，保持向后兼容
 */
function kindForAnswer(answerType: AnswerType | string | null): MessageKind {
  return answerType === 'refused' ? 'refused' : 'normal';
}

/** 后端成功响应 → 助手消息 */
function toAssistantMessage(res: ChatResponse): ChatMessage {
  return {
    id: genId('m'),
    role: 'assistant',
    kind: kindForAnswer(res.answer_type),
    content: res.answer,
    sources: res.sources,
    elapsedMs: res.elapsed_ms,
    serverMessageId: res.message_id,
  };
}

/** 历史恢复消息（/sessions/{id}/messages）→ 气泡消息；存档不持久化 sources，恢复后不展示溯源面板 */
export function toRestoredChatMessages(rows: RestoredMessage[]): ChatMessage[] {
  return rows.map((m) => ({
    id: m.message_id,
    role: m.role === 'user' ? 'user' : 'assistant',
    kind: m.role === 'user' ? 'normal' : kindForAnswer(m.answer_type),
    content: m.content,
    serverMessageId: m.message_id,
  }));
}

export interface UseChatOptions {
  /** 本轮问答结束（含回退链路）后回调：供会话列表刷新 */
  onTurnEnd?: () => void;
}

export function useChat(options: UseChatOptions = {}) {
  const { onTurnEnd } = options;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<ChatStatus>('idle');
  // 会话 ID 提升为状态：侧栏选中历史会话需高亮当前会话
  const [sessionId, setSessionId] = useState<string>(() => genId('sess'));

  const newSession = useCallback(() => {
    setMessages([]);
    setStatus('idle');
    setSessionId(genId('sess'));
  }, []);

  /** 切换到已持久化的会话：用恢复消息替换当前消息流（调用方负责拉取历史） */
  const switchTo = useCallback((id: string, restored: ChatMessage[]) => {
    setStatus('idle');
    setSessionId(id);
    setMessages(restored);
  }, []);

  const send = useCallback(async (question: string) => {
    const text = question.trim();
    if (!text || status === 'sending') return;

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
                kind: kindForAnswer(answerType),
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
      await streamChat(text, sessionId, {
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
          const res = await sendChat(text, sessionId);
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
      // 本轮消息已由后端落库（成功或拒答均存档），通知会话列表刷新标题/排序
      onTurnEnd?.();
    }
  }, [status, sessionId, onTurnEnd]);

  return { messages, status, sessionId, send, newSession, switchTo };
}
