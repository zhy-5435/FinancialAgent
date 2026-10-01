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

/** 识别意图：与后端 src/agent/intent.py 的 IntentName 同源；低置信度回落 kb_qa */
export type Intent = 'kb_qa' | 'chitchat' | 'news_search' | 'quote_query';

/** 作答类型：generated/refused=知识库分支；chatted=闲聊；searched=财经简报（白名单网搜证据生成，无素材时为固定话术）；quoted=实时行情快照（表格化含来源免责，不经 LLM 转写） */
export type AnswerType = 'generated' | 'refused' | 'chatted' | 'searched' | 'quoted';

export interface ChatResponse {
  session_id: string;
  message_id: string;
  question: string;
  answer: string;
  intent: Intent;
  answer_type: AnswerType;
  sources: SourceHit[];
  elapsed_ms: number;
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
