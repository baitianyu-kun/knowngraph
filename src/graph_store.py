"""
Task 4: Graph Storage Layer
将抽取出的 KnowledgeRecord 存入图数据库。

当前实现：纯 Python 内存图（dict + adjacency list），无需外部依赖。
生产替换：将 InMemoryGraphStore 替换为 Neo4jGraphStore，接口完全兼容。
可选增强：安装 networkx 后切换为 NetworkXGraphStore，获得图算法支持。

节点类型: API / Method / Technology / Pattern / Bug / Concept / Developer / Session
边类型:   REQUIRES / RESOLVES / IMPLEMENTED_BY / USES / HAS_ISSUE / APPLIED_TO
          FALLBACK_TO / HANDLES / IMPLEMENTS / CONTRIBUTED_BY / TAGGED_WITH / MODIFIED_FILE / MENTIONS
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from src.knowledge_extractor import KnowledgeRecord


@dataclass
class QueryResult:
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)


class InMemoryGraphStore:
    """
    Pure-Python adjacency-list graph store (no external dependencies).
    Mirrors the API surface of a production Neo4j store.

    _nodes: { node_id: { entity_type, description, tags, sessions, ... } }
    _edges: list of { from, to, relation_type, weight, session_id, timestamp }
    _adj:   { node_id: set of neighbor node_ids }  (bidirectional)
    """

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []
        self._adj: dict[str, set] = defaultdict(set)
        self._session_index: dict[str, KnowledgeRecord] = {}

    # ── Helpers ─────────────────────────────────────────────────────────

    def _add_node(self, node_id: str, attrs: dict) -> None:
        if node_id not in self._nodes:
            self._nodes[node_id] = attrs
        else:
            # Merge sessions and tags
            existing = self._nodes[node_id]
            sessions = existing.get("sessions", [])
            for s in attrs.get("sessions", []):
                if s not in sessions:
                    sessions.append(s)
            existing["sessions"] = sessions
            existing_tags = set(existing.get("tags", []))
            existing_tags.update(attrs.get("tags", []))
            existing["tags"] = list(existing_tags)

    def _add_edge(self, u: str, v: str, attrs: dict) -> None:
        self._edges.append({"from": u, "to": v, **attrs})
        self._adj[u].add(v)
        self._adj[v].add(u)

    # ── Write ────────────────────────────────────────────────────────────

    def ingest(self, record: KnowledgeRecord) -> None:
        """Upsert all entities and relations from a KnowledgeRecord."""
        self._session_index[record.session_id] = record

        # Upsert entity nodes
        for entity in record.entities:
            self._add_node(entity.name, {
                "entity_type": entity.entity_type,
                "description": entity.description,
                "tags": list(record.tags),
                "sessions": [record.session_id],
                "created_at": record.timestamp,
            })

        # Session node
        session_node = f"session:{record.session_id}"
        self._add_node(session_node, {
            "entity_type": "Session",
            "summary": record.summary,
            "developer": record.developer,
            "timestamp": record.timestamp,
            "tags": list(record.tags),
            "files_changed": record.files_changed,
            "confidence": record.confidence,
            "sessions": [record.session_id],
        })

        # Developer node
        dev_node = f"dev:{record.developer}"
        self._add_node(dev_node, {
            "entity_type": "Developer",
            "sessions": [record.session_id],
        })
        self._add_edge(session_node, dev_node, {"relation_type": "CONTRIBUTED_BY", "weight": 1.0})

        # File nodes
        for f in record.files_changed:
            file_node = f"file:{f}"
            self._add_node(file_node, {"entity_type": "File", "sessions": [record.session_id]})
            self._add_edge(session_node, file_node, {"relation_type": "MODIFIED_FILE", "weight": 1.0})

        # Knowledge relations
        entity_names = {e.name for e in record.entities}
        for rel in record.relations:
            if rel.source in entity_names and rel.target in entity_names:
                self._add_edge(rel.source, rel.target, {
                    "relation_type": rel.relation_type,
                    "weight": rel.weight,
                    "session_id": record.session_id,
                    "timestamp": record.timestamp,
                })

        # Session → entity MENTIONS
        for entity in record.entities:
            self._add_edge(session_node, entity.name, {"relation_type": "MENTIONS", "weight": 0.5})

        print(f"[GraphStore] Ingested {record.session_id}: "
              f"{len(record.entities)} entities, {len(record.relations)} relations → "
              f"graph now has {len(self._nodes)} nodes, {len(self._edges)} edges")

    # ── Read / Query ─────────────────────────────────────────────────────

    def query_by_entity(self, entity_name: str, depth: int = 2) -> QueryResult:
        """BFS from entity_name up to `depth` hops."""
        if entity_name not in self._nodes:
            # Partial match
            matches = [n for n in self._nodes if entity_name.lower() in n.lower()]
            if not matches:
                return QueryResult()
            entity_name = matches[0]

        visited = {entity_name}
        frontier = {entity_name}
        for _ in range(depth):
            next_frontier = set()
            for n in frontier:
                next_frontier.update(self._adj.get(n, set()))
            new_nodes = next_frontier - visited
            visited.update(new_nodes)
            frontier = new_nodes

        nodes = [{"id": n, **self._nodes[n]} for n in visited if n in self._nodes]
        edges = [e for e in self._edges if e["from"] in visited and e["to"] in visited]
        summaries = [
            f"[{self._nodes[n].get('developer','?')}] {self._nodes[n].get('summary','')}"
            for n in visited
            if n in self._nodes and self._nodes[n].get("entity_type") == "Session"
        ]
        return QueryResult(nodes=nodes, edges=edges, summaries=summaries)

    def query_by_tag(self, tag: str) -> QueryResult:
        matched_nodes = [
            {"id": nid, **attrs}
            for nid, attrs in self._nodes.items()
            if tag in attrs.get("tags", [])
        ]
        summaries = [
            f"[{n.get('developer','?')}] {n.get('summary','')}"
            for n in matched_nodes
            if n.get("entity_type") == "Session"
        ]
        return QueryResult(nodes=matched_nodes, summaries=summaries)

    def query_by_file(self, file_path: str) -> QueryResult:
        file_node = f"file:{file_path}"
        if file_node not in self._nodes:
            matches = [n for n in self._nodes if file_path in n]
            if not matches:
                return QueryResult()
            file_node = matches[0]

        # Find sessions pointing to this file node
        session_nodes = [
            e["from"] for e in self._edges
            if e["to"] == file_node and e["relation_type"] == "MODIFIED_FILE"
        ]
        result_nodes = [{"id": file_node, **self._nodes[file_node]}]
        summaries = []
        for sn in session_nodes:
            if sn in self._nodes:
                attrs = self._nodes[sn]
                result_nodes.append({"id": sn, **attrs})
                if attrs.get("entity_type") == "Session":
                    summaries.append(f"[{attrs.get('developer','?')}] {attrs.get('summary','')}")
        return QueryResult(nodes=result_nodes, summaries=summaries)

    def stats(self) -> dict:
        type_counts: dict[str, int] = defaultdict(int)
        for attrs in self._nodes.values():
            type_counts[attrs.get("entity_type", "unknown")] += 1
        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "sessions": len(self._session_index),
            "node_types": dict(type_counts),
        }

    def export_json(self, path: str) -> None:
        data = {
            "nodes": [{"id": nid, **attrs} for nid, attrs in self._nodes.items()],
            "edges": self._edges,
        }
        import os; os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[GraphStore] Graph exported to {path}")


# ─── Neo4j Drop-in Replacement (stub) ─────────────────────────────────────

class Neo4jGraphStore:
    """
    Production graph store backed by Neo4j.
    Same interface as InMemoryGraphStore.
    Requires: pip install neo4j
    """

    def __init__(self, uri: str, user: str, password: str):
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(uri, auth=(user, password))
        except ImportError:
            raise RuntimeError("neo4j driver not installed. Run: pip install neo4j")

    def ingest(self, record: KnowledgeRecord) -> None:
        with self._driver.session() as session:
            for entity in record.entities:
                session.run(
                    "MERGE (e:Entity {name: $name}) "
                    "SET e.entity_type = $type, e.description = $desc",
                    name=entity.name, type=entity.entity_type, desc=entity.description
                )
            for rel in record.relations:
                session.run(
                    "MATCH (a:Entity {name: $src}), (b:Entity {name: $dst}) "
                    f"MERGE (a)-[r:{rel.relation_type}]->(b) "
                    "SET r.session_id = $sid, r.timestamp = $ts",
                    src=rel.source, dst=rel.target,
                    sid=record.session_id, ts=record.timestamp
                )

    def query_by_entity(self, entity_name: str, depth: int = 2) -> QueryResult:
        with self._driver.session() as session:
            result = session.run(
                f"MATCH (e:Entity {{name: $name}})-[*1..{depth}]-(n) "
                "RETURN n.name AS id, n.entity_type, n.description LIMIT 50",
                name=entity_name
            )
            nodes = [dict(r) for r in result]
        return QueryResult(nodes=nodes)

    def query_by_tag(self, tag: str) -> QueryResult:
        with self._driver.session() as session:
            result = session.run(
                "MATCH (n) WHERE $tag IN n.tags RETURN n.name AS id, n.entity_type, n.summary LIMIT 50",
                tag=tag
            )
            nodes = [dict(r) for r in result]
        return QueryResult(nodes=nodes)

    def query_by_file(self, file_path: str) -> QueryResult:
        with self._driver.session() as session:
            result = session.run(
                "MATCH (s:Session)-[:MODIFIED_FILE]->(f:File) WHERE f.name CONTAINS $fp "
                "RETURN s.session_id, s.summary, s.developer LIMIT 20",
                fp=file_path
            )
            summaries = [f"[{r['s.developer']}] {r['s.summary']}" for r in result]
        return QueryResult(summaries=summaries)

    def stats(self) -> dict:
        with self._driver.session() as session:
            node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        return {"total_nodes": node_count, "total_edges": edge_count}


if __name__ == "__main__":
    from tests.mock_data import (
        MOCK_CONVERSATION_SESSION_1, MOCK_GIT_DIFF_SESSION_1,
        MOCK_CONVERSATION_SESSION_2, MOCK_GIT_DIFF_SESSION_2,
    )
    from src.collector import MultiSourceCollector
    from src.knowledge_extractor import KnowledgeExtractor

    collector = MultiSourceCollector()
    extractor = KnowledgeExtractor()
    store = InMemoryGraphStore()

    for sess, diff in [
        (MOCK_CONVERSATION_SESSION_1, MOCK_GIT_DIFF_SESSION_1),
        (MOCK_CONVERSATION_SESSION_2, MOCK_GIT_DIFF_SESSION_2),
    ]:
        event = collector.collect(sess, diff_text=diff)
        record = extractor.extract(event, confidence=0.9)
        if record:
            store.ingest(record)

    print("\n── Graph Stats ──")
    print(json.dumps(store.stats(), ensure_ascii=False, indent=2))

    print("\n── Query: /api/order/create ──")
    result = store.query_by_entity("/api/order/create")
    for s in result.summaries:
        print(f"  💡 {s}")

    print("\n── Query tag: 缓存 ──")
    result2 = store.query_by_tag("缓存")
    for s in result2.summaries:
        print(f"  💡 {s}")
