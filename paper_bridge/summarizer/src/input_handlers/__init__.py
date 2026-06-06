"""Input handlers for processing different paper sources."""

from .arxiv_handler import ArxivInputHandler
from .base import BaseInputHandler, ParsedContent
from .generic_handler import GenericPDFHandler

__all__ = [
    "BaseInputHandler",
    "ParsedContent",
    "ArxivInputHandler",
    "GenericPDFHandler",
]
