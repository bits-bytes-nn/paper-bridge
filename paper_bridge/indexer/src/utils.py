import argparse
import functools
import time
from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup
from llama_index.core.types import BaseOutputParser

from .logger import logger


class HTMLTagOutputParser(BaseOutputParser):
    def __init__(self, tag_names: str | tuple[str, ...], verbose: bool = False):
        self.tag_names = tag_names
        self.verbose = verbose

    def parse(self, text: str) -> str | dict[str, str]:
        if not text:
            return {} if isinstance(self.tag_names, tuple) else ""

        if self.verbose:
            logger.debug("Parsing text: %s", text)

        soup = BeautifulSoup(text, "html.parser")
        parsed: dict[str, str] = {}

        tag_names = (
            self.tag_names if isinstance(self.tag_names, tuple) else [self.tag_names]
        )
        for tag_name in tag_names:
            if tag := soup.find(tag_name):
                parsed[tag_name] = str(tag.decode_contents(formatter=None)).strip()

        return (
            parsed
            if isinstance(self.tag_names, tuple)
            else next(iter(parsed.values()), "")
        )

    @property
    def output_type(self) -> type[str | dict[str, str]]:
        return dict[str, str] if isinstance(self.tag_names, tuple) else str


def arg_as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        value = value.lower().strip()
        if value in ("yes", "true", "t", "y", "1"):
            return True
        if value in ("no", "false", "f", "n", "0"):
            return False

    raise argparse.ArgumentTypeError("Boolean value expected")


def measure_execution_time(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        execution_time = time.time() - start_time
        msg = f"Total execution time: {execution_time:.2f} seconds ({execution_time/60:.2f} minutes)"
        logger.info(msg)
        return result

    return wrapper
