"""
End-to-End Real LLM Pipeline Test with DeepSeek API
验证全流程：
1. 多源信息采集 (Agent对话 + Git Diff + 变更文件)
2. 价值识别与判断 (有价值沉淀 vs 无价值噪声过滤)
3. 结构化知识抽取 (实体、关系、避坑指南、总结)
4. 图存储入库 (实体节点、关系边、会话元数据)
5. 检索验证 (模拟开发者 B 询问接口注意事项，准确命中之前沉淀的知识)
"""

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.collector import MultiSourceCollector
from src.deepseek_extractor import DeepSeekKnowledgeEngine
from src.knowledge_extractor import KnowledgeRecord, Entity, Relation
from src.graph_store import InMemoryGraphStore

# ── 构造 4 个多源测试会话（涵盖高价值沉淀和典型噪声过滤） ───────────────

TEST_SESSIONS = [
    {
        "name": "【场景1：高价值】订单创建接口并发重复下单与分布式锁方案",
        "developer": "alice",
        "session_id": "sess_order_concurrency_001",
        "files_changed": ["src/main/java/com/example/service/OrderService.java"],
        "git_diff": """diff --git a/src/main/java/com/example/service/OrderService.java b/src/main/java/com/example/service/OrderService.java
index 3a7f123..9b2c456 100644
--- a/src/main/java/com/example/service/OrderService.java
+++ b/src/main/java/com/example/service/OrderService.java
@@ -45,6 +45,14 @@ public class OrderService {
     public Order createOrder(Long userId, Long productId, int quantity) {
+        RLock lock = redissonClient.getLock("order:create:" + userId + ":" + productId);
+        try {
+            lock.lock(5, TimeUnit.SECONDS);
         Order order = new Order(userId, productId, quantity);
         orderRepository.save(order);
+        } finally {
+            lock.unlock();
+        }
         return order;
     }""",
        "turns": [
            {"role": "user", "content": "我在压测接口 /api/order/create 时发现在高并发场景下会出现重复订单，怎么排查和解决？"},
            {"role": "assistant", "content": "/api/order/create 在高并发下由于并发读写存在竞态条件。建议：1. 幂等控制；2. 针对 userId + productId 维度加 Redisson 分布式锁，TTL设为5秒；3. 数据库唯一索引兜底。"},
            {"role": "user", "content": "好的，我已经修改了 OrderService.createOrder，增加了锁。"},
            {"role": "assistant", "content": "请注意锁粒度必须限定到单个用户与商品，不可加全局锁。同时提醒团队后续对接该接口需注意并发防重规则。"}
        ]
    },
    {
        "name": "【场景2：高价值】支付网关对接微信V3接口证书序列号隐性踩坑",
        "developer": "bob",
        "session_id": "sess_pay_wechat_v3_002",
        "files_changed": ["src/main/java/com/example/pay/WechatPayClient.java"],
        "git_diff": """diff --git a/src/main/java/com/example/pay/WechatPayClient.java b/src/main/java/com/example/pay/WechatPayClient.java
index 8f1e2a3..4b5c6d7 100644
--- a/src/main/java/com/example/pay/WechatPayClient.java
+++ b/src/main/java/com/example/pay/WechatPayClient.java
@@ -28,6 +28,8 @@ public class WechatPayClient {
     public PayResponse requestPay(PayRequest req) {
+        // 微信支付V3规范要求请求Header中必须携带商户API证书序列号 Wechatpay-Serial
+        httpPost.setHeader("Wechatpay-Serial", merchantCertSerialNo);
         return httpClient.execute(httpPost);
     }""",
        "turns": [
            {"role": "user", "content": "对接微信支付统一预下单接口 /api/pay/wechat/prepay 一直报 400 Bad Request，错误码 SIGN_ERROR，但本地验签算出来的串是一致的，为什么？"},
            {"role": "assistant", "content": "微信支付 V3 接口要求非常严格：1. 请求 Header 中必须携带 `Wechatpay-Serial`（商户API证书序列号）；2. Authorization 头部中的签名串末尾必须包含换行符；若缺失序列号，微信服务器将直接报签名错误或非法请求。"},
            {"role": "user", "content": "原来是缺少了 Wechatpay-Serial 请求头！补上之后果然返回 200 预下单成功了。我提交了 WechatPayClient.java。"},
            {"role": "assistant", "content": "太好了！后续任何调用微信 V3 接口的地方都必须遵循此 Header 规范，防止再次踩坑。"}
        ]
    },
    {
        "name": "【场景3：低价值/噪声】单纯代码格式化与变量重命名",
        "developer": "charlie",
        "session_id": "sess_format_clean_003",
        "files_changed": ["src/main/java/com/example/util/StringUtils.java"],
        "git_diff": """diff --git a/src/main/java/com/example/util/StringUtils.java b/src/main/java/com/example/util/StringUtils.java
index 1111111..2222222 100644
--- a/src/main/java/com/example/util/StringUtils.java
+++ b/src/main/java/com/example/util/StringUtils.java
@@ -10,3 +10,3 @@ public class StringUtils {
-    public static boolean isNull(String s) {
-        return s == null || s.length() == 0;
+    public static boolean isEmpty(String str) {
+        return str == null || str.isEmpty();
     }""",
        "turns": [
            {"role": "user", "content": "帮我把 StringUtils 里的 isNull 方法重命名为 isEmpty，并优化下缩进。"},
            {"role": "assistant", "content": "已将 isNull 重命名为 isEmpty，并使用 str.isEmpty() 替代 length() == 0。"}
        ]
    },
    {
        "name": "【场景4：低价值/噪声】简单询问语法，无代码变更",
        "developer": "david",
        "session_id": "sess_syntax_qa_004",
        "files_changed": [],
        "git_diff": "",
        "turns": [
            {"role": "user", "content": "Java 8 的 Stream 怎么把 List 转成 Map？"},
            {"role": "assistant", "content": "使用 `list.stream().collect(Collectors.toMap(Item::getId, Item::getName))` 即可。"},
            {"role": "user", "content": "收到，谢谢！"}
        ]
    }
]


def run_real_llm_test():
    print("=" * 70)
    print("🚀 启动 KnowGraph 第一步：真实 LLM 多源信息识别与抽取端到端验证")
    print("=" * 70)

    engine = DeepSeekKnowledgeEngine()
    store = InMemoryGraphStore()

    results_summary = []

    for item in TEST_SESSIONS:
        print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"📥 正在处理: {item['name']}")
        print(f"   会话ID: {item['session_id']} | 提交者: {item['developer']}")
        print(f"   变更文件: {item['files_changed']}")
        
        # 调用真实 LLM 判断与提取
        analysis = engine.analyze_session(
            developer=item['developer'],
            turns=item['turns'],
            files_changed=item['files_changed'],
            git_diff=item['git_diff']
        )

        is_valuable = analysis.get("is_valuable", False)
        confidence = analysis.get("confidence", 0.0)
        filter_reason = analysis.get("filter_reason", "")
        knowledge_type = analysis.get("knowledge_type", "None")

        status_tag = "✅ 【识别为高价值知识并沉淀】" if is_valuable else "🚫 【识别为无价值/噪声并过滤】"
        print(f"\n{status_tag}")
        print(f"   置信度: {confidence} | 类型: {knowledge_type}")
        print(f"   判断理由: {filter_reason}")

        if is_valuable:
            summary = analysis.get("summary", "")
            entities_raw = analysis.get("entities", [])
            relations_raw = analysis.get("relations", [])
            tags = analysis.get("tags", [])
            pitfalls = analysis.get("precautions_and_pitfalls", "")
            scenarios = analysis.get("applicable_scenarios", "")

            print(f"   💡 核心提炼: {summary}")
            print(f"   🏷️ 业务标签: {tags}")
            print(f"   📌 适用场景: {scenarios}")
            print(f"   ⚠️ 避坑指南: {pitfalls}")
            print(f"   🧩 抽取实体数: {len(entities_raw)} | 关系数: {len(relations_raw)}")

            # 转换为 KnowledgeRecord 并存入图数据库
            entities = [
                Entity(name=e["name"], entity_type=e.get("type", "Concept"), description=e.get("desc", ""))
                for e in entities_raw
            ]
            relations = [
                Relation(source=r["src"], relation_type=r.get("rel", "RELATED_TO"), target=r["dst"])
                for r in relations_raw
            ]

            record = KnowledgeRecord(
                session_id=item['session_id'],
                developer=item['developer'],
                timestamp="2026-10-15T00:15:00+08:00",
                summary=summary,
                entities=entities,
                relations=relations,
                tags=tags,
                files_changed=item['files_changed'],
                confidence=confidence,
            )
            store.ingest(record)

        results_summary.append({
            "name": item['name'],
            "is_valuable": is_valuable,
            "confidence": confidence,
            "filter_reason": filter_reason,
            "summary": analysis.get("summary", "")
        })

    # ── 知识检索联动验证（模拟开发者 B 询问） ─────────────────────────
    print(f"\n{'=' * 70}")
    print("🔍 模拟团队协同场景：开发者 B 对接接口，从图数据库检索历史沉淀")
    print("=" * 70)

    # 查询 1: 开发者 B 询问订单接口注意事项
    target_api = "/api/order/create"
    print(f"\n[Case 1] 开发者 B 正在接入 [{target_api}]，问 Agent：该接口有什么需要注意的事项？")
    res1 = store.query_by_entity(target_api, depth=2)
    if res1.summaries:
        print(f"🎯 图数据库成功命中 {len(res1.summaries)} 条沉淀知识：")
        for idx, s in enumerate(res1.summaries, 1):
            print(f"   [{idx}] {s}")
    else:
        print("❌ 未检索到知识")

    # 查询 2: 开发者 C 询问微信支付预下单注意事项
    target_api2 = "/api/pay/wechat/prepay"
    print(f"\n[Case 2] 开发者 C 正在接入 [{target_api2}] 或处理签名报错，问 Agent：微信支付V3有什么坑？")
    res2 = store.query_by_entity(target_api2, depth=2)
    if res2.summaries:
        print(f"🎯 图数据库成功命中 {len(res2.summaries)} 条沉淀知识：")
        for idx, s in enumerate(res2.summaries, 1):
            print(f"   [{idx}] {s}")
    else:
        # 如果实体名字可能匹配的是客户端方法
        res2_fuzzy = store.query_by_tag("微信支付")
        if res2_fuzzy.summaries:
            print(f"🎯 标签检索命中：")
            for idx, s in enumerate(res2_fuzzy.summaries, 1):
                print(f"   [{idx}] {s}")

    # 查询 3: 通过标签「分布式锁」检索
    print(f"\n[Case 3] 架构师全局查询标签 [分布式锁] 相关的全团队知识积累：")
    res3 = store.query_by_tag("分布式锁")
    for idx, s in enumerate(res3.summaries, 1):
        print(f"   [{idx}] {s}")

    # 导出图结构
    export_path = "data/real_knowledge_graph.json"
    store.export_json(export_path)
    print(f"\n💾 最终知识图谱结构已导出至: {export_path}")
    print(f"📊 图谱统计信息: {json.dumps(store.stats(), ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    run_real_llm_test()
