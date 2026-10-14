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
from typing import Optional
from datetime import datetime


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


class MultiSourceCollector:
    """
    Orchestrates collection from all sources into a unified CollectedEvent.
    """

    def __init__(self):
        self.conv_collector = ConversationCollector()
        self.git_collector = GitContextCollector()

    def collect(
        self,
        session_dict: dict,
        diff_text: str = "",
        repo_path: Optional[str] = None,
    ) -> CollectedEvent:
        conversation = self.conv_collector.from_dict(session_dict)

        if diff_text:
            git_ctx = self.git_collector.from_mock_diff(conversation.session_id, diff_text)
        elif repo_path:
            git_ctx = self.git_collector.from_repo(conversation.session_id, repo_path)
        else:
            git_ctx = GitContext(session_id=conversation.session_id)

        return CollectedEvent(
            session_id=conversation.session_id,
            developer=conversation.developer,
            timestamp=conversation.timestamp,
            conversation=conversation,
            git_context=git_ctx,
        )

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
