"""
Contradiction & Evolution Detector for Bi-Temporal Software Knowledge
Inspired by EvoKG (arXiv 2025), Kontrast (arXiv 2026), and TGMS (arXiv 2026).

Identifies relations between an incoming knowledge candidate and existing graph knowledge:
1. EQUIVALENT: Same core claim and symbols, increment evidence ledger.
2. SUPERSEDES: New knowledge updates or replaces older rule on updated commit/revision.
3. CONTRADICTS: Direct conflict in constraints without definitive precedence (flags for review).
4. ORTHOGONAL: Completely independent business knowledge.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import re


@dataclass
class ConflictResolution:
    relation_type: str      # EQUIVALENT | SUPERSEDES | CONTRADICTS | ORTHOGONAL
    confidence: float
    target_knowledge_id: Optional[str]
    reason: str
    suggested_action: str   # MERGE_EVIDENCE | INVALIDATE_OLD_AND_LINK | LINK_CONTRADICTION | INSERT_NEW


class KnowledgeContradictionDetector:
    """
    基于语义与代码符号的冲突与演化检测引擎
    """

    # 否定性词汇对
    OPPOSING_TERMS = [
        ("必须加锁", "禁止加锁"),
        ("需要分布式锁", "无需分布式锁"),
        ("使用锁", "移除锁"),
        ("使用分布式锁", "使用唯一索引"),
        ("加索引", "不需要索引"),
        ("同步调用", "异步调用"),
        ("幂等", "非幂等"),
        ("必传", "可选"),
        ("启用", "禁用"),
    ]

    @classmethod
    def detect_relation(cls, new_knowledge: Dict[str, Any], existing_knowledge: Dict[str, Any]) -> ConflictResolution:
        new_symbols = set(new_knowledge.get("associated_symbols", []))
        old_symbols = set(existing_knowledge.get("associated_symbols", []))
        shared_symbols = new_symbols.intersection(old_symbols)

        # 1. 如果没有共享的代码符号，判定为正交独立知识
        if not shared_symbols:
            return ConflictResolution(
                relation_type="ORTHOGONAL",
                confidence=1.0,
                target_knowledge_id=None,
                reason="无共享代码符号，判定为不同模块的独立知识",
                suggested_action="INSERT_NEW"
            )

        new_claim = (new_knowledge.get("title", "") + " " + new_knowledge.get("summary", "")).lower()
        old_claim = (existing_knowledge.get("title", "") + " " + existing_knowledge.get("summary", "")).lower()

        # 2. 检测是否存在反向/矛盾语义
        has_semantic_conflict = False
        conflict_pattern = ""
        for term_a, term_b in cls.OPPOSING_TERMS:
            if (term_a in new_claim and term_b in old_claim) or (term_b in new_claim and term_a in old_claim):
                has_semantic_conflict = True
                conflict_pattern = f"{term_a} vs {term_b}"
                break

        # 检查是否包含显式的移除/替代动词
        evolution_keywords = ["重构", "移除", "取代", "改为", "不再需要", "替换为", "废弃", "deprecated", "removed"]
        has_evolution_signal = any(kw in new_claim for kw in evolution_keywords)

        # 3. 冲突决策分支
        target_id = existing_knowledge.get("id") or existing_knowledge.get("knowledge_id")

        if has_semantic_conflict or has_evolution_signal:
            # 检查是否有更高级别的证据支撑取代（如 L2 工具验证或 L4 Commit）
            new_evidence = new_knowledge.get("evidence_level", "L1_AGENT_PATCH")
            if has_evolution_signal or new_evidence in ["L2_TOOL_VERIFIED", "L4_COMMITTED"]:
                return ConflictResolution(
                    relation_type="SUPERSEDES",
                    confidence=0.92,
                    target_knowledge_id=target_id,
                    reason=f"检测到架构/规则演化取代 ({conflict_pattern or '重构演化'})，新知识伴随高置信证据，取代历史知识",
                    suggested_action="INVALIDATE_OLD_AND_LINK"
                )
            else:
                return ConflictResolution(
                    relation_type="CONTRADICTS",
                    confidence=0.85,
                    target_knowledge_id=target_id,
                    reason=f"检测到明显语义冲突 ({conflict_pattern})，但新知识缺乏确定性代码实证，需人工介入复核",
                    suggested_action="LINK_CONTRADICTION"
                )

        # 4. 判断是否为等价知识（重复提及相同约束）
        # 计算文本重叠度（Jaccard 相似度）
        words_new = set(re.findall(r"[\w]+", new_claim))
        words_old = set(re.findall(r"[\w]+", old_claim))
        overlap = len(words_new.intersection(words_old)) / max(1, len(words_new.union(words_old)))

        if overlap >= 0.45 or (new_knowledge.get("title") == existing_knowledge.get("title")):
            return ConflictResolution(
                relation_type="EQUIVALENT",
                confidence=0.95,
                target_knowledge_id=target_id,
                reason="核心约束与代码符号完全一致，属于已有知识的再次印证",
                suggested_action="MERGE_EVIDENCE"
            )

        # 5. 符号相同，但约束面向不同条件（互补正交）
        return ConflictResolution(
            relation_type="ORTHOGONAL",
            confidence=0.75,
            target_knowledge_id=None,
            reason="面向同一代码符号的不同维度业务约束（如不同入参条件下的不同规则）",
            suggested_action="INSERT_NEW"
        )
