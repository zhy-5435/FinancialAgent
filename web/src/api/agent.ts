// L3 Agent 问答服务调用：/api/agent → :8001

import { apiFetch, apiStream, ApiError } from './client';
import type {
  AgentHealth,
  ChatResponse,
  DeleteSessionResponse,
  FeedbackRequest,
  FeedbackResponse,
  QuoteConfirmRequest,
  QuoteConfirmResponse,
  SessionItem,
  SessionMessagesResponse,
  StreamConfirmEvent,
  StreamDoneEvent,
  StreamErrorEvent,
  StreamMetaEvent,
  StreamPlanEvent,
  StreamStepEvent,
  StreamTokenEvent,
} from './types';

export function fetchAgentHealth(): Promise<AgentHealth> {
  return apiFetch<AgentHealth>('agent', '/health', { timeoutMs: 8_000 });
}

/** 会话列表（GET /sessions，按最近活跃降序，后端返回裸数组） */
export function fetchSessions(limit = 50): Promise<SessionItem[]> {
  return apiFetch<SessionItem[]>('agent', `/sessions?limit=${limit}`, { timeoutMs: 10_000 });
}

/** 指定会话的可见历史消息（仅 user/assistant，按时间升序） */
export function fetchSessionMessages(sessionId: string): Promise<SessionMessagesResponse> {
  return apiFetch<SessionMessagesResponse>(
    'agent',
    `/sessions/${encodeURIComponent(sessionId)}/messages`,
    { timeoutMs: 10_000 },
  );
}

/** 删除会话的消息与压缩日志存档 */
export function deleteSession(sessionId: string): Promise<DeleteSessionResponse> {
  return apiFetch<DeleteSessionResponse>(
    'agent',
    `/sessions/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE', timeoutMs: 10_000 },
  );
}

export function sendChat(question: string, sessionId?: string | null): Promise<ChatResponse> {
  return apiFetch<ChatResponse>('agent', '/chat', {
    method: 'POST',
    body: { question, session_id: sessionId ?? null },
    // LLM 作答较慢，问答整体预留 3 分钟
    timeoutMs: 180_000,
  });
}

/** 提交回答反馈标注（有用/无用/内容纠错）；落库独立标注表，不影响运行时链路 */
export function sendFeedback(payload: FeedbackRequest): Promise<FeedbackResponse> {
  return apiFetch<FeedbackResponse>('agent', '/feedback', {
    method: 'POST',
    body: payload,
    timeoutMs: 15_000,
  });
}

/** 行情确认候选点选后的确定性取数：按已知取数键直接返回行情快照（不经解析、不经 LLM） */
export function confirmQuote(payload: QuoteConfirmRequest): Promise<QuoteConfirmResponse> {
  return apiFetch<QuoteConfirmResponse>('agent', '/quote/confirm', {
    method: 'POST',
    body: payload,
    timeoutMs: 20_000,
  });
}

/** 流式问答回调：meta 先到（可渲染溯源），token 逐个到达，done 收尾；plan/step 为 Agent Loop 过程事件（可选）；confirm 为行情确认候选事件（可选） */
export interface ChatStreamHandlers {
  onMeta: (e: StreamMetaEvent) => void;
  onToken: (e: StreamTokenEvent) => void;
  onDone: (e: StreamDoneEvent) => void;
  onPlan?: (e: StreamPlanEvent) => void;
  onStep?: (e: StreamStepEvent) => void;
  onConfirm?: (e: StreamConfirmEvent) => void;
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
        case 'plan':
          handlers.onPlan?.(data as StreamPlanEvent);
          break;
        case 'step':
          handlers.onStep?.(data as StreamStepEvent);
          break;
        case 'confirm':
          handlers.onConfirm?.(data as StreamConfirmEvent);
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
