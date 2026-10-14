"""
Task 2: Knowledge Value Filter
判断一次 agent 会话是否包含有价值的、可沉淀的业务知识。

过滤维度：
  1. 长度过滤 - 过短的对话通常无实质内容
  2. 关键词启发式过滤 - 是否涉及业务相关技术词汇
  3. 代码变更关联 - 是否有对应的文件修改（信号更强）
  4. Mock-LLM 打分 - 模拟 LLM 评分（生产中调用真实 LLM）

Returns a FilterResult with is_valuable flag and reason.
"""

import re
from dataclasses import dataclass
from typing import Optional

from src.collector import CollectedEvent


# ---- Heuristic keyword sets ----
VALUABLE_PATTERNS = [
    # Bug / issue patterns
    r"bug|问题|报错|异常|exception|error|crash|故障|NPE|OOM|死锁|deadlock",
    # Architecture / design decisions
    r"设计|架构|方案|重构|优化|模式|pattern|分布式|微服务|中间件",
    # Concurrency / performance
    r"并发|锁|race.condition|分布式锁|幂等|缓存|cache|性能|qps|tps|超时|timeout",
    # Security
    r"鉴权|权限|token|JWT|SQL.注入|XSS|CSRF|安全",
    # Data consistency
    r"事务|transaction|一致性|回滚|rollback|数据库|DB|mysql|redis|kafka|mq",
    # API / Interface knowledge
    r"接口|API|endpoint|参数|入参|返回值|响应|请求|幂等|限流|熔断",
]

NOISE_PATTERNS = [
    r"^(帮我|请帮我|能帮我)?(格式化|整理|美化|对齐|缩进)",
    r"^(把|将).*翻译",
    r"^写.*注释$",
    r"语法错误|syntax.error.*typo",
    r"帮我生成.*模板",
]

# Minimum thresholds
MIN_TURNS = 3           # At least 3 turns (user + assistant + user)
MIN_CONTENT_CHARS = 200 # At least 200 chars total content
MIN_KEYWORD_SCORE = 2   # At least 2 pattern matches


@dataclass
class FilterResult:
    session_id: str
    is_valuable: bool
    confidence: float      # 0.0 - 1.0
    reason: str
    keyword_score: int = 0
    has_code_changes: bool = False
    turn_count: int = 0


class KnowledgeValueFilter:
    """
    Two-stage filter:
      Stage 1 - Fast heuristic check (no LLM call needed)
      Stage 2 - Mock LLM scoring (simulate prompt-based evaluation)
    """

    def filter(self, event: CollectedEvent) -> FilterResult:
        session_id = event.session_id
        text = event.conversation.raw_text
        turns = event.conversation.turns
        has_code_changes = len(event.git_context.files_changed) > 0

        # --- Stage 1: Quick disqualifiers ---
        turn_count = len(turns)
        if turn_count < MIN_TURNS:
            return FilterResult(
                session_id=session_id,
                is_valuable=False,
                confidence=0.95,
                reason=f"对话轮次过少（{turn_count} < {MIN_TURNS}），无实质内容",
                turn_count=turn_count,
                has_code_changes=has_code_changes,
            )

        if len(text) < MIN_CONTENT_CHARS:
            return FilterResult(
                session_id=session_id,
                is_valuable=False,
                confidence=0.9,
                reason=f"内容过短（{len(text)} chars < {MIN_CONTENT_CHARS}），判断为无效会话",
                turn_count=turn_count,
                has_code_changes=has_code_changes,
            )

        # Check noise patterns first
        for np in NOISE_PATTERNS:
            if re.search(np, text, re.IGNORECASE):
                return FilterResult(
                    session_id=session_id,
                    is_valuable=False,
                    confidence=0.85,
                    reason="命中噪声模式（格式化/翻译/模板等低价值操作）",
                    turn_count=turn_count,
                    has_code_changes=has_code_changes,
                )

        # --- Stage 2: Keyword scoring ---
        keyword_score = 0
        matched_patterns = []
        for pattern in VALUABLE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                keyword_score += 1
                matched_patterns.append(pattern[:30])

        # Code changes are a strong positive signal
        code_change_bonus = 2 if has_code_changes else 0
        total_score = keyword_score + code_change_bonus

        # --- Stage 3: Mock-LLM scoring ---
        # In production: call LLM with structured prompt to get a 0-1 score
        llm_score = self._mock_llm_score(text, has_code_changes)

        # Combined decision
        if total_score >= MIN_KEYWORD_SCORE and llm_score >= 0.6:
            confidence = min(0.99, 0.5 + llm_score * 0.3 + keyword_score * 0.05)
            return FilterResult(
                session_id=session_id,
                is_valuable=True,
                confidence=confidence,
                reason=f"命中 {keyword_score} 个业务关键词模式，LLM 评分 {llm_score:.2f}，"
                       f"{'有代码变更' if has_code_changes else '无代码变更'}",
                keyword_score=keyword_score,
                has_code_changes=has_code_changes,
                turn_count=turn_count,
            )
        else:
            return FilterResult(
                session_id=session_id,
                is_valuable=False,
                confidence=0.7,
                reason=f"关键词得分 {total_score}（阈值 {MIN_KEYWORD_SCORE}），LLM 评分 {llm_score:.2f}",
                keyword_score=keyword_score,
                has_code_changes=has_code_changes,
                turn_count=turn_count,
            )

    def _mock_llm_score(self, text: str, has_code_changes: bool) -> float:
        """
        Simulates LLM-based knowledge value scoring.

        Production prompt template:
        ─────────────────────────────────────────────────────────────────
        你是一个技术知识价值评估专家。给定以下开发者与 AI Agent 的对话，
        请评估该对话是否包含可供团队复用的业务技术知识。

        评估维度：
        1. 是否涉及具体的业务模块、接口或功能？
        2. 是否包含可推广的技术决策、Bug 原因或解决方案？
        3. 其他开发者在类似场景下是否能从中获益？

        请输出 JSON：{"score": 0.0~1.0, "reason": "..."}
        ─────────────────────────────────────────────────────────────────

        In this mock, we simulate scoring based on text features.
        """
        score = 0.3  # baseline

        # Heuristic signals that would boost LLM score
        if "并发" in text or "concurren" in text.lower():
            score += 0.25
        if "分布式锁" in text or "distributed lock" in text.lower():
            score += 0.2
        if "缓存" in text or "cache" in text.lower():
            score += 0.15
        if "接口" in text or "API" in text:
            score += 0.1
        if has_code_changes:
            score += 0.15
        if "建议" in text or "注意" in text or "需要" in text:
            score += 0.05

        # Cap and noise for realism
        score = min(score, 0.99)

        # Low-value signals
        if len(text.split("\n")) < 4:
            score *= 0.5

        return round(score, 2)


if __name__ == "__main__":
    from tests.mock_data import (
        MOCK_CONVERSATION_SESSION_1, MOCK_GIT_DIFF_SESSION_1,
        MOCK_CONVERSATION_SESSION_2, MOCK_GIT_DIFF_SESSION_2,
        MOCK_CONVERSATION_NOISE,
    )
    from src.collector import MultiSourceCollector

    collector = MultiSourceCollector()
    filt = KnowledgeValueFilter()

    sessions = [
        (MOCK_CONVERSATION_SESSION_1, MOCK_GIT_DIFF_SESSION_1),
        (MOCK_CONVERSATION_SESSION_2, MOCK_GIT_DIFF_SESSION_2),
        (MOCK_CONVERSATION_NOISE, ""),
    ]

    print("=" * 60)
    print("Knowledge Value Filter Test")
    print("=" * 60)
    for sess, diff in sessions:
        event = collector.collect(sess, diff_text=diff)
        result = filt.filter(event)
        status = "✅ VALUABLE" if result.is_valuable else "❌ FILTERED"
        print(f"\n{status} | {result.session_id}")
        print(f"  Turns: {result.turn_count} | Code changes: {result.has_code_changes}")
        print(f"  Confidence: {result.confidence:.2f} | Keyword score: {result.keyword_score}")
        print(f"  Reason: {result.reason}")
