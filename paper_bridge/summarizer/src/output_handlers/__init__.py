"""Output handlers for processing summaries to different destinations.

Exported lazily (PEP 562): the GitHub handler depends on ``gitpython`` and the
Slack handler's render path depends on the headless-browser stack. Lazy export
lets one handler be imported and tested without the other's dependencies.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    "BaseOutputHandler": ".base",
    "SlackOutputHandler": ".slack_handler",
    "GitHubOutputHandler": ".github_handler",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return __all__


if TYPE_CHECKING:  # pragma: no cover
    from .base import BaseOutputHandler
    from .github_handler import GitHubOutputHandler
    from .slack_handler import SlackOutputHandler
