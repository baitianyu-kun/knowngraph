"""
Enhanced Knowledge Engine integrating best practices from ref1 and ref2:
1. AST Grounding (Symbol anchor mapping, no hallucinated symbols)
2. Trajectory Folding (Fold trial-and-error, record ruled_out_hypotheses)
3. Value Gatekeeper with Counterfactual Specificity Test (Kill generic coding tips)
4. Read-only Code Walkthrough Support (Knowledge extracted even without git diff)
5. Evidence Ledger & Multi-tier Trust Level (L1~L4, verified vs proposed)
6. Knowledge Hypernode Graph Modeling (with bi-temporal valid_at/invalid_at)
"""

import os
import sys
import json
import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from openai import OpenAI


# ─── 升级版 Prompt：融合一票否决制、反通用常识、试错折叠与受控本体 ───────────

ENHANCED_SYSTEM_PROMPT = """你是一个企业级软件研发团队的「隐性业务知识提炼与图谱构建引擎」。
你的任务是分析开发者与 Code Agent（如 Claude Code, Codex, DeepSeek）的协作轨迹、工具调用记录、代码符号表以及 Git 变更，提炼出可沉淀为团队图谱资产的高价值知识。

【第一步：严苛的价值门控（Value Gatekeeper - 一票否决制）】
在提取任何知识前，你必须先执行以下 4 项一票否决检验。只要命中任意一条，直接判定 `is_valuable=false`：
1. 【反通用常识否决（Counterfactual Specificity Test）】：
   - 假设将这条知识从当前业务系统剥离，放到任意一个同语言的开源通用项目中依然成立（例如：“并发写 HashMap 需加锁”、“判空避免 NPE”、“数据库查询应加索引”、“更新缓存防脏读”等），且不包含当前项目具体接口路径、特定业务表、私有配置项或下游系统契约，则判定为“通用编程常识废话”，立即一票否决！
2. 【未闭环/纯猜测否决】：
   - 对话仍在排查中途、修改后单测仍报错未解决、或结论仅为 Agent 未经代码/测试证实的纯猜测。
3. 【琐碎过程否决】：
   - 仅涉及变量/方法重命名、缩进调整、单次拼写错误纠正、普通代码注释添加、或显而易见的基础语法编译错误修复。
4. 【无代码实体锚点否决】：
   - 无法明确绑定到提供的候选代码符号表（`candidate_code_symbols`）或具体的业务接口路由、配置项。

【第二步：试错轨迹折叠（Trajectory Folding）】
- 如果排查过程中包含多轮试错（例如：前几轮怀疑是数据库连接池耗尽并尝试修改，但发现无效；最终定位到是缓存未设过期时间并解决）：
- 必须折叠排除掉错误假设，将其填入 `ruled_out_hypotheses`（避坑/排除项），**仅将最终实证确认的结论**提炼为主知识！

【第三步：受控本体抽取（Ontology-Guided Extraction）】
抽取必须基于受控本体规范：
- `knowledge_type`:
  - `APIConstraint`: 业务接口调用的隐性参数、Header、鉴权或调用顺序约束
  - `BugFixPattern`: 定位深层并发竞态、死锁、内存泄露或跨服务时序问题的排障定式
  - `ArchitectureDecision`: 涉及业务权衡的架构设计决策（如降级、隔离、幂等方案）
  - `ConfigurationTrap`: 中间件、第三方 SDK 或私有框架的隐性配置深坑
- `symbols`: 必须精准引用 `candidate_code_symbols` 中的 `symbol_id` 或明确的业务接口/配置名，**严禁凭空捏造不存在的代码符号**！

【输出 JSON 契约】：
{
  "is_valuable": true/false,
  "confidence": 0.0到1.0,
  "gatekeeper_check": {
    "passed_counterfactual_test": true/false,
    "has_verified_closure": true/false,
    "is_non_trivial": true/false,
    "has_grounded_anchor": true/false,
    "verdict_reason": "详细说明通过或否决的具体依据"
  },
  "knowledge_type": "APIConstraint|BugFixPattern|ArchitectureDecision|ConfigurationTrap|None",
  "title": "简明标题（如：OrderService 创建订单高并发防重约束）",
  "summary": "一句话核心业务技术结论",
  "associated_symbols": ["引用的 symbol_id 或具体的 API/类名"],
  "ruled_out_hypotheses": ["排查过程中被证伪的错误推测或无效尝试（供他人避开弯路）"],
  "pitfalls_and_actions": "核心避坑指南与具体行动指导",
  "tags": ["标签1", "标签2"],
  "evidence_level": "L1_AGENT_PATCH|L2_TOOL_VERIFIED|L3_READ_ANALYSIS|L4_COMMITTED"
}
"""


class EnhancedDeepSeekKnowledgeEngine:
    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.deepseek.com", model: str = "deepseek-flash"):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url
        self.model = model
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def analyze_session(
        self,
        developer: str,
        turns: List[Dict[str, str]],
        candidate_code_symbols: List[Dict[str, Any]],
        files_changed: List[str] = None,
        git_diff: str = "",
        test_execution_result: str = ""
    ) -> Dict[str, Any]:
        """
        调用 DeepSeek API 分析增强版多源信息
        """
        dialogue_text = "\n".join([f"[{t.get('role', 'user')}]: {t.get('content', '')}" for t in turns])

        prompt_content = f"""【开发者】: {developer}
【AST 候选代码符号表（Candidate Grounded Symbols）】:
{json.dumps(candidate_code_symbols, ensure_ascii=False, indent=2) if candidate_code_symbols else "无显式符号表"}

【修改的文件列表】:
{json.dumps(files_changed or [], ensure_ascii=False)}

【单测/工具执行结果 (Verification Evidence)】:
{test_execution_result if test_execution_result else "无单测输出"}

【Git Diff 内容 (截取关键部分)】:
{git_diff[:2500] if git_diff else "无 Git Diff（纯代码阅读/排查场景）"}

【Agent 对话记录】:
{dialogue_text}
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": ENHANCED_SYSTEM_PROMPT},
                {"role": "user", "content": prompt_content},
            ],
            stream=False,
            temperature=0.1,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"Failed to parse LLM response: {content}")
