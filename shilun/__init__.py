"""Shilun standalone trading decision system."""

__all__ = ["PipelineConfig", "ShilunPipeline"]


def __getattr__(name: str):
    # Lightweight query processes do not need the research pipeline and its
    # scientific stack. Keep the public exports available on first access.
    if name in __all__:
        from importlib import import_module
        value = getattr(import_module("shilun.pipeline"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
