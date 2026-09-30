"""L3 Agent 冒烟测试：验证检索工具、阈值过滤与溯源作答/拒答

运行前提：L2 检索服务已启动（python -m uvicorn src.api:app）
运行方式（项目根目录执行）：python test_agent.py
"""
import sys

from src.agent.graph import ask
from src.agent.tools import search_knowledge


def run_agent_test():
    # 1. 独立验证 L2 检索工具链路
    print("===== 工具链路验证：search_knowledge =====")
    hits = search_knowledge.invoke({"query": "科创板上市前5日涨跌幅", "top_k": 2})
    for h in hits:
        print(f"命中 {h['knowledge_id']} | {h['doc_name']} | 相似度 {h['similarity']}")

    # 2. 端到端验证：可答问题（应带溯源）与超纲问题（应拒答）
    test_questions = [
        "出差住宿报销标准是多少",
        "货币基金的风险等级是多少",
        "明天A股大盘会涨还是跌",  # 知识库无关问题，预期拒答
    ]
    for q in test_questions:
        print(f"\n===== 用户问题：{q} =====")
        print(ask(q))


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    run_agent_test()
