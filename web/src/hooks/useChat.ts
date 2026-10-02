// 会话状态机：idle → sending → idle；消息流生命周期管理
// 问答主链路走 SSE 流式（打印机效果），404 时自动回退整包 /chat
// session_id 由前端生成并随请求上送，后端按其持久化多轮历史（列表/恢复/删除见 useSessions）

import { useCallback, useEffect, useState } from 'react';

import { ApiError } from '../api/client';
import { confirmQuote, sendChat, sendFeedback, streamChat } from '../api/agent';
import type {
  AnswerType,
  ChatResponse,
  FeedbackCategory,
  QuoteCandidate,
  QuoteConfirmPayload,
  RestoredMessage,
  SourceHit,
  StreamConfirmEvent,
  TraceStep,
} from '../api/types';

export type MessageRole = 'user' | 'assistant';
/** normal=正常回答；refused=拒答话术；error=链路错误提示；pending=等待首包骨架；streaming=流式打字中 */
export type MessageKind = 'normal' | 'refused' | 'error' | 'pending' | 'streaming';

/** Agent Loop 单步过程（归一化 plan / tool 两类，供气泡内时间线渲染） */
export interface ChatStep {
  kind: 'plan' | 'tool';
  text?: string; // plan：模型规划说明（可能为空）
  tools?: string[]; // plan：本步拟调用的工具名
  name?: string; // tool：工具名
  ok?: boolean; // tool：是否成功
  summary?: string; // tool：结果摘要
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  kind: MessageKind;
  content: string;
  sources?: SourceHit[];
  steps?: ChatStep[];
  elapsedMs?: number;
  serverMessageId?: string;
  /** 作答类型（供反馈/确认渲染判定；旧数据可为空） */
  answerType?: AnswerType;
  /** 行情灰色确认载荷（answer_type=confirm 时非空，供内联候选点选） */
  confirm?: QuoteConfirmPayload;
  /** 已点选确认的候选代码（确认条置灰、防重复提交） */
  confirmResolved?: string;
  /** 已提交的反馈类别（回显选中态） */
  feedback?: FeedbackCategory | null;
  /** 反馈提交中 */
  feedbackPending?: boolean;
}

export type ChatStatus = 'idle' | 'sending';

function genId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

/** 当前活跃会话 ID 的本地持久化键：整页重载（切到其他 App 后回收/刷新）据此恢复原对话而非每次新开 */
const ACTIVE_SESSION_KEY = 'fin-l1-active-session';

/** 读取本地持久化的会话 ID（仅接受本应用生成的 sess- 前缀，脏值视为无） */
function readStoredSessionId(): string | null {
  const saved = localStorage.getItem(ACTIVE_SESSION_KEY);
  return saved && saved.startsWith('sess-') ? saved : null;
}

/**
 * answer_type → 气泡样式：拒答弱化样式仅限知识库低置信拒答（refused），
 * 闲聊（chatted）、行情（quoted）、资讯占位（searched）等均按正常回答展示；
 * 存档中的旧值/新增枚举（string|null）同样回落到 normal 渲染，保持向后兼容
 */
function kindForAnswer(answerType: AnswerType | string | null): MessageKind {
  return answerType === 'refused' ? 'refused' : 'normal';
}

/** 后端 REST 轨迹 TraceStep[] → UI ChatStep[]（/chat 回退链路用；流式由 plan/step 事件增量累积） */
function fromTrace(steps?: TraceStep[]): ChatStep[] | undefined {
  if (!steps || steps.length === 0) return undefined;
  return steps.map((s) =>
    s.type === 'plan'
      ? { kind: 'plan' as const, text: s.text, tools: s.tool_calls.map((c) => c.name) }
      : { kind: 'tool' as const, name: s.name, ok: s.ok, summary: s.summary },
  );
}

/** 后端成功响应 → 助手消息 */
function toAssistantMessage(res: ChatResponse): ChatMessage {
  return {
    id: genId('m'),
    role: 'assistant',
    kind: kindForAnswer(res.answer_type),
    content: res.answer,
    sources: res.sources,
    steps: fromTrace(res.steps),
    elapsedMs: res.elapsed_ms,
    serverMessageId: res.message_id,
    answerType: res.answer_type,
    confirm: res.confirm ?? undefined,
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
  // 首帧优先复用本地持久化的活跃会话（整页重载后恢复原对话）；无持久化则新建空会话
  const [restoredId] = useState<string | null>(readStoredSessionId);
  const [sessionId, setSessionId] = useState<string>(() => restoredId ?? genId('sess'));

  // 活跃会话随状态写入本地：下次整页重载据上次会话恢复，避免「重新进入即新对话」
  useEffect(() => {
    localStorage.setItem(ACTIVE_SESSION_KEY, sessionId);
  }, [sessionId]);

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
    let steps: ChatStep[] = []; // Agent Loop 过程轨迹（plan/step 事件增量累积）
    let confirmPayload: QuoteConfirmPayload | null = null; // 行情灰色确认候选（confirm 事件）
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
            ? { ...m, id: sid, role: 'assistant', kind: 'streaming', content: '', sources, steps }
            : m,
        ),
      );
    };

    /** 追加一条过程轨迹并实时刷到当前气泡（pending 或 streaming） */
    const pushStep = (step: ChatStep) => {
      steps = [...steps, step];
      const target = streamId ?? pendingId;
      setMessages((prev) => prev.map((m) => (m.id === target ? { ...m, steps } : m)));
    };

    /** 暂存行情确认候选并实时挂到当前气泡（confirm 事件先于/独立于 token 到达） */
    const setConfirm = (c: StreamConfirmEvent) => {
      confirmPayload = { question: c.question, guessed: c.guessed, candidates: c.candidates };
      const target = streamId ?? pendingId;
      setMessages((prev) =>
        prev.map((m) => (m.id === target ? { ...m, confirm: confirmPayload! } : m)),
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
                steps,
                elapsedMs,
                serverMessageId,
                answerType,
                confirm: confirmPayload ?? undefined,
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
        onPlan: (plan) =>
          pushStep({ kind: 'plan', text: plan.text, tools: plan.tools.map((t) => t.name) }),
        onStep: (step) =>
          pushStep({ kind: 'tool', name: step.name, ok: step.ok, summary: step.summary }),
        onConfirm: (c) => setConfirm(c),
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

  /** 提交回答反馈标注（纯增量、不阻断对话）：以 serverMessageId 为锚，乐观更新选中态、失败回滚 */
  const submitFeedback = useCallback(
    async (messageId: string, category: FeedbackCategory, comment?: string | null) => {
      const target = messages.find((m) => m.id === messageId);
      const serverMessageId = target?.serverMessageId;
      if (!serverMessageId) return; // 无 memory 锚点（旧链路/异常）不发，避免脏标注
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId ? { ...m, feedback: category, feedbackPending: true } : m,
        ),
      );
      try {
        await sendFeedback({
          session_id: sessionId,
          message_id: serverMessageId,
          category,
          comment: comment ?? null,
        });
        setMessages((prev) =>
          prev.map((m) => (m.id === messageId ? { ...m, feedbackPending: false } : m)),
        );
      } catch (e) {
        console.warn('反馈提交失败', e);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId ? { ...m, feedback: null, feedbackPending: false } : m,
          ),
        );
      }
    },
    [messages, sessionId],
  );

  /** 行情灰色确认：点选候选后按取数键确定性取数，追加「点选(user) + 行情(assistant)」两条气泡 */
  const resolveConfirm = useCallback(
    async (messageId: string, candidate: QuoteCandidate) => {
      const marketTag = candidate.market ? `（${candidate.market}）` : '';
      const userNote = `已选择标的：${candidate.name}${marketTag}，查询其最新实时行情`;
      const pendingId = genId('p');
      setMessages((prev) => [
        // 确认条置灰锁定（记录已选 code），避免重复提交
        ...prev.map((m) => (m.id === messageId ? { ...m, confirmResolved: candidate.code } : m)),
        { id: genId('m'), role: 'user', kind: 'normal', content: userNote },
        { id: pendingId, role: 'assistant', kind: 'pending', content: '' },
      ]);
      try {
        const res = await confirmQuote({
          session_id: sessionId,
          code: candidate.code,
          name: candidate.name,
        });
        setMessages((prev) =>
          prev
            .filter((m) => m.id !== pendingId)
            .concat({
              id: genId('m'),
              role: 'assistant',
              kind: 'normal',
              content: res.answer,
              answerType: res.answer_type,
              serverMessageId: res.message_id,
              elapsedMs: res.elapsed_ms,
            }),
        );
      } catch (e) {
        const detail =
          e instanceof ApiError ? e.detail : '行情取数失败，请稍后重试或改用准确名称/代码';
        setMessages((prev) =>
          prev
            .filter((m) => m.id !== pendingId)
            .concat({ id: genId('m'), role: 'assistant', kind: 'error', content: detail }),
        );
      } finally {
        onTurnEnd?.();
      }
    },
    [sessionId, onTurnEnd],
  );

  return {
    messages,
    status,
    sessionId,
    restoredId,
    send,
    newSession,
    switchTo,
    submitFeedback,
    resolveConfirm,
  };
}
