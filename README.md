# L1 金融知识库构建系统

面向金融助手 Agent 的**数据层基地**。以 Excel 为唯一权威数据源，对**监管规则、内部制度、产品说明书**三类金融文档进行版本化管理与知识切片，构建 SQLite 权威主库 + Milvus 向量索引的双库架构，向上层应用提供**带状态与生效期过滤**的语义检索能力。

> 分层命名：L1 = 数据层（本系统）→ L2 检索服务层 → L3 Agent 应用层，规划见文末。

## 一、系统架构

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
                        │ 3.有效切片批量向量化（BGE bge-small-zh-v1.5，512 维）
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
│   ├── excel/L1_pro.xlsx      # 权威数据源（doc_version / knowledge_chunk）
│   ├── raw/正式版文档/          # 原始文档（三类子目录）
│   └── processed/             # 预留：中间处理产物
├── db/
│   ├── sqlite/l1_core.db      # SQLite 权威主库
│   └── milvus/l1_milvus.db    # Milvus Lite 向量索引
├── src/
│   ├── config.py              # 路径、模型、向量维度等全局配置
│   ├── sqlite_client.py       # 建表 / Excel 清洗导入 / 值域校验 / 路径校验 / 详情查询
│   ├── milvus_client.py       # 向量同步（全删重插，幂等）/ 带过滤检索
│   ├── embedding.py           # BGE 向量服务（查询侧加检索指令前缀）
│   └── build_pipeline.py      # 全量构建入口（导入 → 校验 → 同步 → 验证）
├── test_search.py             # 检索功能测试
├── 表结构设计.txt               # SQLite 双表 DDL 设计文档
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

环境：Python 3.10+。向量模型 `BAAI/bge-small-zh-v1.5` 首次运行自动下载（已配置 hf-mirror 国内镜像）。

```powershell
pip install -r requirements.txt

# 全量构建：Excel 导入 → 原始文档路径校验 → 向量同步 → 检索验证
python -m src.build_pipeline

# 独立检索测试
python test_search.py
```

**数据更新流程**：修改 / 替换 `data/excel/L1_pro.xlsx` → 重跑 `python -m src.build_pipeline`。构建为全量幂等重建（导入前清空、向量同步全删重插），保证双库与 Excel 强一致；Excel 中的空行、格式噪声（如状态值带换行）由导入层自动清洗，非法值域会在导入前报错拦截。

**检索输出示例**（节选自 `test_search.py` 实际运行结果，展示溯源五要素）：

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

## 五、工程搭建规划（金融助手 Agent）

分层推进，保持简单可靠：

### L1 数据层 —— 本系统（已完成）

- 双库架构：SQLite 权威主库 + Milvus 向量索引，向量库随时可从主库全量重建
- 版本化文档管理与状态机，检索天然带生效期过滤
- 数据质量保障：导入前值域 / 引用完整性校验，原始文档路径对账

### L2 检索服务层（下一步）

- **FastAPI 轻量服务**：将 `milvus_store.search()` 封装为 REST 接口（入参 query + top_k，出参切片内容与溯源信息），供 Agent 稳定调用
- **检索评测集**：积累「query → 期望命中 knowledge_id」样例，每次调整后回归验证，防止检索质量退化
- 进阶可选：SQLite FTS5 关键词召回 + 向量召回的混合检索，提升条款号等精确查询的命中率

### L3 Agent 应用层

- **LLM + Function Calling**：Agent 将 L2 检索作为工具调用，先检索、后作答，回答内容约束在命中的知识切片内
- **强制溯源**：回答附 `doc_name / version / clause_position / original_text`，每条结论可回查原文出处
- **金融场景可靠性**：无命中或相似度过低时明确拒答并提示，禁止编造条款与费率

### 持续运维

- 文档版本演进：新版本 Excel 行入库后，旧版本行改置 `superseded` 即完成切换，历史版本可追溯（表结构已就绪）
- 向量模型或参数调整后，重跑全量构建即可，主库无损
