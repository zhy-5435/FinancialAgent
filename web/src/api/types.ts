// 与后端接口契约逐字段对齐（src/agent/api.py、src/search/api.py）
// 后端新增字段（如流式、反馈）时在此集中演进，组件类型随之可感知

// ---------- L3 Agent 问答服务 ----------

export interface ChatRequest {
  question: string;
  /** 会话主键：前端生成并上送，后端按此持久化消息并按上下文引擎压缩多轮历史 */
  session_id?: string | null;
}

export interface SourceHit {
  knowledge_id: string;
  doc_version_id: string;
  chunk_type: string;
  content: string;
  original_text: string;
  clause_position: string | null;
  heading_path: string | null;
  keywords: string[];
  doc_name: string;
  doc_type: string | null;
  version: string | null;
  publish_dept: string | null;
  effective_date: string | null;
  /** 余弦相似度 */
  similarity: number;
  /** 关键词得分（BM25 归一化到 [0,1]） */
  keyword_score: number;
  /** 综合得分 = 归一化向量相似度×0.8 + 归一化关键词得分×0.2（两路得分各做 min-max 归一化后加权，网格搜索调优选定） */
  score: number;
}

/** 识别意图：与后端 src/agent/intent.py 的 IntentName 同源；Agent Loop 下由实际调用工具回溯派生 */
export type Intent = 'kb_qa' | 'chitchat' | 'news_search' | 'quote_query';

/** 作答类型：generated/refused=知识库分支；chatted=闲聊；searched=财经简报；quoted=实时行情快照；agentic=多工具组合的自主循环作答；confirm=行情灰色区间待确认 */
export type AnswerType = 'generated' | 'refused' | 'chatted' | 'searched' | 'quoted' | 'agentic' | 'confirm';

/** 行情确认候选标的（来自 suggest3 白名单，不含任何行情数字；点选后按 code 确定性取数） */
export interface QuoteCandidate {
  /** 新浪取数键，如 rt_hk00700 / sh600519 / gb_aapl */
  code: string;
  /** 标的显示名 */
  name: string;
  /** 市场归类（A股/港股/港股指数/美股，可能为空） */
  market?: string | null;
}

/** Agent Loop 过程轨迹单条（与后端 graph state.trace 元素同源，REST 层 /chat 的 steps 字段） */
export type TraceStep =
  | { type: 'plan'; step: number; text: string; tool_calls: { name: string; args: Record<string, unknown> }[] }
  | { type: 'tool_result'; step: number; name: string; ok: boolean; summary: string };

export interface ChatResponse {
  session_id: string;
  message_id: string;
  question: string;
  answer: string;
  intent: Intent;
  answer_type: AnswerType;
  sources: SourceHit[];
  /** Agent Loop 过程轨迹（旧后端无此字段时为 undefined，向后兼容） */
  steps?: TraceStep[];
  /** 行情灰色确认载荷（answer_type=confirm 时非空；旧后端无此字段为 undefined） */
  confirm?: QuoteConfirmPayload | null;
  elapsed_ms: number;
}

/** 行情确认载荷（REST /chat 的 confirm 字段与 SSE confirm 事件同源） */
export interface QuoteConfirmPayload {
  question: string;
  guessed?: string;
  candidates: QuoteCandidate[];
}

export interface AgentHealth {
  status: string;
  search_api_base: string;
  search_api_reachable: boolean;
}

// ---------- L3 SSE 流式问答事件（POST /chat/stream，与 ChatResponse 契约同源） ----------

/** event: meta —— 分支就绪即到达，携带意图、作答类型与溯源切片（可先于正文渲染溯源面板） */
export interface StreamMetaEvent {
  session_id: string;
  message_id: string;
  question: string;
  intent: Intent;
  answer_type: AnswerType;
  sources: SourceHit[];
}

/** event: token —— LLM 增量文本，拒答/占位分支为单条固定话术 */
export interface StreamTokenEvent {
  text: string;
}

/** event: plan —— Agent Loop 规划步（LLM 决定调用哪些工具），过程事件 */
export interface StreamPlanEvent {
  step: number;
  text: string;
  tools: { name: string; args: Record<string, unknown> }[];
}

/** event: step —— Agent Loop 单个工具执行结果，过程事件 */
export interface StreamStepEvent {
  name: string;
  ok: boolean;
  summary: string;
}

/** event: confirm —— 行情灰色区间事前确认：候选标的（无数字）供前端内联点选（旧前端忽略即平滑过渡） */
export interface StreamConfirmEvent {
  session_id: string;
  message_id: string;
  question: string;
  guessed?: string;
  candidates: QuoteCandidate[];
}

/** event: done —— 正常结束 */
export interface StreamDoneEvent {
  elapsed_ms: number;
}

/** event: error —— 链路异常（status 语义与 REST 一致，如 502） */
export interface StreamErrorEvent {
  status: number;
  detail: string;
}

// ---------- L3 会话历史（memory 持久化，src/agent/schemas.py SessionItem/RestoredMessage） ----------

/** GET /sessions 会话列表项：title 取首条用户提问，updated_at 为最近一条消息时间 */
export interface SessionItem {
  session_id: string;
  title: string;
  updated_at: string | null;
  message_count: number;
}

/** 历史恢复的单条可见消息（仅 user/assistant；sources 不随存档持久化） */
export interface RestoredMessage {
  message_id: string;
  role: 'user' | 'assistant';
  content: string;
  /** 本轮意图（user 消息携带） */
  intent: string | null;
  /** 作答类型（assistant 消息携带，取值同 AnswerType，旧数据可能为 null 或新增枚举） */
  answer_type: string | null;
  created_at: string | null;
}

/** GET /sessions/{session_id}/messages 响应体（按时间升序） */
export interface SessionMessagesResponse {
  session_id: string;
  messages: RestoredMessage[];
}

/** DELETE /sessions/{session_id} 响应体 */
export interface DeleteSessionResponse {
  session_id: string;
  deleted: boolean;
}

// ---------- L3 回答反馈标注（HITL 反馈闭环，POST /feedback） ----------

/** 反馈类别：useful=有用；useless=无用/答非所问；correction=内容纠错 */
export type FeedbackCategory = 'useful' | 'useless' | 'correction';

/** POST /feedback 请求体（answer_type/intent/提问由服务端按 message_id 回填，前端只送上送项） */
export interface FeedbackRequest {
  session_id: string;
  message_id: string;
  category: FeedbackCategory;
  comment?: string | null;
}

/** POST /feedback 响应体 */
export interface FeedbackResponse {
  feedback_id: string;
  recorded: boolean;
}

// ---------- L3 行情确认候选取数（POST /quote/confirm） ----------

/** POST /quote/confirm 请求体：点选候选后按已知取数键确定性取数 */
export interface QuoteConfirmRequest {
  session_id: string;
  code: string;
  name?: string | null;
}

/** POST /quote/confirm 响应体：与行情终答同形状（answer_type=quoted） */
export interface QuoteConfirmResponse {
  session_id: string;
  message_id: string;
  answer: string;
  answer_type: AnswerType;
  intent: Intent;
  sources: SourceHit[];
  elapsed_ms: number;
}

// ---------- L2 检索服务（预留通道） ----------

export interface SearchRequest {
  query: string;
  top_k?: number;
}

export interface SearchResponse {
  query: string;
  top_k: number;
  total: number;
  results: SourceHit[];
}
