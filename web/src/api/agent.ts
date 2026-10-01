// L3 Agent 问答服务调用：/api/agent → :8001

import { apiFetch, apiStream, ApiError } from './client';
import type {
  AgentHealth,
  ChatResponse,
  StreamDoneEvent,
  StreamErrorEvent,
  StreamMetaEvent,
  StreamTokenEvent,
} from './types';

export function fetchAgentHealth(): Promise<AgentHealth> {
  return apiFetch<AgentHealth>('agent', '/health', { timeoutMs: 8_000 });
}

export function sendChat(question: string, sessionId?: string | null): Promise<ChatResponse> {
  return apiFetch<ChatResponse>('agent', '/chat', {
    method: 'POST',
    body: { question, session_id: sessionId ?? null },
    // LLM 作答较慢，问答整体预留 3 分钟
    timeoutMs: 180_000,
  });
}

/** 流式问答回调：meta 先到（可渲染溯源），token 逐个到达，done 收尾 */
export interface ChatStreamHandlers {
  onMeta: (e: StreamMetaEvent) => void;
  onToken: (e: StreamTokenEvent) => void;
  onDone: (e: StreamDoneEvent) => void;
}

export function streamChat(
  question: string,
  sessionId: string | null | undefined,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return apiStream(
    'agent',
    '/chat/stream',
    { question, session_id: sessionId ?? null },
    ({ event, data }) => {
      switch (event) {
        case 'meta':
          handlers.onMeta(data as StreamMetaEvent);
          break;
        case 'token':
          handlers.onToken(data as StreamTokenEvent);
          break;
        case 'done':
          handlers.onDone(data as StreamDoneEvent);
          break;
        case 'error': {
          const e = data as StreamErrorEvent;
          // 抛出即中断流读取，由调用方统一捕获
          throw new ApiError(e.status, e.detail);
        }
      }
    },
    signal,
  );
}
