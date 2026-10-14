"""
Comprehensive Integration Test for Refined Knowledge Extraction & Graph Ingestion
验证吸收 ref1 & ref2 优点后的四大核心能力：
1. 【纯代码阅读场景（无 Git Diff）】：通过 AST 符号与深度走读闭环提炼业务知识
2. 【试错轨迹折叠（Trajectory Folding）】：自动剔除被证伪的错误推测（ruled_out_hypotheses），沉淀最终实证知识
3. 【反通用常识测试（Counterfactual Specificity Test）】：一票否决脱离业务仓库的通用编程废话（如 HashMap 并发加锁）
4. 【证据账本与知识超节点（Evidence Ledger & Knowledge Hypernode）】：多层级证据链绑定与图谱写入
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.enhanced_deepseek_extractor import EnhancedDeepSeekKnowledgeEngine
from src.graph_store import InMemoryGraphStore

# ─── 构造 4 个极具代表性的进阶测试用例 ─────────────────────────────────────

ENHANCED_TEST_CASES = [
    {
        "id": "CASE_READONLY_ARCHITECTURE",
        "name": "【能力验证1：纯代码阅读与架构走读（无 Git Diff）】",
        "developer": "alice",
        "candidate_symbols": [
            {
                "symbol_id": "sym_transfer_01",
                "fqn": "com.company.pay.service.AccountTransferService#executeTransfer",
                "endpoint": "POST /api/v2/account/transfer",
                "file_path": "src/main/java/com/company/pay/service/AccountTransferService.java",
                "action_type": "INSPECTED_IN_READ"
            },
            {
                "symbol_id": "sym_balance_02",
                "fqn": "com.company.pay.dao.UserBalanceMapper#updateBalanceForUpdate",
                "file_path": "src/main/java/com/company/pay/dao/UserBalanceMapper.java",
                "action_type": "INSPECTED_IN_READ"
            }
        ],
        "files_changed": [],
        "git_diff": "",
        "test_execution_result": "",
        "turns": [
            {
                "role": "user",
                "content": "我正在梳理转账模块 AccountTransferService#executeTransfer 的并发安全机制，请帮我分析下两个账户相互转账时会不会死锁？"
            },
            {
                "role": "assistant",
                "content": "阅读了 AccountTransferService 与 UserBalanceMapper 源码：当用户 A 向用户 B 转账，同时用户 B 向用户 A 转账时，若按入参顺序分别获取 A 和 B 的行级锁（select ... for update），会发生典型的交叉死锁（Deadlock）！\n"
                           "当前业务代码里通过强制按 `accountId.compareTo(targetId)` 排序后再顺序加锁的机制来打破循环等待条件。业务规定：任何涉及双账户同时锁定的操作，必须先锁 ID 较小的账户，再锁 ID 较大的账户！"
            },
            {
                "role": "user",
                "content": "非常清晰！原来转账接口 /api/v2/account/transfer 的隐性死锁防护规则是按 AccountId 升序加锁。我记录一下。"
            }
        ]
    },
    {
        "id": "CASE_TRAJECTORY_FOLDING",
        "name": "【能力验证2：试错轨迹折叠（Trajectory Folding & 排除弯路）】",
        "developer": "bob",
        "candidate_symbols": [
            {
                "symbol_id": "sym_order_submit",
                "fqn": "com.company.order.OrderSubmitService#submitOrder",
                "endpoint": "POST /api/order/submit",
                "file_path": "src/main/java/com/company/order/OrderSubmitService.java",
                "action_type": "MODIFIED_IN_DIFF"
            },
            {
                "symbol_id": "sym_risk_client",
                "fqn": "com.company.risk.client.RiskServiceClient#checkRisk",
                "file_path": "src/main/java/com/company/risk/client/RiskServiceClient.java",
                "action_type": "INSPECTED_IN_READ"
            }
        ],
        "files_changed": ["src/main/java/com/company/order/OrderSubmitService.java"],
        "git_diff": """diff --git a/src/main/java/com/company/order/OrderSubmitService.java b/src/main/java/com/company/order/OrderSubmitService.java
@@ -50,2 +50,3 @@ public class OrderSubmitService {
-        RiskResponse resp = riskClient.checkRisk(req);
+        // 下游风控服务未配超时会导致线程池耗尽，必须显式设置 300ms 超时和降级兜底
+        RiskResponse resp = riskClient.checkRisk(req, Duration.ofMillis(300));
""",
        "test_execution_result": "[INFO] Running com.company.order.OrderSubmitServiceTest\n[INFO] Tests run: 5, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 1.25 s\n[INFO] BUILD SUCCESS",
        "turns": [
            {
                "role": "user",
                "content": "订单提交接口 /api/order/submit 压测时偶发大面积超时，我怀疑是数据库订单表慢查询导致的，你帮我加个联合索引试试？"
            },
            {
                "role": "assistant",
                "content": "我分析了慢查询日志，并为 (user_id, status) 添加了索引后再次压测，发现超时问题依然严重！因此【排除数据库索引导致超时的假设】。"
            },
            {
                "role": "user",
                "content": "那还有什么可能导致偶发几秒的停顿？"
            },
            {
                "role": "assistant",
                "content": "通过线程 Dump 发现，大量工作线程阻塞在 `riskClient.checkRisk(req)` 处！这是因为调用下游风控微服务使用了默认 HTTP 连接池，且没有设置 SocketTimeout。当下游风控网络抖动时，导致本地 HTTP 线程耗尽，进而引发全局接口超时！"
            },
            {
                "role": "user",
                "content": "原来是风控客户端没有配置超时！我在 OrderSubmitService 中添加了 300ms 显式超时与降级逻辑，单测已全部通过。"
            }
        ]
    },
    {
        "id": "CASE_GENERIC_COUNTERFACTUAL",
        "name": "【能力验证3：反通用常识测试（Counterfactual Specificity 一票否决）】",
        "developer": "charlie",
        "candidate_symbols": [],
        "files_changed": ["src/main/java/com/company/util/CacheMap.java"],
        "git_diff": """diff --git a/src/main/java/com/company/util/CacheMap.java b/src/main/java/com/company/util/CacheMap.java
@@ -10,3 +10,3 @@ public class CacheMap {
-    private Map<String, Object> map = new HashMap<>();
+    private Map<String, Object> map = new ConcurrentHashMap<>();
""",
        "test_execution_result": "BUILD SUCCESS",
        "turns": [
            {
                "role": "user",
                "content": "在 Java 多线程环境下，多个线程同时读写普通 HashMap 会有什么风险？怎么改？"
            },
            {
                "role": "assistant",
                "content": "在多线程环境下使用 HashMap 会引发线程安全问题，早期 JDK 1.7 可能在扩容时产生死循环导致 CPU 100%，JDK 8 后可能导致数据丢失或覆盖。建议使用 ConcurrentHashMap 或对写操作加 synchronized / ReentrantLock。"
            },
            {
                "role": "user",
                "content": "好的，我已经改成了 ConcurrentHashMap。"
            }
        ]
    },
    {
        "id": "CASE_TRIVIAL_NOISE",
        "name": "【能力验证4：琐碎过程与简单编译错误（一票否决）】",
        "developer": "david",
        "candidate_symbols": [],
        "files_changed": ["src/main/java/com/company/model/User.java"],
        "git_diff": """diff --git a/src/main/java/com/company/model/User.java b/src/main/java/com/company/model/User.java
@@ -15,1 +15,1 @@ public class User {
-    private String emial;
+    private String email;
""",
        "test_execution_result": "BUILD SUCCESS",
        "turns": [
            {
                "role": "user",
                "content": "User 类里的 emial 变量拼写错了，帮我改成 email，并重新生成一下 getter/setter。"
            },
            {
                "role": "assistant",
                "content": "已修正拼写为 email 并生成了 getEmail() 和 setEmail() 方法。"
            }
        ]
    }
]


def run_enhanced_evaluation():
    print("=" * 80)
    print("🚀 运行 KnowGraph 吸收 ref1 & ref2 优点后的增强版系统评估")
    print("=" * 80)

    engine = EnhancedDeepSeekKnowledgeEngine()
    store = InMemoryGraphStore()

    for tc in ENHANCED_TEST_CASES:
        print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"📌 测试用例: {tc['name']}")
        print(f"   开发者: {tc['developer']} | 候选代码符号数: {len(tc['candidate_symbols'])}")
        print(f"   代码变更状态: {'有修改 (Git Diff)' if tc['git_diff'] else '纯阅读/定位 (无 Diff)'}")
        print(f"   单测验证证据: {'有单元测试转绿证据' if tc['test_execution_result'] else '无单测输出'}")

        analysis = engine.analyze_session(
            developer=tc["developer"],
            turns=tc["turns"],
            candidate_code_symbols=tc["candidate_symbols"],
            files_changed=tc["files_changed"],
            git_diff=tc["git_diff"],
            test_execution_result=tc["test_execution_result"]
        )

        is_valuable = analysis.get("is_valuable", False)
        confidence = analysis.get("confidence", 0.0)
        gatekeeper = analysis.get("gatekeeper_check", {})
        k_type = analysis.get("knowledge_type", "None")

        status_text = "🟢 【一票否决门控通过：判定为高价值团队资产】" if is_valuable else "🔴 【一票否决门控拦截：判定为通用/琐碎/未闭环噪声】"
        print(f"\n{status_text}")
        print(f"   置信度: {confidence} | 知识本体类型: {k_type}")
        print(f"   门控检查详情:")
        print(f"     - 反通用常识检验 (Counterfactual): {gatekeeper.get('passed_counterfactual_test')}")
        print(f"     - 闭环验证检验 (Closure):        {gatekeeper.get('has_verified_closure')}")
        print(f"     - 非琐碎/非表层 (Non-trivial):   {gatekeeper.get('is_non_trivial')}")
        print(f"     - 代码实体锚定 (Grounded):       {gatekeeper.get('has_grounded_anchor')}")
        print(f"     - 门控判决依据: {gatekeeper.get('verdict_reason')}")

        if is_valuable:
            print(f"\n   💎 提炼标题: {analysis.get('title')}")
            print(f"   📝 核心结论: {analysis.get('summary')}")
            print(f"   🔗 关联代码符号: {analysis.get('associated_symbols')}")
            
            ruled_out = analysis.get("ruled_out_hypotheses", [])
            if ruled_out:
                print(f"   🚫 试错排除项 (Ruled-out): {ruled_out}")
            
            print(f"   ⚠️ 避坑与行动指南: {analysis.get('pitfalls_and_actions')}")
            print(f"   🏷️ 业务标签: {analysis.get('tags')}")
            print(f"   📜 证据置信等级: {analysis.get('evidence_level')}")

    print(f"\n{'=' * 80}")
    print("✅ 增强版知识引擎实测完成！")


if __name__ == "__main__":
    run_enhanced_evaluation()
