"""
Task 4: Bi-Temporal Knowledge Hypernode Graph Store
Integrates advances from:
- Zep / Temporal GraphRAG (arXiv:2501.13956)
- TGMS: Agent-Native Bi-Temporal Graph Management (arXiv 2026)
- EvoKG / Kontrast Conflict & Evolution Resolution (arXiv 2025-2026)
- Closed-world code symbol linking (from ref1 and ref2)

Supported Node Types:
- Knowledge (Hypernode with bi-temporal valid_at/invalid_at)
- EvidenceLedger (Immutable proof record: L1-L4, commit, test status)
- CodeSymbol (Physical symbol: FQN, endpoint, file)
- Session / Developer / File (Context tracking)

Supported Edge Types:
- CONSTRAINS / REFERENCES (Knowledge -> CodeSymbol)
- HAS_EVIDENCE (Knowledge -> EvidenceLedger)
- CONTRIBUTED_BY (Knowledge -> Developer)
- SUPERSEDES (Knowledge_new -> Knowledge_old)
- CONTRADICTS (Knowledge_a <-> Knowledge_b)
"""

import json
import time
import uuid
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Set

from src.knowledge_extractor import KnowledgeRecord
from src.contradiction_detector import KnowledgeContradictionDetector, ConflictResolution


@dataclass
class QueryResult:
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    active_knowledge: list[dict] = field(default_factory=list)
    ruled_out_hypotheses: list[str] = field(default_factory=list)


@dataclass
class KnowledgeHypernode:
    id: str
    title: str
    knowledge_type: str        # APIConstraint | BugFixPattern | ArchitectureDecision | ConfigurationTrap
    summary: str
    pitfalls: str
    ruled_out_hypotheses: List[str]
    tags: List[str]
    associated_symbols: List[str]
    status: str = "CORROBORATED"# PROPOSED | CORROBORATED | SUPERSEDED | CONTRADICTED_PENDING_REVIEW
    valid_at: str = ""          # Valid Time (real world validity)
    invalid_at: Optional[str] = None # When superseded or revoked
    transaction_time: str = ""  # System record time
    superseded_by: Optional[str] = None
    contradicts_with: List[str] = field(default_factory=list)


class InMemoryGraphStore:
    """
    Pure-Python bi-temporal hypernode graph store (zero external dependencies).
    Mirrors production Neo4j / FalkorDB schema and semantics.
    """

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []
        self._adj: dict[str, set] = defaultdict(set)
        self._knowledge_nodes: dict[str, KnowledgeHypernode] = {}
        self._symbol_to_knowledge: dict[str, set] = defaultdict(set)
        self._session_index: dict[str, Any] = {}

    # ── Helpers ─────────────────────────────────────────────────────────

    def _add_node(self, node_id: str, attrs: dict) -> None:
        if node_id not in self._nodes:
            self._nodes[node_id] = attrs
        else:
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
        edge = {"from": u, "to": v, **attrs}
        self._edges.append(edge)
        self._adj[u].add(v)
        self._adj[v].add(u)

    # ── 1. 经典三元组 Ingest (向后完全兼容现有 test_pipeline) ────────────

    def ingest(self, record: KnowledgeRecord) -> None:
        """Upsert all entities and relations from a legacy KnowledgeRecord."""
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

    # ── 2. 新一代双时态超节点 Ingest (Bi-Temporal Knowledge Hypernode) ─────

    def ingest_hypernode(
        self,
        extracted_data: Dict[str, Any],
        developer: str,
        session_id: str,
        commit_sha: str = "HEAD",
        test_passed: bool = True
    ) -> Optional[str]:
        """
        超节点入库：自动执行冲突检测、双时态生命周期更新与证据账本绑定。
        """
        if not extracted_data.get("is_valuable", False):
            return None

        now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 1. 构造候选超节点数据
        knowledge_id = f"know_{hashlib.md5((extracted_data.get('title', '') + session_id).encode()).hexdigest()[:12]}"
        candidate_dict = {
            "id": knowledge_id,
            "title": extracted_data.get("title", ""),
            "summary": extracted_data.get("summary", ""),
            "knowledge_type": extracted_data.get("knowledge_type", "None"),
            "associated_symbols": extracted_data.get("associated_symbols", []),
            "ruled_out_hypotheses": extracted_data.get("ruled_out_hypotheses", []),
            "pitfalls": extracted_data.get("pitfalls_and_actions", ""),
            "tags": extracted_data.get("tags", []),
            "evidence_level": extracted_data.get("evidence_level", "L1_AGENT_PATCH"),
        }

        # 2. 矛盾与演化检测（针对所有已知超节点）
        for old_id, old_node in list(self._knowledge_nodes.items()):
            if old_node.invalid_at is not None:
                continue # 已失效的节点跳过

            old_dict = asdict(old_node)
            resolution = KnowledgeContradictionDetector.detect_relation(candidate_dict, old_dict)

            # 场景 A: 演化取代 (SUPERSEDES)
            if resolution.relation_type == "SUPERSEDES":
                # 将旧节点置为失效
                old_node.invalid_at = now_str
                old_node.status = "SUPERSEDED"
                old_node.superseded_by = knowledge_id
                self._nodes[old_id]["invalid_at"] = now_str
                self._nodes[old_id]["status"] = "SUPERSEDED"
                # 添加演化边: new -> old SUPERSEDES
                self._add_edge(knowledge_id, old_id, {
                    "relation_type": "SUPERSEDES",
                    "reason": resolution.reason,
                    "timestamp": now_str
                })

            # 场景 B: 冲突待审 (CONTRADICTS)
            elif resolution.relation_type == "CONTRADICTS":
                old_node.status = "CONTRADICTED_PENDING_REVIEW"
                old_node.contradicts_with.append(knowledge_id)
                self._nodes[old_id]["status"] = "CONTRADICTED_PENDING_REVIEW"
                self._add_edge(knowledge_id, old_id, {
                    "relation_type": "CONTRADICTS",
                    "reason": resolution.reason,
                    "timestamp": now_str
                })

            # 场景 C: 等价再印证 (EQUIVALENT)
            elif resolution.relation_type == "EQUIVALENT":
                # 复用旧节点，追加证据并合并 tags
                self._bind_evidence(old_id, session_id, commit_sha, candidate_dict["evidence_level"], test_passed, now_str)
                old_node.tags = list(set(old_node.tags).union(candidate_dict["tags"]))
                self._nodes[old_id]["tags"] = old_node.tags
                return old_id

        # 3. 创建全新 Knowledge 超节点
        hypernode = KnowledgeHypernode(
            id=knowledge_id,
            title=candidate_dict["title"],
            knowledge_type=candidate_dict["knowledge_type"],
            summary=candidate_dict["summary"],
            pitfalls=candidate_dict["pitfalls"],
            ruled_out_hypotheses=candidate_dict["ruled_out_hypotheses"],
            tags=candidate_dict["tags"],
            associated_symbols=candidate_dict["associated_symbols"],
            status="CORROBORATED" if test_passed else "PROPOSED",
            valid_at=now_str,
            invalid_at=None,
            transaction_time=now_str
        )
        self._knowledge_nodes[knowledge_id] = hypernode

        self._add_node(knowledge_id, {
            "entity_type": "Knowledge",
            "title": hypernode.title,
            "type": hypernode.knowledge_type,
            "summary": hypernode.summary,
            "pitfalls": hypernode.pitfalls,
            "ruled_out": hypernode.ruled_out_hypotheses,
            "tags": hypernode.tags,
            "status": hypernode.status,
            "valid_at": hypernode.valid_at,
            "invalid_at": hypernode.invalid_at,
            "transaction_time": hypernode.transaction_time,
            "sessions": [session_id]
        })

        # 4. 关联代码物理符号 CodeSymbol 节点
        for sym in hypernode.associated_symbols:
            sym_node = f"symbol:{sym}"
            self._add_node(sym_node, {
                "entity_type": "CodeSymbol",
                "fqn": sym,
                "sessions": [session_id]
            })
            self._add_edge(knowledge_id, sym_node, {"relation_type": "CONSTRAINS", "weight": 1.0})
            self._symbol_to_knowledge[sym].add(knowledge_id)

        # 5. 绑定不可变证据账本 EvidenceLedger
        self._bind_evidence(knowledge_id, session_id, commit_sha, candidate_dict["evidence_level"], test_passed, now_str)

        # 6. 关联贡献者 Developer
        dev_node = f"dev:{developer}"
        self._add_node(dev_node, {"entity_type": "Developer", "sessions": [session_id]})
        self._add_edge(knowledge_id, dev_node, {"relation_type": "CONTRIBUTED_BY", "weight": 1.0})

        return knowledge_id

    def _bind_evidence(self, knowledge_id: str, session_id: str, commit_sha: str, level: str, test_passed: bool, ts: str):
        evidence_id = f"ev_{uuid.uuid4().hex[:8]}"
        self._add_node(evidence_id, {
            "entity_type": "EvidenceLedger",
            "level": level,
            "commit_sha": commit_sha,
            "session_id": session_id,
            "test_passed": test_passed,
            "timestamp": ts
        })
        self._add_edge(knowledge_id, evidence_id, {"relation_type": "HAS_EVIDENCE", "weight": 1.0})

    # ── 3. 新一代双时态查询 API ──────────────────────────────────────────

    def query_active_knowledge(self, query: str) -> QueryResult:
        """
        检索当前处于有效时态（invalid_at IS NULL）的高价值知识，
        自动过滤被取代的过时知识，同时提取规避弯路的 ruled_out_hypotheses 负知识。
        """
        active_k_list = []
        all_ruled_out = []
        summaries = []
        matched_nodes = []

        q_lower = query.lower()

        for k_id, k_node in self._knowledge_nodes.items():
            # 双时态过滤：忽略已失效节点
            if k_node.invalid_at is not None:
                continue

            # 匹配符号或文本关键词
            symbol_matched = any(q_lower in sym.lower() for sym in k_node.associated_symbols)
            text_matched = q_lower in k_node.title.lower() or q_lower in k_node.summary.lower() or any(q_lower in t.lower() for t in k_node.tags)

            if symbol_matched or text_matched:
                active_k_list.append(asdict(k_node))
                summaries.append(f"[{k_node.knowledge_type}] {k_node.title} -> {k_node.summary}")
                all_ruled_out.extend(k_node.ruled_out_hypotheses)
                matched_nodes.append({"id": k_id, **self._nodes.get(k_id, {})})

        return QueryResult(
            nodes=matched_nodes,
            summaries=summaries,
            active_knowledge=active_k_list,
            ruled_out_hypotheses=list(set(all_ruled_out))
        )

    def query_temporal_evolution(self, symbol: str) -> List[Dict[str, Any]]:
        """
        查询指定代码符号的历史演化链条（包括有效与已被 supersede 的历史版本）
        """
        chain = []
        for k_id, k_node in self._knowledge_nodes.items():
            if any(symbol.lower() in sym.lower() for sym in k_node.associated_symbols):
                chain.append({
                    "id": k_id,
                    "title": k_node.title,
                    "status": k_node.status,
                    "valid_at": k_node.valid_at,
                    "invalid_at": k_node.invalid_at,
                    "superseded_by": k_node.superseded_by,
                    "summary": k_node.summary
                })
        # 按时间排序
        chain.sort(key=lambda x: x["valid_at"])
        return chain

    # ── 4. 经典图查询接口 (向后兼容) ──────────────────────────────────────

    def query_by_entity(self, entity_name: str, depth: int = 2) -> QueryResult:
        if entity_name not in self._nodes:
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
            if n in self._nodes and (self._nodes[n].get("entity_type") in ["Session", "Knowledge"]) and self._nodes[n].get("summary")
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
            if n.get("entity_type") in ["Session", "Knowledge"] and n.get("summary")
        ]
        return QueryResult(nodes=matched_nodes, summaries=summaries)

    def query_by_file(self, file_path: str) -> QueryResult:
        file_node = f"file:{file_path}"
        if file_node not in self._nodes:
            matches = [n for n in self._nodes if file_path in n]
            if not matches:
                return QueryResult()
            file_node = matches[0]

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
            "knowledge_hypernodes": len(self._knowledge_nodes),
            "node_types": dict(type_counts),
        }

    def export_json(self, path: str) -> None:
        data = {
            "nodes": [{"id": nid, **attrs} for nid, attrs in self._nodes.items()],
            "edges": self._edges,
            "knowledge_hypernodes": [asdict(k) for k in self._knowledge_nodes.values()]
        }
        import os; os.path.dirname(path) and os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[GraphStore] Graph exported to {path}")


# ─── Neo4j Drop-in Replacement (Production Stub) ──────────────────────────

class Neo4jGraphStore:
    def __init__(self, uri: str, user: str, password: str):
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(uri, auth=(user, password))
        except ImportError:
            raise RuntimeError("neo4j driver not installed. Run: pip install neo4j")

    def ingest_hypernode(self, k: Dict[str, Any]) -> None:
        """生产级 Cypher 模板执行"""
        cypher = """
        MERGE (k:Knowledge {id: $id})
        ON CREATE SET
            k.title = $title,
            k.type = $type,
            k.summary = $summary,
            k.pitfalls = $pitfalls,
            k.ruled_out = $ruled_out,
            k.valid_at = datetime(),
            k.status = $status
        """
        with self._driver.session() as session:
            session.run(cypher, **k)
