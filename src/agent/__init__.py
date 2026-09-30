"""L3 Agent 应用层：基于 LangChain + LangGraph 的金融助手

数据流：用户问题 → L2 REST 检索工具 → 相似度过滤 → LLM 约束作答（强制溯源 / 低置信拒答）
"""
