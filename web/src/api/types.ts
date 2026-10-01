// 与后端接口契约逐字段对齐（src/agent/api.py、src/search/api.py）
// 后端新增字段（如流式、反馈）时在此集中演进，组件类型随之可感知

// ---------- L3 Agent 问答服务 ----------

export interface ChatRequest {
  question: string;
  /** 预留：当前后端无状态，前端生成并上送，未来多轮会话直接复用 */
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
  /** 综合得分 = 相似度×0.7 + 关键词得分×0.3 */
  score: number;
}

export type AnswerType = 'generated' | 'refused';

export interface ChatResponse {
  session_id: string;
  message_id: string;
  question: string;
  answer: string;
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

/** event: meta —— 检索完成即到达，携带作答类型与溯源切片（可先于正文渲染溯源面板） */
export interface StreamMetaEvent {
  session_id: string;
  message_id: string;
  question: string;
  answer_type: AnswerType;
  sources: SourceHit[];
}

/** event: token —— LLM 增量文本，拒答分支为单条固定话术 */
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
