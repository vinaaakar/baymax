"""Screen understanding: locate a described UI element (accessibility tree first, OCR fallback)."""

from .elements import Display, Resolution, ScreenElement, display_for, parse_ax_dump, rank, resolve, to_gaze, vision_box_to_global

__all__ = ["Display", "Resolution", "ScreenElement", "display_for", "parse_ax_dump", "rank", "resolve", "to_gaze", "vision_box_to_global"]
