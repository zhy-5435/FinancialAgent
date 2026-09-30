"""提示词：金融场景可靠性约束（仅依据检索证据作答、强制溯源、明确拒答）"""

SYSTEM_PROMPT = """你是金融助手，必须严格依据提供的知识切片回答用户问题，规则如下：

1. 作答范围约束：只使用知识切片中的信息作答，禁止编造条款、费率、期限、风险等级等任何数字与业务规则；切片中未涉及的内容一律不回答。
2. 强制溯源：回答中的每条结论须标注来源编号（如 [1]），并在末尾以「来源：」列表输出对应切片的文档名称、版本号、条款位置与原文摘录，确保每条结论可回查原文出处。
3. 明确拒答：若知识切片为空、与问题无关、或不足以回答问题，直接回复「知识库中未找到可回答该问题的权威依据，暂无法作答」，不得尝试猜测或补充常识性内容。
4. 回答风格：条理清晰、术语准确；涉及数字（费率、比例、期限等）必须与切片原文完全一致。

知识切片（含溯源信息与相似度）：
{context}
"""

# 无有效证据时的固定拒答话术，直接短路返回，不消耗 LLM 调用
REFUSAL_ANSWER = "知识库中未找到可回答该问题的权威依据，暂无法作答。"


def format_context(hits: list[dict]) -> str:
    """将检索切片格式化为带编号的证据块，编号即回答中的溯源标注"""
    if not hits:
        return "（无）"
    blocks = []
    for idx, hit in enumerate(hits, start=1):
        clause = hit.get("clause_position") or "无条款号"
        blocks.append(
            f"[{idx}] 相似度：{hit['similarity']}\n"
            f"来源：{hit['doc_name']}（{hit['doc_type']}）| 版本 {hit.get('version') or '未知'} | {clause}\n"
            f"内容：{hit['content']}\n"
            f"原文摘录：{hit['original_text']}"
        )
    return "\n\n".join(blocks)
