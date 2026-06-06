"""Summarizer package.

Public symbols are exported lazily (PEP 562) so importing a light-weight module
(e.g. the Slack output handler or constants) does not eagerly pull in the heavy
ML stack used by ``fetcher``/``renderer``/``retriever`` (llama-index, selenium,
graphrag-toolkit). This keeps unit tests and tooling fast and importable without
the full inference dependency set installed.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

# Map each public symbol to the submodule that defines it.
_EXPORTS: dict[str, str] = {
    # aws_helpers
    "get_cross_inference_model_id": ".aws_helpers",
    "get_ssm_param_value": ".aws_helpers",
    "submit_batch_job": ".aws_helpers",
    "upload_dir_to_s3": ".aws_helpers",
    "upload_to_s3": ".aws_helpers",
    "wait_for_batch_job_completion": ".aws_helpers",
    # constants
    "EnvVars": ".constants",
    "Format": ".constants",
    "Language": ".constants",
    "LanguageModelId": ".constants",
    "LocalPaths": ".constants",
    "NULL_STRING": ".constants",
    "S3Paths": ".constants",
    "SSMParams": ".constants",
    # fetcher (heavy)
    "Figure": ".fetcher",
    "Paper": ".fetcher",
    "PaperFetcher": ".fetcher",
    # input handlers
    "ArxivInputHandler": ".input_handlers",
    "BaseInputHandler": ".input_handlers",
    "GenericPDFHandler": ".input_handlers",
    "ParsedContent": ".input_handlers",
    # NOTE: ``logger`` / ``is_aws_env`` are intentionally NOT lazy-exported here.
    # The ``logger`` submodule shares its name with the Logger *instance* it
    # defines; if any code imports the submodule first, Python binds the submodule
    # as the package's ``logger`` attribute, and a later ``from ...src import
    # logger`` then resolves to the MODULE (no ``.info``) instead of the instance,
    # depending on import order. Binding them eagerly below (the logger shim is
    # cheap) removes that ambiguity for every caller.
    # output handlers
    "BaseOutputHandler": ".output_handlers",
    "GitHubOutputHandler": ".output_handlers",
    "SlackOutputHandler": ".output_handlers",
    # renderer (heavy)
    "HtmlToImageConverter": ".renderer",
    "PaperDocumentBuilder": ".renderer",
    "Result": ".renderer",
    # retriever (heavy)
    "PaperRetriever": ".retriever",
    "Retriever": ".retriever",
    # summarizer (heavy)
    "PaperSummarizer": ".summarizer",
    # utils
    "HTMLTagOutputParser": ".utils",
    "arg_as_bool": ".utils",
    "send_files_to_slack": ".utils",
}

__all__ = sorted([*_EXPORTS, "logger", "is_aws_env"])


def __getattr__(name: str) -> Any:
    """Lazily import and cache a public symbol on first access (PEP 562)."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache so subsequent access is a plain attribute lookup
    return value


def __dir__() -> list[str]:
    return __all__


# Eagerly bind the logger instance + is_aws_env (cheap shim) so they are never
# shadowed by the same-named ``logger`` submodule via import-order races. This is
# deliberately placed after the module body (not at the top), so suppress import
# ordering/placement lints too.
from .logger import is_aws_env, logger  # noqa: E402, F401, I001


if TYPE_CHECKING:  # pragma: no cover - import-time hints for type checkers only
    from .aws_helpers import (
        get_cross_inference_model_id,
        get_ssm_param_value,
        submit_batch_job,
        upload_dir_to_s3,
        upload_to_s3,
        wait_for_batch_job_completion,
    )
    from .constants import (
        NULL_STRING,
        EnvVars,
        Format,
        Language,
        LanguageModelId,
        LocalPaths,
        S3Paths,
        SSMParams,
    )
    from .fetcher import Figure, Paper, PaperFetcher
    from .input_handlers import (
        ArxivInputHandler,
        BaseInputHandler,
        GenericPDFHandler,
        ParsedContent,
    )
    from .logger import is_aws_env, logger
    from .output_handlers import (
        BaseOutputHandler,
        GitHubOutputHandler,
        SlackOutputHandler,
    )
    from .renderer import HtmlToImageConverter, PaperDocumentBuilder, Result
    from .retriever import PaperRetriever, Retriever
    from .summarizer import PaperSummarizer
    from .utils import HTMLTagOutputParser, arg_as_bool, send_files_to_slack
