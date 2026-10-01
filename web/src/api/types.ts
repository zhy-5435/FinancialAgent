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
