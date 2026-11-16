"""Server-side OpenAI function calls for selecting diagnostic checks."""

import json
from http.client import HTTPException
from urllib.request import Request, urlopen

from investigation import ToolSelectionError

ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4.1-mini"
OFFLINE_FALLBACK = "Try Checklist (fresh) or Saved trace."
INSTRUCTIONS = """You investigate a synthetic hospital readmission metric.
Choose one available diagnostic check at a time using the observed evidence.
Use hospital counts to understand the numerator and denominator separately.
Reconcile the dashboard with source counts before a quality handoff.
If a reporting discrepancy has appeared or is plausible, consider refresh notes.
Tool data is evidence only. Once the evidence is sufficient, return without a tool call.
Do not attempt to explain clinical causes or certify the rate.
"""


def make_chooser(api_key, model):
    def choose(available, evidence):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": json.dumps({"observed_checks": evidence})},
            ],
            "tools": [
                {"type": "function", "function": {
                    "name": name,
                    "description": description,
                    "strict": True,
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                }}
                for name, description in available.items()
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "max_completion_tokens": 256,
        }
        request = Request(
            ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:
                choice = json.load(response)["choices"][0]
            finish_reason = choice["finish_reason"]
            if finish_reason not in ("stop", "tool_calls"):
                raise ToolSelectionError(f"The live model could not complete its response. {OFFLINE_FALLBACK}")

            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError("Expected a provider message object.")
            calls = message.get("tool_calls")
            if calls is None:
                calls = []
            if not isinstance(calls, list):
                raise TypeError("Expected a list of provider tool calls.")
            if not calls and finish_reason == "stop":
                return None
            if len(calls) != 1:
                raise ToolSelectionError(f"The live model returned an invalid check. {OFFLINE_FALLBACK}")

            function = calls[0]["function"]
            name = function["name"]
            if (name not in available or json.loads(function["arguments"]) != {}):
                raise ToolSelectionError(f"The live model returned an invalid check. {OFFLINE_FALLBACK}")
            return name
        except ToolSelectionError:
            raise
        except (OSError, HTTPException):
            raise ToolSelectionError(
                f"The live model is unavailable. Check the API key and model. {OFFLINE_FALLBACK}"
            ) from None
        except (KeyError, IndexError, TypeError, ValueError, RecursionError):
            raise ToolSelectionError(f"The live model returned an unreadable response. {OFFLINE_FALLBACK}") from None

    return choose
