"""Model recommendations for a MacBook Air M4 with 16 GB unified memory.

Approximate resident memory for Q4 quantised GGUF weights + 4k context. These
are guidance figures, not measurements on the user's machine.
"""

from __future__ import annotations

RECOMMENDED_MODELS = [
    {"name": "llama3.2:3b", "approxMemoryGB": 3.0, "role": "default: conversation, intent, planning",
     "note": "Good speed/quality balance on M4 16 GB."},
    {"name": "llama3.2:1b", "approxMemoryGB": 1.5, "role": "fallback when memory is tight",
     "note": "Fastest; weaker at planning and JSON."},
    {"name": "qwen2.5-coder:7b", "approxMemoryGB": 5.5, "role": "coding agent",
     "note": "Better code edits; slower. Do not run alongside other large models."},
    {"name": "qwen2.5:7b", "approxMemoryGB": 5.5, "role": "research summaries",
     "note": "Stronger reasoning; ~2x slower than 3B."},
    {"name": "llama3.1:8b", "approxMemoryGB": 6.0, "role": "higher quality conversation",
     "note": "Fits in 16 GB but leaves less room for apps; expect slower responses."},
]

MAX_RECOMMENDED_GB = 7.0
