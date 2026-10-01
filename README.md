# L1 金融知识库构建系统

面向金融助手 Agent 的**数据层基地 + 全链路问答系统**。以 Excel 为唯一权威数据源，对**监管规则、内部制度、产品说明书**三类金融文档进行版本化管理与知识切片，构建 SQLite 权威主库（含 FTS5 关键词索引）+ Milvus 向量索引的双库架构，向上提供**双路混合检索**能力；L3 层以 LangGraph 编排「意图识别 → 四路分发」（知识库问答 / 闲聊 / 财经资讯网搜 / 实时行情），配套**会话记忆层**（多轮上下文 4 档梯度压缩 + 审计存档），已形成「数据 → 服务 → 应用 → 交互」四层可运行系统。

> 分层命名：L1 = 数据层（`src/data/`）→ L2 检索服务层（FastAPI，`src/search/`）→ L3 Agent 应用层（LangChain + LangGraph，`src/agent/`，CLI + HTTP 双入口）+ 会话记忆层（`src/memory/`）→ L4 交互层（React Web 前端 `web/`）。设计与验证文档统一归档于 `docs/`。

## 一、系统架构

### 1.1 分层架构总览

```
┌──────────────────────────────────────────────┐
│ L1 数据层（src/data/）                       │
│  SQLite 主库 + FTS5 关键词 │ Milvus 向量索引 │
└─────────────────────────┬────────────────────┘
                        │ hybrid_retriever 双路混合检索
                        ▼
┌──────────────────────────────────────────────┐
│ L2 检索服务层（src/search/）                 │
│  接口：POST /search  GET /search  /health    │
└─────────────────────────┬────────────────────┘
                        │ LangChain 工具 search_knowledge
                        ▼
┌──────────────────────────────────────────────┐
│ L3 Agent 应用层（src/agent/）                │
│  意图识别 → 四路分发：问答/闲聊/资讯/行情    │
│  工具：知识检索 │ 白名单网搜 │ 实时行情      │
│ ┌──────────────────────────────────────────┐ │
│ │ 会话记忆层（src/memory/）                │ │
│ │  多轮上下文 4 档压缩 / 审计存档 / 脱敏   │ │
│ └──────────────────────────────────────────┘ │
└─────────────────────────┬────────────────────┘
                        │ /chat /chat/stream /sessions
                        ▼
┌──────────────────────────────────────────────┐
│ L4 交互层：React Web 前端（web/）            │
│  流式问答 / 溯源折叠面板 / 会话历史侧栏      │
└──────────────────────────────────────────────┘
```

L3 通过 HTTP 调用 L2，不直接持有向量库连接：检索链路与应用链路解耦，向量库独占锁由 L2 单进程持有，Agent 侧可独立重启与扩缩。会话记忆层（`src/memory/`）作为 L3 的支撑模块，只被 `graph.py` / `api.py` 调用，落库到独立的 `l1_memory.db`，与权威知识库物理隔离。前端只与 L3 交互（经 Vite 代理 `/api/agent`），另预留 `/api/knowledge` 直达 L2 的检索调试通道。

### 1.2 L1 数据层数据流

```
data/excel/L1_pro.xlsx                    data/raw/正式版文档/
（权威数据源：doc_version /               （原始文档备份：监管规则类、
  knowledge_chunk 两个 sheet）            内部制度类、产品说明书类）
        │                                       │
        │ 1.清洗导入 + 约束校验                 │ 2.file_path 对账校验
        ▼                                       ▼
┌────────────────────────────────────────────────────┐
│         SQLite 主库  db/sqlite/l1_core.db          │
│     doc_version（文档版本表）                      │
│       │ 外键关联 doc_version_id                    │
│     knowledge_chunk（知识切片表）                  │
│       │ 同步重建 FTS5 关键词虚拟表                 │
│     fts_l1_knowledge（中文单字 + 英文数字整词）    │
└───────────────────────┬────────────────────────────┘
                        │ 3.有效切片批量向量化（BGE bge-base-zh-v1.5，768 维）
                        ▼
┌────────────────────────────────────────────────────┐
│    Milvus Lite 向量索引  db/milvus/l1_milvus.db    │
└───────────────────────┬────────────────────────────┘
                        │ 4.双路混合检索：向量语义召回（COSINE，过滤 status=valid
                        │   + 生效期内）与 SQLite FTS5 关键词召回去重补齐，
                        │   两路得分 min-max 归一化后加权排序（向量 0.8 + 关键词 0.2）
                        ▼
            5.命中 knowledge_id 回 SQLite 补全切片详情
              （原文摘录 / 标题路径 / 关键词 / 溯源信息）
```

**双库职责划分：**

- **SQLite 主库**：唯一权威事实源（Source of Truth），负责结构化存储、导入前值域校验与库内 CHECK / UNIQUE / 外键约束、切片详情查询，并承载 FTS5 关键词召回；可独立支撑上层应用。
- **Milvus Lite**：纯检索加速层，只存向量、过滤字段与 knowledge_id，可随时从 SQLite 全量重建，不承担事实存储职责。
- **FTS5 关键词索引**：与向量索引同范围（仅 `status=valid` 切片）、随导入全量重建；当前 SQLite 环境不支持 FTS5 时自动退化为纯向量检索。

## 二、目录结构

```
├── data/
│   ├── excel/L1_pro.xlsx      # 权威数据源（doc_version / knowledge_chunk 两个 sheet）
│   ├── raw/正式版文档/        # 原始文档备份（监管规则 / 内部制度 / 产品说明书三类子目录）
│   └── processed/             # 预留：中间处理产物
├── db/                        # 构建产物（gitignore），可由 python -m src.data.pipeline 全量重建
│   ├── sqlite/l1_core.db      # SQLite 权威主库（含 FTS5 关键词索引）
│   ├── sqlite/l1_memory.db    # 会话记忆库（与主库物理隔离，互不污染）
│   └── milvus/l1_milvus.db    # Milvus Lite 向量索引
├── docs/                      # 设计与验证文档
│   ├── 表结构设计.txt                 # SQLite 主库双表 DDL 设计文档
│   ├── 意图识别与工具库规划.md        # 意图识别 / 工具库建设规划（M1~M4 落地路线）
│   ├── 检索验证报告.md                # 双路召回检索效果验证与调参报告
│   └── 计划评估.md                    # 现状评估与自治 Agent 差距分析
├── src/
│   ├── config.py              # 全局共享路径配置（PROJECT_ROOT / 双库 / 记忆库路径）
│   ├── data/                  # L1 数据层（知识库构建与存储）
│   │   ├── config.py          # Collection / Embedding 模型 / TOP_K / 双路权重
│   │   ├── embedding.py       # BGE 向量服务（查询侧加检索指令前缀）
│   │   ├── sqlite_client.py   # 建表 / 清洗导入 / 值域校验 / 路径对账 / FTS5 关键词检索
│   │   ├── milvus_client.py   # 向量同步 / 带生效期过滤检索 / 指定切片相似度补算
│   │   ├── hybrid_retriever.py# 双路混合检索编排（向量 + 关键词，归一化加权）
│   │   └── pipeline.py        # 全量构建入口（python -m src.data.pipeline）
│   ├── search/                # L2 检索服务层（FastAPI）
│   │   ├── config.py          # L2 监听地址与端口
│   │   ├── schemas.py         # 检索请求 / 响应 Pydantic 模型
│   │   └── api.py             # POST/GET /search、GET /health（:8000）
│   ├── memory/                # 会话记忆层（L3 多轮支撑模块）
│   │   ├── config.py          # 上下文预算 / 4 档压缩阈值 / 脱敏开关 / 摘要模型
│   │   ├── memory_manager.py  # 门面：落库本轮 + 构建上下文 + 历史恢复
│   │   ├── schemas.py         # 消息 / 上下文包 / 压缩记录 Pydantic 模型
│   │   ├── prompts.py         # 历史摘要提示词（业务要点 + 关键实体清单）
│   │   ├── store/
│   │   │   ├── sqlite_store.py# conversation_message + compression_log（审计存档永不删除）
│   │   │   └── masking.py     # 入库前 PII 脱敏（身份证 / 手机 / 银行卡 / 邮箱）
│   │   └── context/
│   │       ├── builder.py     # 上下文引擎主编排（block / turns 两种组装模式）
│   │       ├── budget.py      # 按可用预算占比判定 4 档（direct/warning/global/trim）
│   │       ├── summarizer.py  # 局部 / 全局摘要生成 + 关键实体存活校验
│   │       ├── tagging.py     # 消息优先级打标（锁存实体 > 核心业务 > 工具 > 普通）
│   │       ├── tool_prefilter.py # 历史工具结果精简为结构化摘要，防吃满预算
│   │       └── tokenizer.py   # 字符启发式 token 估算（零新增依赖）
│   └── agent/                 # L3 Agent 应用层（LangChain + LangGraph）
│       ├── config.py          # .env 集中加载：模型 / 检索 / 意图 / 网搜 / 行情 / HTTP
│       ├── schemas.py         # 问答请求 / 响应 / 会话 Pydantic 模型
│       ├── llm.py             # init_chat_model 构造大模型实例
│       ├── prompts.py         # 四分支系统提示词（溯源约束 / 闲聊 / 简报 / 意图分类）
│       ├── intent.py          # 意图识别：规则短路 + LLM 四分类，低置信回落 kb_qa
│       ├── tools.py           # 三个 LangChain 工具（知识检索 / 网搜 / 行情）
│       ├── web_search.py      # Tavily 白名单网搜 + 直连补抓 + 证据块打包
│       ├── quote_service.py   # 新浪实时行情（A股/ETF/指数/港股/美股，防幻觉规范）
│       ├── graph.py           # 意图路由状态图 + SSE 流式生成器（invoke / stream 双链路）
│       ├── api.py             # L3 HTTP 服务（:8001，chat / chat/stream / sessions）
│       └── main.py            # CLI 问答入口（python -m src.agent.main）
├── web/                       # L4 React 前端（Vite + TypeScript + Tailwind）
│   ├── vite.config.ts         # dev 代理：/api/agent → :8001，/api/knowledge → :8000
│   └── src/
│       ├── api/               # 接口层：client.ts 统一 fetch、types.ts 契约镜像、agent.ts 调用
│       ├── hooks/useChat.ts   # 消息流状态机（SSE 流式消费、拒答/错误态）
│       ├── hooks/useSessions.ts # 会话列表拉取 / 历史恢复 / 删除
│       ├── components/        # Chat（消息流/气泡/溯源面板/输入区）、Layout（侧栏）
│       └── pages/ChatPage.tsx # 问答主页组装
├── .env                       # 大模型 / Tavily API Key 等敏感配置（禁止提交）
└── requirements.txt
```

## 三、数据模型

与《docs/表结构设计.txt》保持一致，权威主库两张核心表：

**doc_version（文档版本表）**

- `UNIQUE(doc_id, version)`：同一文档多版本可共存，为后续版本演进预留（新版本生效、旧版本置 `superseded`）
- `status` 状态机：`draft / valid / superseded / expired / archived`
- 记录发布部门、发布 / 生效 / 失效日期（`expire_date` 为 NULL 表示长期有效）

**knowledge_chunk（知识切片表）**

- 外键关联 `doc_version_id`，切片随文档版本管理
- `content` 为检索用改写文本，`original_text` 为原文逐字摘录（溯源依据）
- `chunk_type` 十类：`clause / table / exception / principle / definition / product_element / fee / risk / process / other`
- `keywords` 以 JSON 数组存储；产品说明书无条款号时 `clause_position` 为 NULL

**fts_l1_knowledge（FTS5 关键词虚拟表）**

- 仅索引 `status=valid` 切片的 `content + keywords`（与向量路同步范围一致）
- 分词规则：中文按单字切分，英文 / 数字（含 `2.4.2`、`20%` 等）连续串作整词，建索引与查询两侧共用

**会话记忆库 l1_memory.db（与主库物理隔离）**

- `conversation_message`：原始完整消息序列（user / assistant / tool 三角色，含 `intent` / `answer_type` / `priority_tag` / `token_est` / `tool_payload` / `compression_state`），**审计存档永不删除**，压缩仅改状态不删原文
- `compression_log`：摘要压缩元数据（覆盖消息 ID 集合、摘要版本、关键实体清单），保证压缩可追溯

**当前数据规模**：21 个文档版本（监管规则 4 / 内部制度 7 / 产品说明书 10），596 条有效知识切片。

## 四、快速开始

### 4.1 环境准备与 .env 配置

环境：Python 3.10+。向量模型 `BAAI/bge-base-zh-v1.5` 首次运行自动下载（已配置 hf-mirror 国内镜像，本地已有缓存时强制离线加载）。

```powershell
pip install -r requirements.txt
```

各层参数由项目根目录 `.env` 提供（`KEY=VALUE` 格式，由 `src/agent/config.py` 与 `src/memory/config.py` 通过 python-dotenv 加载）。**`.env` 含密钥，已由 `.gitignore` 忽略，禁止提交版本库**：

**大模型与检索链路**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `API_KEY` | 必填 | 大模型 API Key（亦可命名为 `OPENAI_API_KEY`，两者取先命中的） |
| `LLM_MODEL` | `GLM-5.1` | 模型名 |
| `LLM_BASE_URL` | `https://tokenhub.tencentmaas.com/v1` | OpenAI 兼容接口地址 |
| `LLM_TEMPERATURE` | `0.1` | 采样温度，低值降低编造概率 |
| `LLM_DISABLE_THINKING` | `false` | 对支持该参数的模型关闭思考模式（首 token 延迟 ~3-9s → ~0.5s） |
| `SEARCH_API_BASE` | `http://127.0.0.1:8000` | L2 检索服务地址 |
| `SEARCH_TOP_K` | `5` | Agent 每次检索的切片数 |
| `SIMILARITY_THRESHOLD` | `0.4` | 低于该相似度的切片不作为作答依据（bge-base-zh-v1.5 实测校准值） |

**意图识别与分支工具（网搜 / 行情）**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `INTENT_RULES_ENABLED` | `true` | 关键词/规则预分类短路（强信号命中跳过意图 LLM，省 ~10s） |
| `INTENT_CONFIDENCE_THRESHOLD` | `0.6` | 非 kb_qa 意图低于该置信度一律回落 kb_qa（宁可拒答不乱分流） |
| `TAVILY_API_KEY` | 空 | Tavily 搜索 Key（未配置时联网搜索优雅降级为空结果） |
| `TAVILY_SEARCH_DEPTH` | `advanced` | 检索深度（advanced 正文提取更全，消耗 2 倍额度） |
| `WEB_SEARCH_MAX_PAGES` | `5` | 打包进 Prompt 的白名单网页数上限 |
| `QUOTE_TIMEOUT_SECONDS` | `10` | 行情接口硬超时，超时即降级为固定话术 |
| `QUOTE_FUZZY_MIN_CONFIDENCE` | `0.6` | 错别字/近似标的 LLM 推断命中阈值，低于则走未识别话术 |

**会话记忆层**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_MASKING_ENABLED` | `true` | 存档入库前 PII 脱敏（身份证/手机/银行卡/邮箱） |
| `CONTEXT_MAX_TOKENS` | `32000` | 模型上下文窗口预算（字符启发式估算口径） |
| `CONTEXT_OUTPUT_RESERVE` | `2000` | 为模型输出预留的 token |
| `CONTEXT_TIER_WARNING` / `GLOBAL` / `TRIM` | `0.70` / `0.82` / `0.92` | 4 档梯度压缩阈值（占可用预算比例） |
| `CONTEXT_RECENT_FULL_ROUNDS` / `KEEP_ROUNDS` | `3` / `2` | 预警档 / 全局摘要档完整保留的最近轮数 |
| `MEMORY_SUMMARIZER_MODEL` | 同 `LLM_MODEL` | 摘要模型（可独立配置更轻量模型做摘要） |

**HTTP 服务**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `AGENT_API_HOST` / `AGENT_API_PORT` | `0.0.0.0` / `8001` | L3 HTTP 服务监听地址 |
| `AGENT_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许跨域的前端来源（逗号分隔，置空则关闭 CORS） |

### 4.2 启动链路

```powershell
# 1) 全量构建 L1 知识库（L2 / L3 未运行时执行，独占 MilvusLite 锁）
python -m src.data.pipeline

# 2) 启动 L2 检索服务（独占向量库连接，另开终端）
python -m uvicorn src.search.api:app --host 127.0.0.1 --port 8000

# 3) 启动 L3 Agent HTTP 服务（另开终端，供前端调用）
python -m src.agent.api

# 4) 启动 React 前端（web/ 目录，访问 http://localhost:5173）
cd web; npm install; npm run dev

# CLI 问答（需 L2 已启动）
python -m src.agent.main
```

> MilvusLite 为**单进程独占锁**：L2 服务运行期间不能同时执行 `src.data.pipeline`，否则报 `DataDirLockedError`；构建完成后先起 L2，再跑 Agent。

**数据更新流程**：修改 / 替换 `data/excel/L1_pro.xlsx` → 停止 L2 服务 → 重跑 `python -m src.data.pipeline` → 重启 L2。构建为全量幂等重建（导入前清空、向量与 FTS5 索引全删重插），保证双库与 Excel 强一致；Excel 中的空行、格式噪声（如状态值带换行）由导入层自动清洗，非法值域会在导入前报错拦截。会话记忆库独立于构建流程，重跑 pipeline 不影响历史会话。

## 五、L2 检索服务层（src/search/）

FastAPI 将 `hybrid_retriever.search()`（双路混合检索）封装为 REST 接口，OpenAPI 交互文档：`http://127.0.0.1:8000/docs`。

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/search` | JSON：`query`（1~500 字）、`top_k`（1~50，默认 `TOP_K`） | `query / top_k / total / results[]` |
| GET | `/search` | Query：`query`、`top_k` | 与 POST 等价，便于调试 |
| GET | `/health` | — | `status / collection` |

**双路混合检索机制**（`src/data/hybrid_retriever.py`，金融场景「精准优先」）：

1. **双路召回**：Milvus 向量语义召回 + SQLite FTS5 关键词召回，各取 `CANDIDATE_TOP_K=10` 候选；
2. **去重补齐**：两路按 `knowledge_id` 合并，仅关键词路命中的切片按查询向量补算余弦相似度，保证两路得分口径一致；
3. **归一化加权排序**：两路得分各自 min-max 归一化到 [0,1] 后加权——`综合得分 = 归一化向量相似度 × 0.8 + 归一化关键词得分 × 0.2`。权重与归一化策略经 14 条评测集网格搜索校准（BGE 余弦压缩在 0.5~0.75、BM25 铺满 0~1，不归一直接加权会放大关键词噪声，验证过程见 `docs/检索验证报告.md`）；
4. **优雅退化**：FTS5 不可用时自动退化为纯向量检索，出参字段保持统一。

`results[]` 每条切片含三类字段：

- **内容字段**：`knowledge_id`、`content`、`original_text`、`chunk_type`、`heading_path`、`clause_position`、`keywords`
- **溯源字段**：`doc_name`、`doc_type`、`doc_version_id`、`version`、`publish_dept`、`effective_date`
- **得分字段**：`similarity`（余弦相似度）、`keyword_score`（BM25 归一化）、`score`（综合得分，按其降序）

**可靠性约束**：入参由 Pydantic 校验（非法 `top_k` 返回 422）；检索链路异常统一归一为 500，不外泄内部堆栈；结果天然过滤 `status != valid` 与生效期外切片。

**真实响应示例**（`POST /search`，`{"query": "A股股票涨跌幅限制是多少", "top_k": 1}`，`content` 与 `original_text` 为排版需要做了省略）：

```json
{
  "query": "A股股票涨跌幅限制是多少",
  "top_k": 1,
  "total": 1,
  "results": [
    {
      "knowledge_id": "L1-0218",
      "doc_version_id": "REG-002:V2026",
      "chunk_type": "clause",
      "content": "本所对证券交易实行价格涨跌幅限制。主板股票的价格涨跌幅限制比例为 10%，创业板股票的价格涨跌幅限制比例为 20%。……",
      "original_text": "3.3.13 本所对证券交易实行价格涨跌幅限制。……本所向市场公布涨跌幅限制比例为 20% 的基金名单。",
      "clause_position": "3.3.13",
      "heading_path": "第三章 证券买卖 > 第三节 申报 > 3.3.13",
      "keywords": ["涨跌幅限制", "主板 10%", "创业板 20%", "基金涨跌幅"],
      "doc_name": "深圳证券交易所交易规则（2026年修订）",
      "doc_type": "监管规则",
      "version": "V2026",
      "publish_dept": "深圳证券交易所",
      "effective_date": "2026-01-18",
      "similarity": 0.6733,
      "keyword_score": 0.9923,
      "score": 0.9985
    }
  ]
}
```

每次检索均返回：语义改写内容（供模型阅读）、切片类型与标题路径（定位结构）、关键词（要素提取）、文档版本与条款位置（权威溯源）、原文逐字摘录（人工核验依据）与三得分（排序依据）——为 L3 Agent 层「强制溯源、可核验作答」提供完整数据基础。

## 六、L3 Agent 应用层（src/agent/）

LangGraph 状态图编排「意图识别 → 四路分发」，LLM 通过 `init_chat_model` 接入 OpenAI 兼容服务：

```
START → classify_intent ─（条件路由）─┬─ kb_qa       → retrieve ─┬→ generate → END
                                     │（L2 检索+阈值过滤）      └→ refuse  → END
                                     ├─ chitchat    → chat_generate → END
                                     ├─ news_search → news_generate → END
                                     └─ quote_query → quote_answer  → END
```

### 6.1 意图识别（intent.py）

LLM 结构化输出四分类 + 实体抽取（`symbol` / `news_topic` / `rewritten_query`），兼顾时延与可靠性：

| 意图 | 触发场景 | 分发链路 | answer_type |
|---|---|---|---|
| `kb_qa` | 监管规则 / 内部制度 / 产品说明书类业务问题 | L2 检索 → 有效切片约束作答 / 无有效证据拒答 | `generated` / `refused` |
| `chitchat` | 寒暄、身份与能力提问、常识闲聊 | 通用对话提示词，不检索、不带溯源 | `chatted` |
| `news_search` | 财经新闻、市场动态、政策快讯 | Tavily 白名单网搜 → 财经简报生成；无素材固定话术 | `searched` |
| `quote_query` | 最新价 / 涨跌幅 / 行情类提问或 6 位代码 | 行情工具取数 → 表格化呈现（数字不经 LLM） | `quoted` |

- **规则短路**：行情 / 资讯 / 闲聊三类强信号词经关键词规则直接分流（跳过一次阻塞式意图 LLM 调用，实测省 ~10s）；规则只在强信号时触发，拿不准一律交给 LLM。
- **兜底策略**：分类链路异常或置信度低于 `INTENT_CONFIDENCE_THRESHOLD` 的非 kb_qa 意图，一律回落 kb_qa——保留检索改写与既有拒答底线，宁可拒答不乱分流。

### 6.2 工具层（tools.py + web_search.py + quote_service.py）

惯例为「薄封装 + 独立服务模块」，三个 LangChain 工具均带超时与降级，永不抛错阻断主链路：

- `search_knowledge`：HTTP 调用 L2 混合检索，返回带三得分与溯源字段的切片列表
- `web_search`：Tavily 搜索以 `include_domains` 限定**权威信源白名单**（8 家证监会指定信息披露媒体 + 18 家监管机构 / 交易所 / 行业协会官网，名单见 `config.py`），结果二次域名过滤 + 直连补抓正文，打包为带「站点 | 标题 | URL | 时间 | 原文摘录」的证据块供 LLM 引用 URL 作答；无命中返回明确空标记，禁止编造
- `search_realtime_quote`：新浪财经公开行情接口（`hq.sinajs.cn` 单标的直连 + `suggest3` 名称检索），覆盖 A 股个股 / ETF / 指数、港股与全球指数、美股；标的解析按「显式代码 → 常见指数静态映射 → 检索端点 → LLM 错别字推断（宁缺毋滥）」优先级，推断命中时在回答开头固定话术说明；**行情数字与数据源逐字一致、不经 LLM 转写**，附来源与免责，超时 / 无数据降级为固定话术

### 6.3 金融场景约束（prompts.py）

- **作答范围**：kb_qa 分支只使用知识切片信息，禁止编造条款、费率、期限、风险等级；切片未涉及一律不答
- **强制溯源**：每条结论标注来源编号 `[n]`，末尾「来源」列表输出 `doc_name / version / clause_position / original_text`
- **明确拒答**：证据不足时回复「知识库中未找到可回答该问题的权威依据，暂无法作答」，**不消耗 LLM 作答调用**
- **来源区分**：知识库回答带条款溯源、财经简报带 URL、行情快照带「仅供参考」免责，四类回答在前端可见地区分，保证溯源体系不被多分支稀释

### 6.4 会话记忆层（src/memory/）

`session_id` 非空即进入多轮模式，`graph.py` 各分支作答前调 `memory_manager.build()` 组装上下文、结果产出后 `remember_*` 落库；单轮模式（CLI 默认）跳过记忆链路：

- **持久化**（store/）：消息全量落 `l1_memory.db`（审计存档永不删除，压缩仅置状态），入库前 PII 脱敏；`compression_log` 登记每次摘要的覆盖范围与版本
- **上下文引擎**（context/）：字符启发式 token 估算 → 消息优先级打标（锁存实体 > 核心业务 > 工具结果 > 普通对话）→ 历史工具结果精简 → 按可用预算占比走 **4 档梯度压缩**：`direct`（<70% 全量直装）、`warning`（70~82% 最早低优先级局部摘要、最近 3 轮完整）、`global`（82~92% 全局摘要 + 关键实体存活校验）、`trim`（>92% 摘要后再裁剪）
- **两种组装模式**：`block`（kb_qa：历史以带框定语的文本块并入系统提示词，**事实只依据本轮检索，历史仅供指代**）；`turns`（chitchat / news：历史作为既往轮次注入）
- **历史恢复**：`GET /sessions` / `GET /sessions/{id}/messages` / `DELETE /sessions/{id}` 供前端侧栏会话列表、多轮回放与删除

### 6.5 HTTP 服务与 L4 React 前端

`src/agent/api.py` 将问答链路封装为 REST 接口（OpenAPI 文档：`http://127.0.0.1:8001/docs`），供 `web/` 前端调用：

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/chat` | JSON：`question`（1~500 字）、`session_id`（缺省服务端生成，非空即多轮） | `session_id / message_id / question / answer / intent / answer_type / sources[] / elapsed_ms` |
| POST | `/chat/stream` | 同上 | SSE 事件流：`meta`（intent + answer_type + sources）→ `token`（增量文本）→ `done`（elapsed_ms） |
| GET | `/sessions` | `limit`（1~200） | 按最近活跃降序的会话列表（标题取首条提问） |
| GET | `/sessions/{id}/messages` | — | 可见历史消息（仅 user/assistant，时间升序，存档已脱敏） |
| DELETE | `/sessions/{id}` | — | 删除会话存档 |
| GET | `/health` | — | `status / search_api_base / search_api_reachable`（含 L2 连通性探测） |

`sources` 仅 kb_qa 有效作答时非空（含溯源五要素与三得分）；L2 不可达时统一返回 502 并给出启动提示。

**真实运行示例**（L2 / L3 服务均在运行，POST /chat 出参；「回答摘录」为 `answer` 前段，`……` 处为省略）：

```
[health] {'status': 'ok', 'search_api_base': 'http://127.0.0.1:8000', 'search_api_reachable': True}

[chat/kb_qa]  question=出差住宿报销标准是多少  intent=kb_qa  answer_type=generated  耗时 5857 ms
  回答摘录：根据《差旅报销管理办法》，出差住宿费按单间/天计算，同性两人出差原则上合住，
            按一间标准报销。具体标准如下 [1]：一类城市：员工350元/天，部门负责人450元/天……
  sources=5 条，首条：差旅报销管理办法 | similarity=0.6458 | keyword_score=1.0 | score=1.0

[chat/refused] question=个人房贷的首付比例最低是多少  intent=kb_qa  answer_type=refused  耗时 3328 ms
  回答：知识库中未找到可回答该问题的权威依据，暂无法作答。

[chat/chitchat] question=你好，你是谁  intent=chitchat  answer_type=chatted  耗时 7096 ms
  回答摘录：你好！我是你的友好金融助理……对于具体的金融产品条款、费率、风险等级这些
            需要精确数据的内容，我不会随便给结论……

[chat/quote]   question=贵州茅台 600519 最新价  intent=quote_query  answer_type=quoted  耗时 567 ms
  **贵州茅台（sh600519）实时行情** · 行情时间：2026-09-30 15:34:59
  | 指标 | 数值 | —— 最新价 1258.620 | 涨跌幅 +1.86% | 昨收 1235.580 | 成交额 47.97 亿 CNY ……
  来源：新浪财经公开行情接口（hq.sinajs.cn 实时快照）＋ 免责固定话术（数字不经 LLM 转写）

[chat/stream]  question=差旅住宿报销标准是多少
  event: meta   data={"intent": "kb_qa", "answer_type": "generated", "sources": [ …5 条切片… ]}
  event: token  data={"text": "根据"}   event: token  data={"text": "差"}   ……（逐 token 增量）
  event: done   data={"elapsed_ms": 8448}

[sessions]     GET /sessions → [{"session_id": "sess-…", "title": "货币基金的风险等级是多少",
                                 "updated_at": "2026-10-01 20:46:45", "message_count": 6}, …]
```

多轮指代示例：同一 `session_id` 下先问「货币基金的风险等级是多少」，追问「**那它**属于高风险产品吗，需要特别注意什么」——上下文引擎注入历史供指代消解，作答事实仍只依据本轮新检索的切片（回答明确声明「切片中未包含针对货币基金风险等级的直接认定规则」，并给出高风险产品销售的特别义务条款溯源）。

**前端**为 Vite + React + TypeScript + Tailwind 的简约浅色问答界面（`web/`，开发地址 `http://localhost:5173`）：

- 消息流按 `role + kind`（normal / refused / error / pending）渲染，SSE 逐 token 流式输出；回答下方提供**作答依据溯源折叠面板**（文档五要素 + 原文摘录 + 相似度条，支持一键复制引用）
- 侧栏提供**会话历史列表**（拉取 / 恢复 / 删除，对接 `/sessions` 契约）与每 30s 轮询的 `/api/agent/health` 链路状态灯（绿 / 黄 / 红指示 L3 / L2）
- 请求统一经 Vite 代理：`/api/agent` → :8001、`/api/knowledge` → :8000（预留检索调试通道），新增服务只需扩展代理表与 `api/types.ts` 契约层

## 七、工程搭建规划与后续方向

### 已完成

- **L1 数据层**：双库架构（SQLite 权威主库 + Milvus 向量索引）、版本化文档与状态机、检索带生效期过滤、导入前值域 / 引用完整性校验与原始文档路径对账；FTS5 关键词索引
- **L2 检索服务层**：双路混合检索（向量 + 关键词，min-max 归一化加权 0.8/0.2，权重经评测集网格搜索校准），REST 接口封装与入参校验、异常归一，FTS5 缺失时优雅退化
- **L3 Agent 应用层**：LangGraph「意图识别 → 四路分发」（知识库问答 / 闲聊 / 财经资讯 / 实时行情），规则短路降时延、低置信回落保底线；强制溯源与低置信拒答；三个工具（知识检索 / Tavily 白名单网搜 / 新浪行情防幻觉直出）；CLI 问答入口
- **会话记忆层**：独立记忆库审计存档（永不删除 + PII 脱敏）、上下文 4 档梯度压缩与关键实体锁存、多轮指代消解（block / turns 双模式）
- **L3 HTTP 服务与 L4 前端**：`/chat` + `/chat/stream`（SSE）双链路、`/sessions` 会话档案契约、React 问答界面（流式消息流 / 溯源面板 / 会话历史侧栏 / 链路状态灯）

### 后续可选

- **检索评测集扩展**：当前 14 条样例（见 `docs/检索验证报告.md`）继续扩充规模，接入 CI 回归，防止检索质量退化
- **溯源后置验证**：对生成回答中的引用条款做原文存在性校验，进一步收紧幻觉面
- **行情标的扩展**：场外基金净值、期货等品种（当前覆盖 A 股 / ETF / 指数 / 港股 / 美股，其余类型显式剔除）
- **前端生产部署**：`web/` 构建产物由 FastAPI StaticFiles 同域挂载，去除 dev 代理与 CORS 依赖
- **文档版本演进**：新版本 Excel 行入库后，旧版本行改置 `superseded` 即完成切换，历史版本可追溯（表结构已就绪）
- **向量模型或参数调整**：重跑全量构建即可，主库无损
- **自治 Agent Loop / MCP 化**：规划与差距分析见 `docs/计划评估.md` 与 `docs/意图识别与工具库规划.md`
