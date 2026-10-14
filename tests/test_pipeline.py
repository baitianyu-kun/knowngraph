"""
End-to-end pipeline test: 模拟完整的 KnowGraph Step-1 流程

流程：
  Mock 会话数据
    → MultiSourceCollector（多源采集）
    → KnowledgeValueFilter（价值过滤）
    → KnowledgeExtractor（知识抽取）
    → InMemoryGraphStore（图存储）
    → 查询验证（模拟开发者B查询）

运行：
  python -m tests.test_pipeline
"""

import sys
import os
import json

# 保证从项目根目录运行
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.mock_data import ALL_SESSIONS
from src.collector import MultiSourceCollector
from src.knowledge_filter import KnowledgeValueFilter
from src.knowledge_extractor import KnowledgeExtractor
from src.graph_store import InMemoryGraphStore


def run_pipeline():
    collector = MultiSourceCollector()
    filt = KnowledgeValueFilter()
    extractor = KnowledgeExtractor()
    store = InMemoryGraphStore()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║          KnowGraph Step-1 End-to-End Pipeline Test       ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    filtered_count = 0
    ingested_count = 0

    for session_dict, diff_text, _ in ALL_SESSIONS:
        print(f"▶ Processing session: {session_dict['session_id']} (developer: {session_dict['developer']})")

        # Step 1: Multi-source collection
        event = collector.collect(session_dict, diff_text=diff_text)
        print(f"  [Collect] turns={len(event.conversation.turns)}, "
              f"files_changed={event.git_context.files_changed}")

        # Step 2: Value filter
        filter_result = filt.filter(event)
        status_icon = "✅" if filter_result.is_valuable else "❌"
        print(f"  [Filter] {status_icon} valuable={filter_result.is_valuable}, "
              f"confidence={filter_result.confidence:.2f}, keyword_score={filter_result.keyword_score}")
        print(f"           reason: {filter_result.reason}")

        if not filter_result.is_valuable:
            filtered_count += 1
            print()
            continue

        # Step 3: Knowledge extraction
        record = extractor.extract(event, confidence=filter_result.confidence)
        if record is None:
            print("  [Extract] ⚠️  Extraction returned None, skipping")
            print()
            continue

        print(f"  [Extract] entities={len(record.entities)}, relations={len(record.relations)}, "
              f"tags={record.tags}")
        print(f"           summary: {record.summary[:80]}...")

        # Step 4: Store in graph
        store.ingest(record)
        ingested_count += 1
        print()

    # ── Summary ──────────────────────────────────────────────────────────
    print("─" * 60)
    stats = store.stats()
    print(f"📊 Pipeline Summary:")
    print(f"   Total sessions processed : {len(ALL_SESSIONS)}")
    print(f"   Filtered (low value)     : {filtered_count}")
    print(f"   Ingested to graph        : {ingested_count}")
    print(f"   Graph nodes              : {stats['total_nodes']}")
    print(f"   Graph edges              : {stats['total_edges']}")
    print(f"   Node type breakdown      : {stats['node_types']}")
    print()

    # ── Scenario: Developer B asks about /api/order/create ──────────────
    print("═" * 60)
    print("🎬 Scenario: 开发者 Bob 接入 /api/order/create，向 Agent 查询注意事项")
    print("═" * 60)
    query = "/api/order/create"
    result = store.query_by_entity(query, depth=2)
    print(f"\n🔍 Query: '{query}'")
    if result.summaries:
        print("\n💡 从知识图谱检索到的历史知识：")
        for i, s in enumerate(result.summaries, 1):
            print(f"   {i}. {s}")
    else:
        print("   （无相关历史知识）")

    print(f"\n   关联节点 ({len(result.nodes)})：", [n["id"] for n in result.nodes[:8]])

    # ── Scenario: Query by tag ──────────────────────────────────────────
    print()
    print("🎬 Scenario: 开发者查询所有「并发」相关知识")
    result2 = store.query_by_tag("并发")
    print(f"\n🔍 Query tag: '并发'")
    if result2.summaries:
        for i, s in enumerate(result2.summaries, 1):
            print(f"   {i}. {s}")
    else:
        print("   （无相关知识）")

    # ── Scenario: Query by file ──────────────────────────────────────────
    print()
    print("🎬 Scenario: Code Review 时查看 OrderService.java 的历史知识背景")
    result3 = store.query_by_file("OrderService.java")
    print(f"\n🔍 Query file: 'OrderService.java'")
    if result3.summaries:
        for i, s in enumerate(result3.summaries, 1):
            print(f"   {i}. {s}")
    else:
        print("   （无相关知识）")

    # Export graph
    export_path = os.path.join(os.path.dirname(__file__), "..", "data", "graph_export.json")
    store.export_json(export_path)
    print(f"\n📁 Graph exported to: {export_path}")
    print("\n✅ Pipeline test completed successfully.")


if __name__ == "__main__":
    run_pipeline()
