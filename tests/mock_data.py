"""
Mock data for testing the KnowGraph pipeline without a real LLM API.
Simulates:
  1. A Claude Code agent conversation log (multi-turn)
  2. Git diff output
  3. The expected extracted knowledge triples
"""

# ---- Mock: Agent conversation log (as if exported from Claude Code hooks) ----
MOCK_CONVERSATION_SESSION_1 = {
    "session_id": "sess_abc123",
    "developer": "alice",
    "timestamp": "2026-10-10T10:00:00+08:00",
    "turns": [
        {
            "role": "user",
            "content": "我在测试接口 /api/order/create 时发现在高并发场景下会出现重复订单，怎么排查？"
        },
        {
            "role": "assistant",
            "content": (
                "这是一个经典的并发问题。/api/order/create 在高并发下可能存在竞态条件（race condition）。"
                "建议检查以下几点：\n"
                "1. 是否有幂等性保证（如唯一订单号校验）\n"
                "2. 是否需要对用户ID+商品ID加分布式锁（Redis SETNX 或 Redisson）\n"
                "3. 数据库层面是否有唯一索引兜底"
            )
        },
        {
            "role": "user",
            "content": "我看了代码，OrderService.createOrder() 里确实没有加锁，只有数据库的唯一约束。能帮我加上分布式锁吗？"
        },
        {
            "role": "assistant",
            "content": (
                "好的，可以用 Redisson 实现。对 userId + productId 组合作为锁的 key。\n"
                "关键代码：\n"
                "```java\n"
                "RLock lock = redissonClient.getLock(\"order:create:\" + userId + \":\" + productId);\n"
                "try {\n"
                "    lock.lock(5, TimeUnit.SECONDS);\n"
                "    // 执行创建订单逻辑\n"
                "} finally {\n"
                "    lock.unlock();\n"
                "}\n"
                "```\n"
                "这样可以防止同一用户对同一商品在 5 秒内重复下单。"
            )
        },
        {
            "role": "user",
            "content": "好的，我已经修改了 OrderService.java，提交了一个 commit。"
        },
        {
            "role": "assistant",
            "content": "修改完成后建议同时在接口文档中标注：此接口在并发场景下依赖分布式锁，锁粒度为 userId+productId，TTL 5秒。"
        }
    ]
}

MOCK_CONVERSATION_SESSION_2 = {
    "session_id": "sess_def456",
    "developer": "bob",
    "timestamp": "2026-10-11T14:00:00+08:00",
    "turns": [
        {
            "role": "user",
            "content": "UserService.getUserById 方法每次调用都会查数据库，有没有什么优化方案？"
        },
        {
            "role": "assistant",
            "content": (
                "可以加本地缓存（Caffeine）或分布式缓存（Redis）。\n"
                "由于 user 信息变化不频繁，建议：\n"
                "1. 读时先查 Redis，TTL 设置为 10 分钟\n"
                "2. 写时同步更新 Redis（Cache-Aside 模式）\n"
                "注意：用户注销、修改密码等操作需要主动删除缓存。"
            )
        },
        {
            "role": "user",
            "content": "如果 Redis 宕机怎么处理？"
        },
        {
            "role": "assistant",
            "content": (
                "需要做降级处理：在 Redis 不可用时，直接走数据库查询，同时告警。"
                "可以在 try-catch 中捕获 RedisException，fallback 到 DB。"
                "这样系统在 Redis 故障时仍然可用，只是性能降低。"
            )
        }
    ]
}

# ---- Mock: Git diff output ----
MOCK_GIT_DIFF_SESSION_1 = """
diff --git a/src/main/java/com/example/service/OrderService.java b/src/main/java/com/example/service/OrderService.java
index 3a7f123..9b2c456 100644
--- a/src/main/java/com/example/service/OrderService.java
+++ b/src/main/java/com/example/service/OrderService.java
@@ -45,6 +45,14 @@ public class OrderService {
     public Order createOrder(Long userId, Long productId, int quantity) {
+        // 加分布式锁防止并发重复下单
+        RLock lock = redissonClient.getLock("order:create:" + userId + ":" + productId);
+        try {
+            lock.lock(5, TimeUnit.SECONDS);
         Order order = new Order(userId, productId, quantity);
         orderRepository.save(order);
+        } finally {
+            lock.unlock();
+        }
         return order;
     }
"""

MOCK_GIT_DIFF_SESSION_2 = """
diff --git a/src/main/java/com/example/service/UserService.java b/src/main/java/com/example/service/UserService.java
index 1c2d3e4..5f6a7b8 100644
--- a/src/main/java/com/example/service/UserService.java
+++ b/src/main/java/com/example/service/UserService.java
@@ -30,5 +30,18 @@ public class UserService {
     public User getUserById(Long userId) {
-        return userRepository.findById(userId).orElseThrow();
+        String cacheKey = "user:" + userId;
+        try {
+            String cached = redisTemplate.opsForValue().get(cacheKey);
+            if (cached != null) {
+                return objectMapper.readValue(cached, User.class);
+            }
+        } catch (RedisException e) {
+            log.warn("Redis unavailable, fallback to DB", e);
+        }
+        User user = userRepository.findById(userId).orElseThrow();
+        try {
+            redisTemplate.opsForValue().set(cacheKey, objectMapper.writeValueAsString(user), 10, TimeUnit.MINUTES);
+        } catch (RedisException e) {
+            log.warn("Redis write failed", e);
+        }
+        return user;
     }
"""

# ---- Mock: LLM extraction output (what we expect an LLM to return) ----
MOCK_EXTRACTED_KNOWLEDGE_SESSION_1 = {
    "session_id": "sess_abc123",
    "is_valuable": True,
    "confidence": 0.95,
    "summary": "接口 /api/order/create (OrderService.createOrder) 在并发场景下需要加分布式锁，"
               "使用 Redisson，锁粒度为 userId+productId，TTL 5秒，防止重复下单。",
    "entities": [
        {"name": "/api/order/create", "type": "API"},
        {"name": "OrderService.createOrder", "type": "Method"},
        {"name": "Redisson", "type": "Technology"},
        {"name": "分布式锁", "type": "Pattern"},
        {"name": "并发重复下单", "type": "Bug"},
        {"name": "userId+productId", "type": "LockKey"},
    ],
    "relations": [
        {"source": "/api/order/create", "target": "OrderService.createOrder", "type": "IMPLEMENTED_BY"},
        {"source": "OrderService.createOrder", "target": "并发重复下单", "type": "HAS_ISSUE"},
        {"source": "分布式锁", "target": "并发重复下单", "type": "RESOLVES"},
        {"source": "Redisson", "target": "分布式锁", "type": "IMPLEMENTS"},
        {"source": "OrderService.createOrder", "target": "分布式锁", "type": "REQUIRES"},
    ],
    "tags": ["并发", "分布式锁", "Redisson", "订单", "幂等性"],
    "files_changed": ["src/main/java/com/example/service/OrderService.java"],
}

MOCK_EXTRACTED_KNOWLEDGE_SESSION_2 = {
    "session_id": "sess_def456",
    "is_valuable": True,
    "confidence": 0.88,
    "summary": "UserService.getUserById 使用 Redis Cache-Aside 模式缓存用户信息（TTL 10分钟），"
               "Redis 不可用时降级到 DB 查询，需处理缓存失效场景（注销、修改密码）。",
    "entities": [
        {"name": "UserService.getUserById", "type": "Method"},
        {"name": "Redis", "type": "Technology"},
        {"name": "Cache-Aside", "type": "Pattern"},
        {"name": "Caffeine", "type": "Technology"},
        {"name": "RedisException", "type": "Exception"},
    ],
    "relations": [
        {"source": "UserService.getUserById", "target": "Redis", "type": "USES"},
        {"source": "Cache-Aside", "target": "UserService.getUserById", "type": "APPLIED_TO"},
        {"source": "RedisException", "target": "UserService.getUserById", "type": "HANDLED_BY"},
    ],
    "tags": ["缓存", "Redis", "Cache-Aside", "降级", "UserService"],
    "files_changed": ["src/main/java/com/example/service/UserService.java"],
}

# A session with no valuable knowledge (noise)
MOCK_CONVERSATION_NOISE = {
    "session_id": "sess_ghi789",
    "developer": "charlie",
    "timestamp": "2026-10-12T09:00:00+08:00",
    "turns": [
        {"role": "user", "content": "帮我把这段代码格式化一下"},
        {"role": "assistant", "content": "好的，已经格式化完成。"},
    ]
}

MOCK_EXTRACTED_NOISE = {
    "session_id": "sess_ghi789",
    "is_valuable": False,
    "confidence": 0.1,
    "reason": "纯格式化操作，无业务知识价值",
}

ALL_SESSIONS = [
    (MOCK_CONVERSATION_SESSION_1, MOCK_GIT_DIFF_SESSION_1, MOCK_EXTRACTED_KNOWLEDGE_SESSION_1),
    (MOCK_CONVERSATION_SESSION_2, MOCK_GIT_DIFF_SESSION_2, MOCK_EXTRACTED_KNOWLEDGE_SESSION_2),
    (MOCK_CONVERSATION_NOISE, "", MOCK_EXTRACTED_NOISE),
]
