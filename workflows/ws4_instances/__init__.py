"""Reusable WS4 TSA-instance scaffolding and area-control scheduling."""

__all__ = ["AreaControlConfig", "AreaControlError", "run_area_control", "scaffold_instance"]


def __getattr__(name: str):
    """Import the implementation lazily so ``python -m`` does not double-import it."""
    if name in {"AreaControlConfig", "AreaControlError", "run_area_control"}:
        from . import area_control

        return getattr(area_control, name)
    if name == "scaffold_instance":
        from .make_instance import scaffold_instance

        return scaffold_instance
    raise AttributeError(name)
