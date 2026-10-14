"""
Task 3: Knowledge Extractor
从通过过滤的 agent 会话中抽取结构化知识三元组（实体+关系）。

生产中：调用 LLM structured output（Pydantic schema）
当前：使用 Mock 数据模拟 LLM 输出，并附规则增强层
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from src.collector import CollectedEvent


@dataclass
class Entity:
    name: str
    entity_type: str   # API | Method | Technology | Pattern | Bug | Concept
    description: str = ""


@dataclass
class Relation:
    source: str
    target: str
    relation_type: str  # e.g. REQUIRES, RESOLVES, IMPLEMENTED_BY, USES, HAS_ISSUE
    weight: float = 1.0


@dataclass
class KnowledgeRecord:
    session_id: str
    developer: str
    timestamp: str
    summary: str
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "developer": self.developer,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "entities": [{"name": e.name, "type": e.entity_type, "desc": e.description} for e in self.entities],
            "relations": [{"src": r.source, "rel": r.relation_type, "dst": r.target, "w": r.weight} for r in self.relations],
            "tags": self.tags,
            "files_changed": self.files_changed,
            "confidence": self.confidence,
        }


# ─────────────────────────────────────────────────────
# Mock LLM responses keyed by session_id
# (Production: replace with actual LLM structured call)
# ─────────────────────────────────────────────────────
_MOCK_LLM_RESULTS: dict[str, dict] = {
    "sess_abc123": {
        "summary": "接口 /api/order/create (OrderService.createOrder) 在并发场景下需要加分布式锁，"
                   "使用 Redisson，锁粒度为 userId+productId，TTL 5秒，防止重复下单。",
        "entities": [
            {"name": "/api/order/create",        "type": "API",         "desc": "创建订单接口"},
            {"name": "OrderService.createOrder",  "type": "Method",      "desc": "订单创建服务方法"},
            {"name": "Redisson",                  "type": "Technology",  "desc": "Redis 分布式锁客户端"},
            {"name": "分布式锁",                  "type": "Pattern",     "desc": "防止并发竞态条件的锁机制"},
            {"name": "并发重复下单",              "type": "Bug",         "desc": "高并发下重复创建订单的问题"},
        ],
        "relations": [
            {"src": "/api/order/create",          "rel": "IMPLEMENTED_BY", "dst": "OrderService.createOrder"},
            {"src": "OrderService.createOrder",   "rel": "HAS_ISSUE",      "dst": "并发重复下单"},
            {"src": "分布式锁",                   "rel": "RESOLVES",       "dst": "并发重复下单"},
            {"src": "Redisson",                   "rel": "IMPLEMENTS",     "dst": "分布式锁"},
            {"src": "OrderService.createOrder",   "rel": "REQUIRES",       "dst": "分布式锁"},
        ],
        "tags": ["并发", "分布式锁", "Redisson", "订单", "幂等性"],
    },
    "sess_def456": {
        "summary": "UserService.getUserById 使用 Redis Cache-Aside 模式缓存用户信息（TTL 10分钟），"
                   "Redis 不可用时降级到 DB 查询，写/改操作需主动删除缓存（注销/改密场景）。",
        "entities": [
            {"name": "UserService.getUserById",   "type": "Method",      "desc": "按 ID 查询用户"},
            {"name": "Redis",                     "type": "Technology",  "desc": "分布式缓存"},
            {"name": "Cache-Aside",               "type": "Pattern",     "desc": "旁路缓存模式"},
            {"name": "RedisException",            "type": "Concept",     "desc": "Redis 不可用异常"},
            {"name": "缓存降级",                  "type": "Pattern",     "desc": "Redis 故障时 fallback 到 DB"},
        ],
        "relations": [
            {"src": "UserService.getUserById",    "rel": "USES",         "dst": "Redis"},
            {"src": "Cache-Aside",               "rel": "APPLIED_TO",   "dst": "UserService.getUserById"},
            {"src": "缓存降级",                  "rel": "HANDLES",      "dst": "RedisException"},
            {"src": "UserService.getUserById",   "rel": "FALLBACK_TO",  "dst": "缓存降级"},
        ],
        "tags": ["缓存", "Redis", "Cache-Aside", "降级", "UserService"],
    },
}


class MockLLMExtractor:
    """
    Simulates structured extraction via LLM.

    Production prompt template:
    ─────────────────────────────────────────────────────────────────────
    你是一个代码知识图谱构建专家。给定以下 Agent 对话记录和 Git 变更，
    请抽取其中包含的业务技术知识，以 JSON 格式输出：

    {
      "summary": "一句话总结核心知识点",
      "entities": [
        {"name": "实体名", "type": "API|Method|Technology|Pattern|Bug|Concept", "desc": "简要说明"}
      ],
      "relations": [
        {"src": "源实体名", "rel": "关系类型", "dst": "目标实体名"}
      ],
      "tags": ["标签1", "标签2"]
    }

    对话记录：
    {conversation_text}

    Git 变更文件：{files_changed}
    ─────────────────────────────────────────────────────────────────────
    """

    def extract(self, session_id: str, conversation_text: str, files_changed: list[str]) -> Optional[dict]:
        """Return mock structured result for known session IDs."""
        if session_id in _MOCK_LLM_RESULTS:
            return _MOCK_LLM_RESULTS[session_id]
        # Fallback: rule-based minimal extraction
        return self._rule_based_fallback(conversation_text, files_changed)

    def _rule_based_fallback(self, text: str, files: list[str]) -> Optional[dict]:
        """Simple regex-based extraction as a last resort."""
        entities = []
        # Extract Java method-like patterns e.g. SomeService.someMethod
        methods = re.findall(r'\b([A-Z][a-zA-Z]+Service\.[a-z][a-zA-Z]+)\b', text)
        for m in set(methods):
            entities.append({"name": m, "type": "Method", "desc": ""})

        # Extract API paths
        apis = re.findall(r'/api/[a-zA-Z0-9/_-]+', text)
        for a in set(apis):
            entities.append({"name": a, "type": "API", "desc": ""})

        if not entities and not files:
            return None

        return {
            "summary": "（规则提取）" + text[:100],
            "entities": entities[:10],
            "relations": [],
            "tags": [],
        }


class KnowledgeExtractor:
    """
    Orchestrates extraction: calls LLM (mock), enriches with git context,
    and produces a KnowledgeRecord.
    """

    def __init__(self):
        self.llm = MockLLMExtractor()

    def extract(self, event: CollectedEvent, confidence: float = 1.0) -> Optional[KnowledgeRecord]:
        raw = self.llm.extract(
            session_id=event.session_id,
            conversation_text=event.conversation.raw_text,
            files_changed=event.git_context.files_changed,
        )
        if raw is None:
            return None

        entities = [
            Entity(name=e["name"], entity_type=e["type"], description=e.get("desc", ""))
            for e in raw.get("entities", [])
        ]
        relations = [
            Relation(source=r["src"], relation_type=r["rel"], target=r["dst"])
            for r in raw.get("relations", [])
        ]

        return KnowledgeRecord(
            session_id=event.session_id,
            developer=event.developer,
            timestamp=event.timestamp,
            summary=raw.get("summary", ""),
            entities=entities,
            relations=relations,
            tags=raw.get("tags", []),
            files_changed=event.git_context.files_changed,
            confidence=confidence,
        )


if __name__ == "__main__":
    from tests.mock_data import (
        MOCK_CONVERSATION_SESSION_1, MOCK_GIT_DIFF_SESSION_1,
        MOCK_CONVERSATION_SESSION_2, MOCK_GIT_DIFF_SESSION_2,
    )
    from src.collector import MultiSourceCollector
    import json

    collector = MultiSourceCollector()
    extractor = KnowledgeExtractor()

    print("=" * 60)
    print("Knowledge Extractor Test")
    print("=" * 60)

    for sess, diff in [
        (MOCK_CONVERSATION_SESSION_1, MOCK_GIT_DIFF_SESSION_1),
        (MOCK_CONVERSATION_SESSION_2, MOCK_GIT_DIFF_SESSION_2),
    ]:
        event = collector.collect(sess, diff_text=diff)
        record = extractor.extract(event, confidence=0.9)
        if record:
            print(f"\n📌 Session: {record.session_id} | Developer: {record.developer}")
            print(f"   Summary: {record.summary}")
            print(f"   Entities ({len(record.entities)}): {[e.name for e in record.entities]}")
            print(f"   Relations ({len(record.relations)}): {[(r.source, r.relation_type, r.target) for r in record.relations]}")
            print(f"   Tags: {record.tags}")
            print(f"   Files: {record.files_changed}")
