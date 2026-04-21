# -*- coding: utf-8 -*-
"""Core pipeline package."""

from importlib import import_module

__all__ = ["pipeline"]


def __getattr__(name: str):
    if name == "pipeline":
        module = import_module("src.core.pipeline")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
