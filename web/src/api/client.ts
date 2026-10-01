// fetch 统一封装：路由前缀映射、超时控制、FastAPI 错误体归一化
// 后续新增后端服务只需在 API_PREFIX 中登记前缀，调用方不感知真实端口

const API_PREFIX = {
  agent: '/api/agent',
  knowledge: '/api/knowledge',
} as const;

export type ApiScope = keyof typeof API_PREFIX;

/** 归一化后的接口错误：status 为 HTTP 状态码，network 表示服务不可达 */
export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

interface RequestInitLike {
  method?: string;
  body?: unknown;
  timeoutMs?: number;
}

export async function apiFetch<T>(
  scope: ApiScope,
  path: string,
  { method = 'GET', body, timeoutMs = 120_000 }: RequestInitLike = {},
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(`${API_PREFIX[scope]}${path}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });

    if (!resp.ok) {
      // FastAPI 错误体统一为 {detail: string}；解析失败时回落状态码描述
      let detail = `HTTP ${resp.status}`;
      try {
        const data = await resp.json();
        if (typeof data?.detail === 'string') detail = data.detail;
      } catch {
        /* 非 JSON 错误体，保持默认 detail */
      }
      throw new ApiError(resp.status, detail);
    }
    return (await resp.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError(0, '请求超时，请稍后重试');
    }
    // fetch 网络层失败（代理目标未启动时 Vite 返回 500/HTML 或连接被拒）
    throw new ApiError(0, '无法连接后端服务，请确认 L2/L3 服务已启动');
  } finally {
    clearTimeout(timer);
  }
}

/** SSE 单帧解析结果 */
export interface StreamEvent {
  event: string;
  data: unknown;
}

/** 解析一帧 SSE：event: 行 + 多条 data: 行（JSON 负载内不会出现裸换行） */
function parseFrame(frame: string): StreamEvent | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) };
  } catch {
    return null;
  }
}

/**
 * POST 并流式消费 SSE 响应（fetch + ReadableStream，EventSource 不支持 POST 故手写）：
 * 逐 chunk 解码、按空行切帧、增量派发，不做整包缓冲；
 * onEvent 抛出异常会中断读取并向上传播（用于 error 事件转 ApiError）。
 */
export async function apiStream(
  scope: ApiScope,
  path: string,
  body: unknown,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${API_PREFIX[scope]}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(0, '无法连接后端服务，请确认 L2/L3 服务已启动');
  }

  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      if (typeof data?.detail === 'string') detail = data.detail;
    } catch {
      /* 非 JSON 错误体，保持默认 detail */
    }
    throw new ApiError(resp.status, detail);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    // stream: true 处理跨 chunk 被切断的多字节字符
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseFrame(frame.replace(/\r\n/g, '\n'));
      if (ev) onEvent(ev);
    }
  }
  const tail = parseFrame(buffer.replace(/\r\n/g, '\n'));
  if (tail) onEvent(tail);
}
