"""Ask a vision model about a captured screen, ported from ``core/imagery.py``.

The Actor never receives pixels: it asks a question about an image and gets a
text report back. That keeps the Actor's context textual while still settling
rendered facts, which is exactly the split upstream uses (``look`` -> imagery
-> a text answer on the next turn).
"""
import base64
import json
import urllib.request


class VisionError(RuntimeError):
    """The vision call failed or returned nothing usable."""


VISION_INSTRUCTION = (
    "You are a vision inspector. Answer the question about this screenshot "
    "precisely and only from what is visible. If the screenshot cannot settle "
    "the question, say so explicitly instead of guessing. Keep the answer under "
    "120 words and quote any visible text you rely on.")


def ask_about_image(client, image_bytes, question, *, mime="image/png",
                    max_tokens=512, timeout=120):
    """Send one image plus one question to an OpenAI-compatible vision model."""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": getattr(client, "model", ""),
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_INSTRUCTION + "\n\nQuestion: " + question},
                {"type": "image_url",
                 "image_url": {"url": "data:{};base64,{}".format(mime, encoded)}},
            ],
        }],
    }
    request = urllib.request.Request(
        client.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + client.api_key})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except Exception as exc:
        raise VisionError("vision call failed: " + repr(exc)) from exc
    try:
        text = str(body["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError("vision response had no message: " + repr(exc)) from exc
    if not text:
        raise VisionError("vision model returned empty content")
    return text


def record_usage(client):
    """Vision calls are not role calls; count them separately for the report."""
    calls = getattr(client, "calls", None)
    if isinstance(calls, dict):
        calls["vision"] = calls.get("vision", 0) + 1
