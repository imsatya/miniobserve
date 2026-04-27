"""Optional framework integrations (install ``langchain-core`` or ``miniobserve[langchain]``)."""

from typing import Any, List

__all__ = ["MiniObserveCallbackHandler", "miniobserve_langchain_callbacks"]


def __getattr__(name: str) -> Any:
    if name == "MiniObserveCallbackHandler":
        from .langchain_callback import MiniObserveCallbackHandler

        return MiniObserveCallbackHandler
    if name == "miniobserve_langchain_callbacks":
        from .langchain_callback import miniobserve_langchain_callbacks

        return miniobserve_langchain_callbacks
    raise AttributeError(name)


def __dir__() -> List[str]:
    return list(__all__)
