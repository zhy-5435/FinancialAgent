"""会话记忆层（src/memory）：多轮持久化存档 + 上下文梯度压缩引擎

两层独立、互不依赖，可分别单测：
    store/    持久化存储层：原始完整消息序列（审计、脱敏存档）+ 压缩元数据日志
    context/  上下文构建引擎：每次调用模型前生成精简推理上下文视图（4 档梯度压缩）
memory_manager.py 为门面，串起 store + context 供 L3 graph/api 调用。
"""
