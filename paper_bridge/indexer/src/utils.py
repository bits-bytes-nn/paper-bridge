import argparse
import ast
import functools
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union
from bs4 import BeautifulSoup
from llama_index.core.types import BaseOutputParser
from .logger import logger


class HTMLTagOutputParser(BaseOutputParser):
    def __init__(self, tag_names: Union[str, Tuple[str, ...]], verbose: bool = False):
        self.tag_names = tag_names
        self.verbose = verbose

    def parse(self, text: str) -> Union[str, Dict[str, str]]:
        if not text:
            return {} if isinstance(self.tag_names, tuple) else ""

        if self.verbose:
            logger.debug("Parsing text: %s", text)

        soup = BeautifulSoup(text, "html.parser")
        parsed: Dict[str, str] = {}

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
    def output_type(self) -> Type[Union[str, Dict[str, str]]]:
        return Dict[str, str] if isinstance(self.tag_names, tuple) else str


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


def arg_as_list(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None

    try:
        urls = json.loads(value)
        if isinstance(urls, str):
            return [urls.strip()]
        if isinstance(urls, list):
            return urls
    except json.JSONDecodeError:
        if value.strip().startswith("[") and value.strip().endswith("]"):
            try:
                urls = ast.literal_eval(value)
                if isinstance(urls, list):
                    return urls
            except (SyntaxError, ValueError):
                pass
        return [value.strip()]


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
