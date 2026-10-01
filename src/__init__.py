"""L1 金融知识库项目源码包

分层结构：
    src/data/    L1 数据层（SQLite 权威主库 + Milvus 向量索引）
    src/search/  L2 检索服务层（FastAPI REST 接口）
    src/agent/   L3 Agent 应用层（LangChain + LangGraph 问答编排）
"""
