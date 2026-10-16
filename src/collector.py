"""
Task 1: Multi-source information collector
Simulates the "hook" layer that captures data from:
  - Agent conversation sessions (via Claude Code hooks or file polling)
  - Git diffs for changed files
  - Session metadata

This module is the entry point of the pipeline.
"""

import json
import subprocess
import os
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime

from src.hook_collector import LocalDLPFilter


@dataclass
class MicroLoopSignal:
    should_trigger: bool
    trigger_type: str  # BUG_FIX_LOOP | WALKTHROUGH_LOOP | MANUAL | NONE
    confidence: float
    description: str


@dataclass
class ConversationSession:
    session_id: str
    developer: str
    timestamp: str
    turns: list[dict]
    raw_text: str = ""

    def __post_init__(self):
        # Build a flat text representation for LLM processing
        parts = []
        for turn in self.turns:
            role = "用户" if turn["role"] == "user" else "Agent"
            parts.append(f"[{role}]: {turn['content']}")
        self.raw_text = "\n".join(parts)


@dataclass
class GitContext:
    session_id: str
    files_changed: list[str] = field(default_factory=list)
    diff_summary: str = ""
    commit_message: str = ""
    commit_hash: str = ""


@dataclass
class CollectedEvent:
    """Unified event container from all sources."""
    session_id: str
    developer: str
    timestamp: str
    conversation: ConversationSession
    git_context: GitContext
    source_type: str = "claude_code"  # claude_code | codex | deepseek | generic


class ConversationCollector:
    """
    Simulates collecting conversation sessions from agent hooks.
    In production:
      - Claude Code: reads from ~/.claude/hooks or PostToolUse webhook
      - Generic: watches a log directory for session JSON files
    """

    def from_dict(self, session_dict: dict) -> ConversationSession:
        return ConversationSession(
            session_id=session_dict["session_id"],
            developer=session_dict["developer"],
            timestamp=session_dict["timestamp"],
            turns=session_dict["turns"],
        )

    def from_json_file(self, path: str) -> ConversationSession:
        with open(path) as f:
            data = json.load(f)
        return self.from_dict(data)

    def from_stdin(self, raw_json: str) -> ConversationSession:
        """Entry point for Claude Code hook (receives JSON via stdin)."""
        data = json.loads(raw_json)
        return self.from_dict(data)


class GitContextCollector:
    """
    Collects git context: changed files, diffs, commit info.
    In production, runs git commands in the project root.
    """

    def from_mock_diff(self, session_id: str, diff_text: str) -> GitContext:
        """Used for testing with pre-defined mock diff strings."""
        files = self._parse_files_from_diff(diff_text)
        return GitContext(
            session_id=session_id,
            files_changed=files,
            diff_summary=diff_text[:500],
            commit_message="[mock] fix concurrent issue",
            commit_hash="abc1234",
        )

    def from_repo(self, session_id: str, repo_path: str = ".") -> GitContext:
        """
        Runs git commands to capture the latest staged/committed changes.
        """
        try:
            # Get the latest commit hash and message
            result = subprocess.run(
                ["git", "log", "-1", "--pretty=format:%H|%s"],
                cwd=repo_path, capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return GitContext(session_id=session_id)

            parts = result.stdout.strip().split("|", 1)
            commit_hash = parts[0] if parts else ""
            commit_message = parts[1] if len(parts) > 1 else ""

            # Get changed files in last commit
            files_result = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", commit_hash],
                cwd=repo_path, capture_output=True, text=True, timeout=10
            )
            files_changed = [f for f in files_result.stdout.strip().splitlines() if f]

            # Get diff (truncated)
            diff_result = subprocess.run(
                ["git", "show", "--stat", commit_hash],
                cwd=repo_path, capture_output=True, text=True, timeout=10
            )
            diff_summary = diff_result.stdout[:1000]

            return GitContext(
                session_id=session_id,
                files_changed=files_changed,
                diff_summary=diff_summary,
                commit_message=commit_message,
                commit_hash=commit_hash,
            )
        except Exception as e:
            print(f"[GitContextCollector] Failed to collect git context: {e}")
            return GitContext(session_id=session_id)

    def _parse_files_from_diff(self, diff_text: str) -> list[str]:
        files = []
        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                # Extract "b/path/to/file"
                parts = line.split(" ")
                if len(parts) >= 4:
                    file_path = parts[3].lstrip("b/")
                    files.append(file_path)
        return files


class MicroLoopDetector:
    """
    微闭环信号监听器（基于 ref1 & ref2 优化）：
    不必等整场长对话退出，在检测到“排障闭环”或“架构走读闭环”时增量触发后台分析。
    """
    @staticmethod
    def detect(conversation: ConversationSession, git_context: GitContext, test_output: str = "") -> MicroLoopSignal:
        text = conversation.raw_text.lower()
        has_error_mention = any(k in text for k in ["error", "exception", "failed", "报错", "异常", "排查", "死锁", "超时"])
        has_code_changes = len(git_context.files_changed) > 0 or bool(git_context.diff_summary)
        has_test_success = any(k in (test_output.lower() + text) for k in ["build success", "tests passed", "单测通过", "测试通过", "all passed", "exit code: 0"])

        # 信号 A：排障闭环 (报错 -> 改码 -> 单测通过)
        if has_error_mention and has_code_changes and has_test_success:
            return MicroLoopSignal(
                should_trigger=True,
                trigger_type="BUG_FIX_LOOP",
                confidence=0.92,
                description="检测到完整的排障闭环：报错排查 -> 代码修改 -> 单测转绿验证"
            )

        # 信号 B：深度架构走读闭环 (连续检查多个符号/文件，无改码，输出长文架构结论)
        if not has_code_changes and len(conversation.turns) >= 3:
            assistant_text = "".join([t.get("content", "") for t in conversation.turns if t.get("role") in ["assistant", "agent"]])
            if len(assistant_text) >= 200 and any(k in assistant_text for k in ["机制", "架构", "死锁", "加锁", "必须", "约定", "设计", "规则", "约束"]):
                return MicroLoopSignal(
                    should_trigger=True,
                    trigger_type="WALKTHROUGH_LOOP",
                    confidence=0.88,
                    description="检测到架构走读闭环：多轮代码逻辑走读分析并输出了高密度机制约定"
                )

        return MicroLoopSignal(
            should_trigger=False,
            trigger_type="NONE",
            confidence=0.1,
            description="未达到微闭环触发阈值"
        )


class MultiSourceCollector:
    """
    Orchestrates collection from all sources into a unified CollectedEvent.
    """

    def __init__(self):
        self.conv_collector = ConversationCollector()
        self.git_collector = GitContextCollector()
        self.micro_loop_detector = MicroLoopDetector()

    def collect(
        self,
        session_dict: dict,
        diff_text: str = "",
        repo_path: Optional[str] = None,
        test_output: str = "",
    ) -> CollectedEvent:
        # 1. 客户端 DLP 脱敏
        sanitized_session = LocalDLPFilter.sanitize_obj(session_dict)
        conversation = self.conv_collector.from_dict(sanitized_session)

        # 2. 收集 Git 上下文
        if diff_text:
            sanitized_diff = LocalDLPFilter.sanitize(diff_text)
            git_ctx = self.git_collector.from_mock_diff(conversation.session_id, sanitized_diff)
        elif repo_path:
            git_ctx = self.git_collector.from_repo(conversation.session_id, repo_path)
        else:
            git_ctx = GitContext(session_id=conversation.session_id)

        # 3. 收集事件并挂载微闭环判定
        event = CollectedEvent(
            session_id=conversation.session_id,
            developer=conversation.developer,
            timestamp=conversation.timestamp,
            conversation=conversation,
            git_context=git_ctx,
        )
        return event

    def detect_micro_loop(self, event: CollectedEvent, test_output: str = "") -> MicroLoopSignal:
        return self.micro_loop_detector.detect(event.conversation, event.git_context, test_output)

    def to_dict(self, event: CollectedEvent) -> dict:
        return asdict(event)



if __name__ == "__main__":
    # Quick smoke test
    from tests.mock_data import MOCK_CONVERSATION_SESSION_1, MOCK_GIT_DIFF_SESSION_1

    collector = MultiSourceCollector()
    event = collector.collect(MOCK_CONVERSATION_SESSION_1, diff_text=MOCK_GIT_DIFF_SESSION_1)
    print(f"Collected session: {event.session_id}")
    print(f"Developer: {event.developer}")
    print(f"Files changed: {event.git_context.files_changed}")
    print(f"Conversation turns: {len(event.conversation.turns)}")
    print("\nRaw conversation text:")
    print(event.conversation.raw_text[:300])
