"""The agent: Gemini decides which analytics tool(s) to call and how to phrase the
answer. It NEVER computes numbers itself -- every figure it states comes back from a
tool call in bi_agent/analytics.py, which does the maths in pandas.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from google import genai
from google.genai import types

from . import analytics

SYSTEM_PROMPT = """You are a business-intelligence analyst for Skylark Drones, a founder-facing
chat assistant over live monday.com data (a Deals/pipeline board and a Work Orders board).

Rules:
- Always answer using the tool results you receive. Never invent or estimate a number yourself.
- Every tool result includes a "caveats" list describing data-quality issues (missing values,
  status/stage mismatches, stale dates, mixed units, no shared ID between boards, etc).
  Always weave the caveats that are relevant to the question into your answer in plain
  language -- don't just append a generic disclaimer.
- If a question is ambiguous (e.g. which sector, which time period, "this quarter" when most
  dates are stale, whether to include outlier deals), ask ONE short clarifying question
  before calling a tool, unless a reasonable default clearly works -- in that case, state the
  default you're using and proceed.
- Keep answers concise and founder-friendly: lead with the number, then the one or two caveats
  that matter, then offer to go deeper.
"""

_DECLARATIONS = [
    types.FunctionDeclaration(
        name="pipeline_summary",
        description="Open-deal pipeline value overall and by sector, with/without outliers.",
        parameters={"type": "OBJECT", "properties": {
            "sector": {"type": "STRING", "description": "Optional sector filter, e.g. 'Mining'."}}},
    ),
    types.FunctionDeclaration(
        name="revenue_summary",
        description="Realised revenue from Won deals, with/without outliers, and value coverage.",
        parameters={"type": "OBJECT", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="sector_breakdown",
        description="Deal count/value and Work Order count by sector across both boards (approximate join).",
        parameters={"type": "OBJECT", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="billing_status_summary",
        description="Work Order billing state: amount to be billed, amount receivable, by invoice/WO status.",
        parameters={"type": "OBJECT", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="quantity_summary",
        description="Total surveyed quantity by unit (Hectares, Acres, Km, ...), optionally filtered by type of work.",
        parameters={"type": "OBJECT", "properties": {
            "type_of_work": {"type": "STRING", "description": "Optional substring filter, e.g. 'LiDAR'."}}},
    ),
    types.FunctionDeclaration(
        name="data_quality_overview",
        description="All data-quality caveats found during cleaning, for direct 'how reliable is this data' questions.",
        parameters={"type": "OBJECT", "properties": {}},
    ),
]

_TOOLS = types.Tool(function_declarations=_DECLARATIONS)


class Agent:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def answer(self, history: List[Dict[str, str]]) -> str:
        """history: list of {"role": "user"|"model", "content": str}. Returns the reply text."""
        contents = [
            types.Content(role=h["role"], parts=[types.Part(text=h["content"])]) for h in history
        ]
        for _ in range(4):  # a few tool-call rounds is plenty for this data
            resp = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT, tools=[_TOOLS], temperature=0.2
                ),
            )
            candidate = resp.candidates[0]
            fn_calls = [p.function_call for p in candidate.content.parts if p.function_call]
            if not fn_calls:
                return resp.text or "I didn't get a response — please try rephrasing."

            contents.append(candidate.content)
            for call in fn_calls:
                fn = analytics.TOOLS.get(call.name)
                result: Dict[str, Any] = (
                    fn(**dict(call.args or {})) if fn else {"error": f"unknown tool {call.name}"}
                )
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part(function_response=types.FunctionResponse(
                            name=call.name, response={"result": json.dumps(result, default=str)}
                        ))],
                    )
                )
        return "I ran into trouble finishing that analysis — please try a narrower question."