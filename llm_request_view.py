"""
Contentview addon for mitmproxy that renders LLM API request bodies as
pretty-printed JSON with \\n sequences in strings expanded as real newlines,
indented to align with the opening quote of each string value.

Load with: mitmproxy -s llm_request_view.py
"""
import json
from mitmproxy import contentviews


def _expand_string_newlines(text: str) -> str:
    result = []
    i = 0
    col = 0
    in_string = False
    string_start_col = 0

    while i < len(text):
        ch = text[i]

        if not in_string:
            if ch == '"':
                in_string = True
                string_start_col = col
            result.append(ch)
            col = 0 if ch == '\n' else col + 1
            i += 1
        else:
            if ch == '\\' and i + 1 < len(text):
                nxt = text[i + 1]
                if nxt == 'n':
                    result.append('\n')
                    result.append(' ' * string_start_col)
                    col = string_start_col
                    i += 2
                else:
                    result.append(ch)
                    result.append(nxt)
                    col += 2
                    i += 2
            elif ch == '"':
                in_string = False
                result.append(ch)
                col += 1
                i += 1
            elif ch == '\n':
                result.append(ch)
                col = 0
                i += 1
            else:
                result.append(ch)
                col += 1
                i += 1

    return ''.join(result)


def _is_llm_request(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    model = data.get("model")
    # Anthropic / OpenAI chat-completions: {model, messages}
    if isinstance(model, str) and isinstance(data.get("messages"), list):
        return True
    # OpenAI Responses API request: {model, input}
    if isinstance(model, str) and isinstance(data.get("input"), list):
        return True
    # OpenAI Responses API response: {object: "response", output}
    if data.get("object") == "response" and isinstance(data.get("output"), list):
        return True
    return False


class LlmRequestContentview(contentviews.Contentview):
    name = "LLM Request"

    def prettify(self, data: bytes, metadata: contentviews.Metadata) -> str:
        try:
            parsed = json.loads(data)
        except Exception as e:
            raise ValueError(f"Not valid JSON: {e}") from e

        return _expand_string_newlines(json.dumps(parsed, indent=2, ensure_ascii=False))

    def render_priority(self, data: bytes, metadata: contentviews.Metadata) -> float:
        content_type = metadata.content_type or ""
        if "json" not in content_type:
            return 0
        try:
            parsed = json.loads(data)
        except Exception:
            return 0
        return 1.5 if _is_llm_request(parsed) else 0


contentviews.add(LlmRequestContentview)
