# -*- coding: utf-8 -*-
"""Bootstrap logging helpers."""

from __future__ import annotations

import logging
import sys


def setup_bootstrap_logging(debug: bool = False) -> None:
    """Initialize stderr-only logging before config is loaded."""
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)
