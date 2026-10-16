"""Turn a dictated transcript into text segments and editor commands.

``"Hello world new paragraph how are you question mark"`` becomes::

    [{"text": "Hello world"}, {"cmd": "paragraph"}, {"text": "how are you"}, {"text": "?"}]
"""

from __future__ import annotations

import re

PUNCT = {
    "comma": ",",
    "period": ".",
    "full stop": ".",
    "question mark": "?",
    "exclamation mark": "!",
    "exclamation point": "!",
    "colon": ":",
    "semicolon": ";",
    "open quote": "“",
    "close quote": "”",
    "dash": " — ",
    "ellipsis": "…",
    # Turkish
    "virgül": ",",
    "nokta": ".",
    "soru işareti": "?",
    "ünlem işareti": "!",
}
ACTIONS = {
    "new paragraph": "paragraph",
    "new line": "linebreak",
    "scene break": "scenebreak",
    "undo that": "undo",
    "stop dictation": "stop",
    "yeni paragraf": "paragraph",
    "yeni satır": "linebreak",
    "geri al": "undo",
}

_PHRASES = sorted(list(PUNCT) + list(ACTIONS), key=len, reverse=True)
# A command must be a whole phrase, optionally surrounded by Whisper's own punctuation.
_RE = re.compile(r"(?P<lead>[\s,.;:!?]*)\b(?P<phrase>" + "|".join(re.escape(p) for p in _PHRASES) + r")\b[\s,.;:!?]*", re.IGNORECASE)


def parse(transcript: str, enabled: bool = True) -> list[dict]:
    text = transcript.strip()
    if not text:
        return []
    if not enabled:
        return [{"text": text}]
    out: list[dict] = []
    pos = 0
    for m in _RE.finditer(text):
        phrase = m.group("phrase").lower()
        before = text[pos : m.start()]
        if phrase not in PUNCT:
            # Keep the sentence punctuation Whisper put before an action ("city. New paragraph").
            before += m.group("lead").strip()
        before = before.strip()
        if before:
            out.append({"text": before})
        if phrase in PUNCT:
            out.append({"text": PUNCT[phrase], "punct": True})
        else:
            out.append({"cmd": ACTIONS[phrase]})
        pos = m.end()
    rest = text[pos:].strip()
    if rest:
        out.append({"text": rest})
    return _merge(out)


def _merge(segs: list[dict]) -> list[dict]:
    """Attach punctuation to the preceding text so spacing comes out right."""
    merged: list[dict] = []
    for s in segs:
        if s.get("punct") and merged and "text" in merged[-1]:
            merged[-1] = {"text": merged[-1]["text"].rstrip() + s["text"]}
        elif "text" in s:
            merged.append({"text": s["text"]})
        else:
            merged.append(s)
    return merged
