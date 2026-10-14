"""
Real LLM Extractor & Filter using DeepSeek API (deepseek-flash)
用于真实评估与抽取：
1. 多源信息输入（Agent 对话上下文 + Git Diff + 修改文件）
2. 判断知识价值（有意义、业务相关、共性、可复用、能帮到他人）
3. 结构化知识抽取（实体、关系三元组、标签、避坑指南、总结）
"""

import os
import json
import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from openai import OpenAI


SYSTEM_PROMPT_JUDGE_AND_EXTRACT = """你是一个企业级软件研发团队的「代码知识沉淀专家与架构师」。
团队中有许多开发者在使用 AI Code Agent（如 Claude Code, Codex, DeepSeek 等）进行需求开发、Bug排查、重构和性能优化。
每次会话结束后，系统会采集：
1. 开发者与 Agent 的完整对话记录
2. 关联的 Git 修改文件列表与 Git Diff

【核心任务】
请综合分析这些多源信息，判断本次会话中是否沉淀了【高价值、有意义、与业务相关、具有共性且可复用】的技术知识。

【高价值知识标准（满足以下特征之一）：】
1. 业务踩坑与排错沉淀：例如特定接口在高并发下的幂等/锁机制、数据库死锁原因、特定依赖版本的兼容问题等。
2. 架构设计与约束决策：例如为什么选择某种缓存策略、降级机制、消息消费顺序保障等。
3. 核心业务接口/模块的隐性规则：例如调用某接口必须先满足的前置校验、特定参数的边界约束等。
4. 具有通用指导价值：未来其他开发者接触相关接口/代码模块时，能直接避免重复踩坑或理解设计意图。

【低价值/无沉淀价值内容（需过滤）：】
1. 简单的语法纠错、拼写修改、代码格式化（Lint/Prettier）。
2. 毫无业务或架构背景的代码翻译、单纯加几行日志。
3. 仅是一次性机械修改，未体现任何可推广的技术经验或业务规则。
4. 未解决任何问题或中途放弃的探索。

【输出要求】
请严格输出合法 JSON（不含任何外部说明文字），格式如下：
{
  "is_valuable": true/false,
  "confidence": 0.0到1.0的浮点数,
  "filter_reason": "详细说明判断有价值或无价值的理由",
  "knowledge_type": "BugFix|ArchitectureDecision|PerformanceOptimization|APIConstraint|None",
  "summary": "一句话核心知识总结（给未来开发者看的直接结论，如：接口x在并发场景下需要加分布式锁）",
  "entities": [
    {"name": "实体名（如具体的接口路径、类名、方法名、组件技术名）", "type": "API|Method|Technology|Pattern|Issue|Config", "desc": "简要说明"}
  ],
  "relations": [
    {"src": "源实体名", "rel": "REQUIRES|RESOLVES|USES|IMPLEMENTED_BY|CAUSES|CONSTRAINED_BY", "dst": "目标实体名"}
  ],
  "tags": ["标签1", "标签2", ...],
  "applicable_scenarios": "该知识适用的场景（如：高并发下单、秒杀）",
  "precautions_and_pitfalls": "避坑指南或核心注意事项（如：锁粒度必须是userId+productId，不能锁全表）"
}
"""


class DeepSeekKnowledgeEngine:
    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.deepseek.com", model: str = "deepseek-flash"):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url
        self.model = model
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def analyze_session(self, developer: str, turns: List[Dict[str, str]], files_changed: List[str], git_diff: str = "") -> Dict[str, Any]:
        """
        调用 DeepSeek API 分析会话与 Git 多源信息
        """
        # 构建 prompt 内容
        dialogue_text = "\n".join([f"[{t.get('role', 'user')}]: {t.get('content', '')}" for t in turns])
        
        prompt_content = f"""【开发者】: {developer}
【修改的文件列表】: {json.dumps(files_changed, ensure_ascii=False) if files_changed else "无文件修改"}

【Git Diff 内容】:
{git_diff[:2500] if git_diff else "无 Git Diff"}

【Agent 对话记录】:
{dialogue_text}
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_JUDGE_AND_EXTRACT},
                {"role": "user", "content": prompt_content},
            ],
            stream=False,
            temperature=0.1,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content
        try:
            parsed = json.loads(content)
            return parsed
        except json.JSONDecodeError:
            # 兼容可能的 markdown 代码块包装
            match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"Failed to parse LLM response as JSON: {content}")


if __name__ == "__main__":
    from tests.mock_data import MOCK_CONVERSATION_SESSION_1, MOCK_GIT_DIFF_SESSION_1
    
    engine = DeepSeekKnowledgeEngine()
    print("Testing DeepSeek LLM Knowledge Analysis...")
    result = engine.analyze_session(
        developer=MOCK_CONVERSATION_SESSION_1["developer"],
        turns=MOCK_CONVERSATION_SESSION_1["turns"],
        files_changed=["src/main/java/com/example/service/OrderService.java"],
        git_diff=MOCK_GIT_DIFF_SESSION_1
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
