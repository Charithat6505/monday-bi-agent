"""
The agent: Groq decides which analytics tool(s) to call and how to phrase
the answer. It NEVER computes numbers itself.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from groq import Groq

from . import analytics


SYSTEM_PROMPT = """You are a business-intelligence analyst for Skylark Drones,
a founder-facing chat assistant over live monday.com data.

Rules:
- Always answer using the tool results you receive.
- Never invent or estimate numbers yourself.
- Every tool result includes a caveats list.
- Explain relevant caveats in plain language.
- If a question is ambiguous, ask one short clarifying question.
- Keep answers concise and founder-friendly.
"""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "pipeline_summary",
            "description": "Open-deal pipeline value overall and by sector, with and without outliers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sector": {
                        "type": "string",
                        "description": "Optional sector filter, such as Mining.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revenue_summary",
            "description": "Realised revenue from Won deals, with and without outliers.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sector_breakdown",
            "description": "Deal count/value and Work Order count by sector.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "billing_status_summary",
            "description": "Work Order billing state and amounts receivable.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quantity_summary",
            "description": "Total surveyed quantity by unit, optionally filtered by type of work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type_of_work": {
                        "type": "string",
                        "description": "Optional work type filter, such as LiDAR.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "data_quality_overview",
            "description": "Show data-quality caveats found during cleaning.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


class Agent:
    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-oss-20b",
    ):
        self._client = Groq(api_key=api_key)
        self._model = model

    def answer(self, history: List[Dict[str, str]]) -> str:
        """Return an answer using Groq and the analytics tools."""

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            }
        ]

        for item in history:
            role = item["role"]

            if role == "model":
                role = "assistant"

            messages.append(
                {
                    "role": role,
                    "content": item["content"],
                }
            )

        for _ in range(4):
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )

            message = response.choices[0].message

            assistant_message = {
                "role": "assistant",
                "content": message.content or "",
            }

            if message.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in message.tool_calls
                ]

            messages.append(assistant_message)

            if not message.tool_calls:
                return message.content or (
                    "I didn't get a response. Please try rephrasing."
                )

            for tool_call in message.tool_calls:
                function_name = tool_call.function.name

                try:
                    arguments = json.loads(
                        tool_call.function.arguments or "{}"
                    )
                except json.JSONDecodeError:
                    arguments = {}

                function = analytics.TOOLS.get(function_name)

                if function:
                    # Remove invalid empty argument names
                    if isinstance(arguments, dict):
                        arguments = {
                            key: value
                            for key, value in arguments.items()
                            if isinstance(key, str) and key.strip()
                        }

                    # Call the analytics function
                    result: Dict[str, Any] = function(**arguments)

                else:
                    result = {
                        "error": f"Unknown tool: {function_name}"
                    }

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(
                            result,
                            default=str,
                        ),
                    }
                )

        return (
            "I ran into trouble finishing that analysis. "
            "Please try a narrower question."
        )