"""Context management for long attempts, ported from upstream ``core/loop.py``.

Three behaviours matter and are reproduced here:

1. ``cap_worklog`` — bound the model-written WORK LOG at a *line* boundary and
   say that the tail was dropped. Upstream replaced a blind character slice
   after it amputated whole sections mid-value; the kept part is the top,
   because the summariser is told to put CURRENT STATE / KEY VALUES / NEXT
   STEPS first and re-derivable detail last.

2. ``fold_input`` — a long tool/trace result becomes head + tail, because the
   part that matters at the end (tracebacks, final error lines) is exactly what
   a prefix slice hides.

3. ``fold_history`` — once a message list exceeds a budget, fold the oldest
   exchanges into one summary message, keeping the most recent turns intact.
   Folding is counted so callers can see how many times it happened.
"""
import re

_DROPPED_NOTE = ("[... WORK LOG truncated at the char cap — least-important "
                 "(re-derivable) detail dropped; the machine still holds ground "
                 "truth, re-probe if needed]")
_FOLD_MARK = "\u2026[trace middle elided]\u2026"


def cap_worklog(text, limit):
    """Keep the top of a WORK LOG, cut at a line boundary, and flag the drop."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:          # no usable line break near the cap: hard cut
        cut = limit
    return text[:cut].rstrip() + "\n" + _DROPPED_NOTE


def fold_input(content, head=1500, tail=2500):
    """Render an over-long observation as head + tail."""
    text = content if isinstance(content, str) else str(content)
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n" + _FOLD_MARK + "\n" + text[-tail:]


def _split_blocks(messages):
    """Group a user/assistant transcript into (user, assistant) pairs."""
    blocks, pending = [], None
    for message in messages:
        role = message.get("role")
        if role == "user":
            pending = message
        elif role == "assistant" and pending is not None:
            blocks.append((pending, message))
            pending = None
        elif role == "assistant" and pending is None:
            blocks.append((None, message))
    if pending is not None:
        blocks.append((pending, None))
    return blocks


def fold_history(messages, budget_chars=120000, keep_recent=4, summarizer=None):
    """Fold the oldest exchanges into one summary message when over budget.

    ``summarizer(blocks) -> str`` renders the folded text; without one the
    blocks are reduced to a digest of their first lines. Returns
    ``(messages, folds)`` where folds is 0 when nothing was folded.
    """
    total = sum(len(str(m.get("content", ""))) for m in messages)
    if total <= budget_chars or len(messages) <= keep_recent:
        return list(messages), 0

    blocks = _split_blocks(messages)
    if len(blocks) <= 1:
        return list(messages), 0
    split = max(0, len(blocks) - max(1, keep_recent // 2))
    old, recent = blocks[:split], blocks[split:]
    if not old:
        return list(messages), 0

    if summarizer is not None:
        summary = summarizer(old)
    else:
        key = re.compile(r"CURRENT STATE|KEY VALUES|NEXT STEPS|state|next", re.IGNORECASE)
        lines = []
        for user, assistant in old:
            for message in (user, assistant):
                if not message:
                    continue
                text = str(message.get("content", ""))
                picked = [ln for ln in text.splitlines() if key.search(ln)][:3]
                lines.extend(picked or text.splitlines()[:2])
        summary = "\n".join(lines)[:4000] or "(earlier exchanges folded)"

    folded = [{"role": "user",
               "content": "[earlier exchanges folded into this summary]\n" + summary}]
    for user, assistant in recent:
        if user is not None:
            folded.append(user)
        if assistant is not None:
            folded.append(assistant)
    return folded, 1


def estimate_tokens(messages):
    """Rough size estimate: 4 characters per token, rounded up."""
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return (chars + 3) // 4
