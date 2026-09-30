from src.sqlite_client import sqlite_client
from src.milvus_client import milvus_store

def build_full_pipeline():
    print("=" * 60)
    print("🚀 开始执行 L1 知识库全量构建流程")
    print("=" * 60)

    # Step1: Excel导入SQLite权威主库
    print("\n[Step 1/4] 导入Excel数据到SQLite主库...")
    n_docs, n_chunks = sqlite_client.load_excel_to_db()
    print(f"✅ 导入完成：doc_version {n_docs} 条，knowledge_chunk {n_chunks} 条")

    # Step2: 校验原始文档路径（Excel的file_path -> data/raw）
    print("\n[Step 2/4] 校验原始文档路径...")
    report = sqlite_client.verify_doc_files()
    ok = [r for r in report if r["result"] == "ok"]
    fuzzy = [r for r in report if r["result"] == "fuzzy"]
    missing = [r for r in report if r["result"] == "missing"]
    print(f"✅ 校验完成：直接匹配 {len(ok)} 条，模糊匹配 {len(fuzzy)} 条，缺失 {len(missing)} 条")
    for r in fuzzy:
        print(f"   ⚠️ [{r['doc_version_id']}] 路径经空白/中点规范化后匹配：{r['matched_path']}")
    for r in missing:
        print(f"   ❌ [{r['doc_version_id']}] 未找到原始文档：{r['file_path']}")

    # Step3: 同步向量到Milvus
    print("\n[Step 3/4] 生成向量并同步到Milvus Lite...")
    milvus_store.sync_from_sqlite()

    # Step4: 功能验证
    print("\n[Step 4/4] 检索功能验证...")
    test_queries = [
        "A股股票涨跌幅限制是多少",
        "理财产品申购费率",
        "出差住宿报销标准",
        "投资者适当性如何分类管理"
    ]

    for q in test_queries:
        print(f"\n🔍 测试查询：{q}")
        results = milvus_store.search(q)
        if results:
            top1 = results[0]
            print(f"   匹配结果：{top1['content']}")
            print(f"   类型：{top1['chunk_type']} | 关键词：{top1['keywords']}")
            source = f"{top1['doc_name']} {top1['version']}"
            if top1["clause_position"]:
                source += f" {top1['clause_position']}"
            print(f"   来源：{source} | 相似度：{top1['similarity']}")
        else:
            print("   无匹配结果")

    print("\n" + "=" * 60)
    print("🎉 L1知识库构建完成")
    print("=" * 60)

if __name__ == "__main__":
    build_full_pipeline()
