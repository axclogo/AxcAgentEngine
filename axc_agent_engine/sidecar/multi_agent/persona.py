"""Persona — 角色/人设构建"""
from __future__ import annotations

import logging
from typing import Any

from axc_agent_engine.core.schema import LLMResponse

logger = logging.getLogger(__name__)

GENERATE_PERSONA_PROMPT = """请根据以下信息生成一个角色设定：
主题：{topic}
角色提示：{role_hint}

请返回以下格式的角色设定（纯文本，不要 JSON）：
角色：<角色名称和定位>
立场：<在该主题下的立场>
背景：<专业背景和经验>
行为规则：<发言时应遵循的规则>"""


def build_persona_prompt(agent_name: str, persona: dict) -> str:
	"""构建角色设定 prompt，追加到 Agent 的 system_prompt"""
	parts = []
	if persona.get("role"):
		parts.append(f"你的角色：{persona['role']}")
	if persona.get("stance"):
		parts.append(f"你的立场：{persona['stance']}")
	if persona.get("background"):
		parts.append(f"你的背景：{persona['background']}")
	if persona.get("rules"):
		parts.append(f"行为规则：{persona['rules']}")
	if persona.get("team"):
		parts.append(f"你的队伍：{persona['team']}")
	return "\n".join(parts)


async def generate_persona(topic: str, role_hint: str, llm_client: Any) -> dict:
	"""调 LLM 自动生成角色设定"""
	prompt = GENERATE_PERSONA_PROMPT.format(topic=topic, role_hint=role_hint)
	messages = [{"role": "user", "content": prompt}]
	try:
		resp = await llm_client.chat(messages)
		if not isinstance(resp, LLMResponse):
			raise TypeError(f"LLMProvider.chat 必须返回 LLMResponse，实际得到 {type(resp).__name__}")
		content = resp.message.content
		return _parse_persona_response(content)
	except Exception as e:
		logger.warning(f"[persona] Persona generation failed: {e}")
		return {"role": role_hint}


def _parse_persona_response(content: str) -> dict:
	"""解析 LLM 返回的角色设定文本"""
	result: dict[str, str] = {}
	mapping = {"角色": "role", "立场": "stance", "背景": "background", "行为规则": "rules"}
	for line in content.strip().split("\n"):
		line = line.strip()
		if not line:
			continue
		for cn_key, en_key in mapping.items():
			if line.startswith(f"{cn_key}：") or line.startswith(f"{cn_key}:"):
				result[en_key] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
				break
	return result
