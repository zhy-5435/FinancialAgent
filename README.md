# L1 金融知识库构建系统

面向金融助手 Agent 的**数据层基地**。以 Excel 为唯一权威数据源，对**监管规则、内部制度、产品说明书**三类金融文档进行版本化管理与知识切片，构建 SQLite 权威主库 + Milvus 向量索引的双库架构，向上层应用提供**带状态与生效期过滤**的语义检索能力，并已沿 L2、L3 向上打通，形成「数据 → 服务 → 应用」全链路可运行系统。

> 分层命名：L1 = 数据层（本系统）→ L2 检索服务层（FastAPI）→ L3 Agent 应用层（LangChain + LangGraph，CLI + HTTP 双入口）→ L4 交互层（React Web 前端 `web/`），四层均已落地，后续方向见文末。

## 一、系统架构

### 1.1 分层架构总览

```
┌──────────────────────────────────────────────┐
│ L1 数据层：SQLite 主库 + Milvus 向量索引     │
└─────────────────────────┬────────────────────┘
                        │ milvus_store.search()
                        ▼
┌──────────────────────────────────────────────┐
│ L2 检索服务层：FastAPI（src/search/）       │
│ 接口：POST /search  GET /search  /health     │
└─────────────────────────┬────────────────────┘
                        │ LangChain 工具 search_knowledge
                        ▼
┌──────────────────────────────────────────────┐
│ L3 Agent 应用层：LangChain + LangGraph       │
│ 节点：retrieve / generate / refuse           │
└─────────────────────────┬────────────────────┘
                        │ POST /chat（HTTP）
                        ▼
┌──────────────────────────────────────────────┐
│ L3 HTTP 服务：FastAPI（src/agent/api.py）    │
│ 接口：POST /chat  GET /health                │
└─────────────────────────┬────────────────────┘
                        │ Vite dev proxy /api/agent
                        ▼
┌──────────────────────────────────────────────┐
│ L4 交互层：React Web 前端（web/）            │
│ 问答消息流 / 溯源折叠面板 / 服务状态指示     │
└──────────────────────────────────────────────┘
```

L3 通过 HTTP 调用 L2，不直接持有向量库连接：检索链路与应用链路解耦，向量库独占锁由 L2 单进程持有，Agent 侧可独立重启与扩缩。前端只与 L3 交互（经 Vite 代理 `/api/agent`），另预留 `/api/knowledge` 直达 L2 的检索调试通道。

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
└───────────────────────┬────────────────────────────┘
                        │ 3.有效切片批量向量化（BGE bge-base-zh-v1.5，768 维）
                        ▼
┌────────────────────────────────────────────────────┐
│    Milvus Lite 向量索引  db/milvus/l1_milvus.db    │
└───────────────────────┬────────────────────────────┘
                        │ 4.COSINE 语义检索（过滤 status=valid + 文档生效期内）
                        ▼
            5.命中 knowledge_id 回 SQLite 补全切片详情
              （原文摘录 / 标题路径 / 关键词 / 溯源信息）
```

**双库职责划分：**

- **SQLite 主库**：唯一权威事实源（Source of Truth），负责结构化存储、导入前值域校验与库内 CHECK / UNIQUE / 外键约束、切片详情查询；可独立支撑上层应用。
- **Milvus Lite**：纯检索加速层，只存向量、过滤字段与 knowledge_id，可随时从 SQLite 全量重建，不承担事实存储职责。

## 二、目录结构

```
├── data/
│   ├── excel/L1_pro.xlsx      # 权威数据源（doc_version / knowledge_chunk 两个 sheet）
│   ├── raw/正式版文档/        # 原始文档备份（三类子目录）
│   └── processed/             # 预留：中间处理产物
├── db/
│   ├── sqlite/l1_core.db      # SQLite 权威主库
│   └── milvus/l1_milvus.db    # Milvus Lite 向量索引
├── src/
│   ├── __init__.py
│   ├── config.py              # 全局路径配置（PROJECT_ROOT / DATA_DIR / DB_DIR 等）
│   ├── data/                  # L1 数据层（知识库构建与存储）
│   │   ├── __init__.py
│   │   ├── config.py          # 向量库 Collection / Embedding 模型 / TOP_K 等专有配置
│   │   ├── embedding.py       # BGE 向量服务（查询侧加检索指令前缀）
│   │   ├── sqlite_client.py   # 建表 / Excel 清洗导入 / 值域校验 / 路径对账 / 详情查询
│   │   ├── milvus_client.py   # 向量同步（幂等全删重插）/ 带生效期过滤检索
│   │   └── pipeline.py        # 全量构建入口（python -m src.data.pipeline）
│   ├── search/                # L2 检索服务层（FastAPI）
│   │   ├── __init__.py
│   │   ├── config.py          # L2 监听地址与端口
│   │   ├── schemas.py         # 检索请求 / 响应 Pydantic 模型
│   │   └── api.py             # L2 REST 接口（POST/GET /search，GET /health）
│   └── agent/                 # L3 Agent 应用层（LangChain + LangGraph）
│       ├── __init__.py
│       ├── config.py          # 加载 .env，模型 / 检索服务 / 拒答阈值 / HTTP 服务配置
│       ├── schemas.py         # 问答请求 / 响应 Pydantic 模型
│       ├── llm.py             # init_chat_model 构造大模型实例
│       ├── tools.py           # search_knowledge 工具：HTTP 调用 L2 检索
│       ├── prompts.py         # 强制溯源与拒答约束的系统提示词
│       ├── graph.py           # 问答状态图：retrieve → generate / refuse
│       ├── api.py             # L3 HTTP 服务（POST /chat /chat/stream，GET /health；:8001）
│       └── main.py            # CLI 问答入口（python -m src.agent.main）
├── web/                       # L4 React 前端（Vite + TypeScript + Tailwind）
│   ├── vite.config.ts         # dev 代理：/api/agent → :8001，/api/knowledge → :8000
│   └── src/
│       ├── api/               # 接口层：client.ts 统一 fetch、types.ts 契约镜像、agent.ts 调用
│       ├── hooks/useChat.ts   # 会话状态机与消息流生命周期
│       ├── components/        # Chat（消息流/气泡/溯源面板/输入区）、Layout（侧栏/顶栏）
│       └── pages/ChatPage.tsx # 问答主页组装
├── .env                       # 大模型 API Key 等敏感配置（含密钥，禁止提交）
├── 表结构设计.txt             # SQLite 双表 DDL 设计文档
└── requirements.txt
```

## 三、数据模型

与《表结构设计.txt》保持一致，两张核心表：

**doc_version（文档版本表）**

- `UNIQUE(doc_id, version)`：同一文档多版本可共存，为后续版本演进预留（新版本生效、旧版本置 `superseded`）
- `status` 状态机：`draft / valid / superseded / expired / archived`
- 记录发布部门、发布 / 生效 / 失效日期（`expire_date` 为 NULL 表示长期有效）

**knowledge_chunk（知识切片表）**

- 外键关联 `doc_version_id`，切片随文档版本管理
- `content` 为检索用改写文本，`original_text` 为原文逐字摘录（溯源依据）
- `chunk_type` 十类：`clause / table / exception / principle / definition / product_element / fee / risk / process / other`
- `keywords` 以 JSON 数组存储；产品说明书无条款号时 `clause_position` 为 NULL

**当前数据规模**：21 个文档版本（监管规则 4 / 内部制度 7 / 产品说明书 10），596 条有效知识切片。

## 四、快速开始

### 4.1 环境准备与 .env 配置

环境：Python 3.10+。向量模型 `BAAI/bge-base-zh-v1.5` 首次运行自动下载（已配置 hf-mirror 国内镜像）。

```powershell
pip install -r requirements.txt
```

L3 Agent 的大模型接入参数由项目根目录 `.env` 提供（`KEY=VALUE` 格式，由 `src/agent/config.py` 通过 python-dotenv 加载）。**`.env` 含密钥，已由 `.gitignore` 忽略，禁止提交版本库**：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `API_KEY` | 必填 | 大模型 API Key（亦可命名为 `OPENAI_API_KEY`，两者取先命中的） |
| `LLM_MODEL` | `GLM-5.1` | 模型名 |
| `LLM_BASE_URL` | `https://tokenhub.tencentmaas.com/v1` | OpenAI 兼容接口地址 |
| `LLM_TEMPERATURE` | `0.1` | 采样温度，低值降低编造概率 |
| `SEARCH_API_BASE` | `http://127.0.0.1:8000` | L2 检索服务地址 |
| `SEARCH_TOP_K` | `5` | Agent 每次检索的切片数 |
| `SIMILARITY_THRESHOLD` | `0.4` | 低于该相似度的切片不作为作答依据（bge-base-zh-v1.5 实测校准值） |
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

**数据更新流程**：修改 / 替换 `data/excel/L1_pro.xlsx` → 停止 L2 服务 → 重跑 `python -m src.data.pipeline` → 重启 L2。构建为全量幂等重建（导入前清空、向量同步全删重插），保证双库与 Excel 强一致；Excel 中的空行、格式噪声（如状态值带换行）由导入层自动清洗，非法值域会在导入前报错拦截。

### 4.3 检索输出示例

L1 / L2 单次检索结果示例（展示溯源五要素）：

```
===== 用户问题：货币基金的风险等级是多少 =====
【Top1】相似度：0.6149
内容：产品或服务存在下列因素应当审慎评估风险等级：杠杆易造成本金大部 / 全部亏损；
      变现困难；结构复杂难以理解；……
切片类型：risk | 标题路径：产品风险分级 > 第十七条
关键词：['高风险产品', '审慎评估', '杠杆', '流动性风险']
来源：证券期货投资者适当性管理办法（监管规则）| REG-003:V2020 | 第十七条
原文片段：第十七条　产品或者服务存在下列因素的，应当审慎评估其风险等级：（一）存在
          本金损失的可能性……
```

每次检索均返回：语义改写内容（供模型阅读）、切片类型与标题路径（定位结构）、关键词（要素提取）、文档版本与条款位置（权威溯源）、原文逐字摘录（人工核验依据）——为 L3 Agent 层「强制溯源、可核验作答」提供完整数据基础。

## 五、L2 检索服务层（src/search/）

FastAPI 将 `milvus_store.search()` 封装为 REST 接口，OpenAPI 交互文档：`http://127.0.0.1:8000/docs`。

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/search` | JSON：`query`（1~500 字）、`top_k`（1~50，默认 `TOP_K`） | `query / top_k / total / results[]` |
| GET | `/search` | Query：`query`、`top_k` | 与 POST 等价，便于调试 |
| GET | `/health` | — | `status / collection` |

`results[]` 每条切片含两类字段：

- **内容字段**：`knowledge_id`、`content`、`original_text`、`chunk_type`、`heading_path`、`clause_position`、`keywords`
- **溯源字段**：`doc_name`、`doc_type`、`doc_version_id`、`version`、`publish_dept`、`effective_date`、`similarity`

**可靠性约束**：入参由 Pydantic 校验（非法 `top_k` 返回 422）；检索链路异常统一归一为 500，不外泄内部堆栈；结果天然过滤 `status != valid` 与生效期外切片；`results` 按相似度降序，Top1 即最相关。

**真实响应示例**（`POST /search`，`{"query": "A股股票涨跌幅限制是多少", "top_k": 1}`，`content` 与 `original_text` 为排版需要做了省略）：

```json
{
  "query": "A股股票涨跌幅限制是多少",
  "top_k": 1,
  "total": 1,
  "results": [
    {
      "knowledge_id": "L1-0046",
      "doc_version_id": "REG-001:V2026",
      "chunk_type": "clause",
      "content": "本所对股票、基金交易实行价格涨跌幅限制，涨跌幅限制比例为 10%。……",
      "original_text": "3.3.13　本所对股票、基金交易实行价格涨跌幅限制，涨跌幅限制比例为 10%。……",
      "clause_position": "3.3.13",
      "heading_path": "第三章 证券买卖 > 第三节 申报 > 3.3.13",
      "keywords": ["涨跌幅限制", "10%", "无涨跌幅限制", "IPO 前 5 日", "退市整理", "重新上市"],
      "doc_name": "上海证券交易所交易规则（2026年修订）",
      "doc_type": "监管规则",
      "version": "V2026",
      "publish_dept": "上海证券交易所",
      "effective_date": "2026-01-15",
      "similarity": 0.7479
    }
  ]
}
```

## 六、L3 Agent 应用层（src/agent/）

LangGraph 状态图编排「先检索、后作答」，LLM 通过 `init_chat_model` 接入 OpenAI 兼容服务：

```
START → retrieve ─（条件路由）→ generate → END
              └────────────────→ refuse  → END
```

- **retrieve**：调用 `search_knowledge` 工具请求 L2，按 `SIMILARITY_THRESHOLD` 过滤出有效证据
- **generate**：有效证据非空时，切片以编号证据块注入系统提示词，LLM 仅据此作答
- **refuse**：无命中或全部低于阈值时直接返回固定话术，**不消耗 LLM 调用**

三条金融场景约束（见 `prompts.py`）：

- **作答范围**：只使用知识切片信息，禁止编造条款、费率、期限、风险等级；切片未涉及一律不答
- **强制溯源**：每条结论标注来源编号 `[n]`，末尾「来源」列表输出 `doc_name / version / clause_position / original_text`
- **明确拒答**：证据不足时回复「知识库中未找到可回答该问题的权威依据，暂无法作答」

**问答示例**（CLI 入口 `python -m src.agent.main`，需 L2 已启动）：

```
===== 用户问题：出差住宿报销标准是多少 =====
出差住宿报销标准按单间/天计算，同性两人出差原则上合住，按一间标准报销 [1]。
- 一类城市：员工350元、部门负责人450元、副总及以上600元 [1]；
- 二类城市：员工300元、部门负责人400元、副总及以上550元 [1]；
- 三类城市：员工250元、部门负责人350元、副总及以上500元 [1]。

来源：
- 差旅报销管理办法（内部制度）| 版本 V2.1 | 第三章 第八条 | 原文摘录：第八条 住宿费……

===== 用户问题：货币基金的风险等级是多少 =====
货币市场基金的风险等级为低风险（R1）[3]。
来源：
[3] 易方达易理财货币市场基金 B（产品说明书）| 版本 V2026-02 | 无条款号 | 原文摘录：……基金类型：货币市场基金 风险等级：低风险（R1）

===== 用户问题：明天A股大盘会涨还是跌 =====
知识库中未找到可回答该问题的权威依据，暂无法作答
```

工具链路验证（同一测试脚本先独立调用工具）：`search_knowledge` 对「科创板上市前5日涨跌幅」命中 `L1-0141 / L1-0142`，相似度 0.7139 / 0.7001。

### 6.1 L3 HTTP 服务与 L4 React 前端

`src/agent/api.py` 将问答图封装为 REST 接口（OpenAPI 文档：`http://127.0.0.1:8001/docs`），供 `web/` 前端调用：

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/chat` | JSON：`question`（1~500 字）、`session_id`（可缺省，预留字段） | `session_id / message_id / question / answer / answer_type / sources[] / elapsed_ms` |
| GET | `/health` | — | `status / search_api_base / search_api_reachable`（含 L2 连通性探测） |

`answer_type` 为 `generated`（基于有效切片作答，`sources` 携带溯源切片）或 `refused`（低置信拒答，`sources` 为空）；L2 不可达时统一返回 502 并给出启动提示。`session_id` / `message_id` 先行占位，后续升级多轮会话与 SSE 流式输出不改接口形状。

**接口响应示例**（L2 / L3 服务均在运行时，POST /chat 出参；「回答摘录」为 `answer` 前 200 字，`……` 处为省略）：

```
[health] {'status': 'ok', 'search_api_base': 'http://127.0.0.1:8000', 'search_api_reachable': True}
[chat/generated] answer_type=generated, sources=5 条, 耗时 19042 ms
  session_id=sess-072686b6bf30, message_id=msg-00e24c94366d
  回答摘录： 经营机构向普通投资者销售高风险产品或者提供相关服务时，应当履行特别的注意义务，
            具体特别安排包括： 1. 制定专门的工作程序 [1] 2. 追加了解相关信息 [1] ……
[chat/refused] answer_type=refused, sources=0 条
  回答： 知识库中未找到可回答该问题的权威依据，暂无法作答。
[chat/session] session_id 透传正常: sess-frontend-test
```

前端为 Vite + React + TypeScript + Tailwind 的简约浅色问答界面（`web/`，开发地址 `http://localhost:5173`）：消息流按 `role + kind`（normal / refused / error / pending）渲染，回答下方提供**作答依据溯源折叠面板**（文档五要素 + 原文摘录 + 相似度条，支持一键复制引用）；侧栏每 30s 轮询 `/api/agent/health`，以绿 / 黄 / 红圆点指示 L3 / L2 链路状态。请求统一经 Vite 代理：`/api/agent` → :8001、`/api/knowledge` → :8000（预留检索调试通道），前端代码只感知路径前缀，后续新增页面与服务只需在代理表与 `api/types.ts` 契约层扩展。

## 七、工程搭建规划与后续方向

### 已完成

- **L1 数据层**：双库架构（SQLite 权威主库 + Milvus 向量索引）、版本化文档与状态机、检索带生效期过滤、导入前值域 / 引用完整性校验与原始文档路径对账
- **L2 检索服务层**：FastAPI REST 接口封装（入参 query + top_k，出参切片内容与溯源信息），入参校验与异常归一，供 Agent 稳定调用
- **L3 Agent 应用层**：LangChain 工具化检索 + LangGraph 状态图编排，强制溯源与低置信拒答，CLI 问答入口
- **L3 HTTP 服务与 L4 前端**：`POST /chat` 问答接口（answer_type + sources 溯源出参，session 字段预留），React 问答界面（消息流 / 溯源面板 / 链路状态指示，Vite 代理统一路由前缀）

### 后续可选

- **检索评测集**：积累「query → 期望命中 knowledge_id」样例，每次调整后回归验证，防止检索质量退化
- **混合检索**：SQLite FTS5 关键词召回 + 向量召回，提升条款号等精确查询命中率
- **多轮会话**：LangGraph checkpointer 持久化对话状态，支持追问与指代消解（`session_id` 已由前端上送，后端接入即可）
- **流式输出**：在 `/chat` 之外新增 `POST /chat/stream`（SSE），逐 token 返回回答（接口契约已预留版本空间）
- **前端生产部署**：`web/` 构建产物由 FastAPI StaticFiles 同域挂载，去除 dev 代理与 CORS 依赖
- **文档版本演进**：新版本 Excel 行入库后，旧版本行改置 `superseded` 即完成切换，历史版本可追溯（表结构已就绪）
- **向量模型或参数调整**：重跑全量构建即可，主库无损
