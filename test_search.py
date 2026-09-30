from src.milvus_client import milvus_store

def run_search_test():
    test_queries = [
        "A股股票涨跌幅限制是多少",
        "理财产品申购费率",
        "出差住宿报销标准",
        "科创板上市前5日涨跌幅",
        "差旅报销需要哪些材料",
        "货币基金的风险等级是多少"
    ]
    for q in test_queries:
        print(f"\n===== 用户问题：{q} =====")
        results = milvus_store.search(q)
        if not results:
            print("无匹配知识点")
            continue
        for idx, item in enumerate(results):
            print(f"【Top{idx+1}】相似度：{item['similarity']:.4f}")
            print(f"内容：{item['content']}")
            print(f"切片类型：{item['chunk_type']} | 标题路径：{item['heading_path']}")
            print(f"关键词：{item['keywords']}")
            source = f"来源：{item['doc_name']}（{item['doc_type']}）| {item['doc_version_id']}"
            if item["clause_position"]:
                source += f" | {item['clause_position']}"
            print(source)
            print(f"原文片段：{item['original_text']}")

if __name__ == "__main__":
    run_search_test()
