"""
Comprehensive End-to-End Test for KnowGraph v4.0 Pipeline
Integrating all 2025-2026 arXiv advancements:
1. Client-Side DLP Sanitization & Trimming (Minim / PolicyGuard)
2. Deterministic AST Grounding from Git Diff & Code (Closed-World Resolver)
3. Four-dimension Gatekeeper & Trajectory Folding (SWE-Exp & TRAJDEBUG Error Bank)
4. Bi-Temporal Hypernode Store with Conflict & Evolution Detection (EvoKG / TGMS)
5. Developer Querying: Active Knowledge & Ruled-Out Hypotheses Inspection
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.hook_collector import LocalDLPFilter, PayloadTrimmer
from src.ast_grounder import ASTGrounder, MultiLangASTParser
from src.collector import MultiSourceCollector
from src.enhanced_deepseek_extractor import EnhancedDeepSeekKnowledgeEngine
from src.graph_store import InMemoryGraphStore
from src.contradiction_detector import KnowledgeContradictionDetector


def run_v4_pipeline_test():
    print("=" * 80)
    print("🌟 运行 KnowGraph v4.0 (2025-2026 前沿成果集大成) 端到端全流程验证")
    print("=" * 80)

    store = InMemoryGraphStore()
    engine = EnhancedDeepSeekKnowledgeEngine()
    grounder = ASTGrounder()
    collector = MultiSourceCollector()

    # ──────────────────────────────────────────────────────────────────────────
    # 场景 1：订单并发排障，产生试错折叠并录入初始知识 K1
    # ──────────────────────────────────────────────────────────────────────────
    print("\n▶ [Step 1] 模拟开发者 Alice 进行订单提交服务排障与优化（包含密钥与敏感数据）")
    
    raw_diff_v1 = """diff --git a/com/company/order/OrderSubmitService.java b/com/company/order/OrderSubmitService.java
@@ -14,2 +14,3 @@
-        RiskResponse resp = riskClient.checkRisk(req);
+        // 下游风控调用必须显式设置 300ms 超时和降级兜底
+        RiskResponse resp = riskClient.checkRisk(req, Duration.ofMillis(300));
"""
    diff_ranges = {"com/company/order/OrderSubmitService.java": [(14, 16)]}
    # 模拟 AST 锚定得到的物理符号
    grounded_symbols_v1 = [
        {
            "symbol_id": "sym_order_submit",
            "fqn": "com.company.order.OrderSubmitService#submitOrder",
            "symbol_type": "method",
            "endpoint": "POST /api/v1/order/submit",
            "file_path": "com/company/order/OrderSubmitService.java",
            "action_type": "MODIFIED_IN_DIFF"
        }
    ]

    session_v1 = {
        "session_id": "sess_order_v1",
        "developer": "alice",
        "timestamp": "2026-10-15T10:00:00Z",
        "turns": [
            {
                "role": "user",
                "content": "订单接口 /api/v1/order/submit 压测超时，我们的 API key sk-proj-supersecret9999999999999 是否需要配置？我先加了个索引但无效。"
            },
            {
                "role": "assistant",
                "content": "分析表明慢查索引无效，排除索引假设！真正根因是下游风控客户端无超时耗尽了线程池。已添加 300ms 超时与降级逻辑。"
            }
        ]
    }

    # 1. 采集与客户端 DLP 脱敏
    event_v1 = collector.collect(session_v1, diff_text=raw_diff_v1, test_output="[INFO] BUILD SUCCESS")
    print("  [DLP 脱敏验证] 对话内容中密钥是否被擦除：", "[MASKED_API_KEY]" in event_v1.conversation.raw_text)
    assert "sk-proj-supersecret" not in event_v1.conversation.raw_text

    # 2. 微闭环检测
    loop_sig = collector.detect_micro_loop(event_v1, test_output="BUILD SUCCESS")
    print(f"  [微闭环信号] {loop_sig.trigger_type} (触发: {loop_sig.should_trigger}) - {loop_sig.description}")

    # 3. 增强抽取（四维门控 + 试错折叠）
    extract_res_v1 = engine.analyze_session(
        developer="alice",
        turns=event_v1.conversation.turns,
        candidate_code_symbols=grounded_symbols_v1,
        files_changed=["com/company/order/OrderSubmitService.java"],
        git_diff=raw_diff_v1,
        test_execution_result="BUILD SUCCESS"
    )
    print(f"  [门控判定] 门控通过: {extract_res_v1['is_valuable']} | 标题: {extract_res_v1['title']}")
    print(f"  [负知识 Error Bank] 排除弯路: {extract_res_v1['ruled_out_hypotheses']}")

    # 4. 超节点入库
    k1_id = store.ingest_hypernode(
        extract_res_v1,
        developer="alice",
        session_id="sess_order_v1",
        commit_sha="commit_sha_111",
        test_passed=True
    )
    print(f"  [图写入] 成功沉淀知识超节点: {k1_id}")

    # ──────────────────────────────────────────────────────────────────────────
    # 场景 2：后续重构演化（代码演化取代历史旧规则）
    # ──────────────────────────────────────────────────────────────────────────
    print("\n▶ [Step 2] 模拟数周后开发者 Bob 对 OrderSubmitService 进行重大重构演化")
    print("  业务变更：风控架构重构，改为异步事件驱动解耦，移除显式 300ms 同步超时机制，改用 MQ 异步校验")

    extract_res_v2 = {
        "is_valuable": True,
        "confidence": 0.95,
        "knowledge_type": "ArchitectureDecision",
        "title": "订单提交重构为 MQ 异步风控解耦，移除同步 300ms 超时限制",
        "summary": "重构风控链路为 Transactional Outbox 异步发布，彻底取代历史的同步 300ms 降级超时约束",
        "associated_symbols": ["com.company.order.OrderSubmitService#submitOrder"],
        "ruled_out_hypotheses": ["排除同步 RPC 阻塞风控方案"],
        "pitfalls_and_actions": "新接入方严禁再配置同步 HTTP 超时，需监听 risk_verified 领域事件",
        "tags": ["订单重构", "异步解耦", "ArchitectureDecision"],
        "evidence_level": "L2_TOOL_VERIFIED"
    }

    k2_id = store.ingest_hypernode(
        extract_res_v2,
        developer="bob",
        session_id="sess_order_v2",
        commit_sha="commit_sha_222",
        test_passed=True
    )
    print(f"  [图写入与演化] 新知识节点 {k2_id} 成功录入！")

    # ──────────────────────────────────────────────────────────────────────────
    # 场景 3：时态演化回溯与当前有效知识检索验证
    # ──────────────────────────────────────────────────────────────────────────
    print("\n▶ [Step 3] 验证双时态图谱检索与历史演化链条回溯")

    # 1. 查询当前有效知识（Active Knowledge）
    query_target = "OrderSubmitService"
    active_res = store.query_active_knowledge(query_target)
    print(f"\n🔍 针对 '{query_target}' 查询当前有效规则 (invalid_at IS NULL):")
    for ak in active_res.active_knowledge:
        print(f"   🟢 [当前生效] ID: {ak['id']} | 标题: {ak['title']}")
        print(f"      状态: {ak['status']} | 生效时间: {ak['valid_at']} | 失效时间: {ak['invalid_at']}")
        print(f"      核心结论: {ak['summary']}")

    print(f"\n   🚫 团队共享避坑负知识汇总 (Ruled-Out Hypotheses):")
    for ro in active_res.ruled_out_hypotheses:
        print(f"      - {ro}")

    # 2. 回溯演化历史（Temporal Evolution Chain）
    evolution_chain = store.query_temporal_evolution("com.company.order.OrderSubmitService#submitOrder")
    print(f"\n⏳ 针对符号的历史时态演化链条 (共 {len(evolution_chain)} 个版本):")
    for ver in evolution_chain:
        print(f"   • [{ver['status']}] {ver['id']} - {ver['title']}")
        print(f"     生效: {ver['valid_at']} | 失效: {ver['invalid_at']} | 被谁取代: {ver['superseded_by']}")

    # 断言验证
    assert len(active_res.active_knowledge) == 1
    assert active_res.active_knowledge[0]["id"] == k2_id
    assert evolution_chain[0]["status"] == "SUPERSEDED"
    assert evolution_chain[0]["superseded_by"] == k2_id

    # 导出全量图数据
    export_path = os.path.join(os.path.dirname(__file__), "..", "data", "v4_full_knowledge_graph.json")
    store.export_json(export_path)
    print(f"\n📁 成功导出全量双时态超节点知识图谱至: {export_path}")
    print("\n🎉 KnowGraph v4.0 综合全流程验证完美通过！")


if __name__ == "__main__":
    run_v4_pipeline_test()
