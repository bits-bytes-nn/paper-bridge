import argparse
import functools
import os
import time
from typing import Any, Callable, Dict, Union, Tuple, Type, Optional
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
            tags = soup.find_all(tag_name)
            if tags:
                parsed[tag_name] = " ".join(tag.get_text().strip() for tag in tags)

        if not parsed:
            for tag_name in tag_names:
                start = f"<{tag_name}>"
                end = f"</{tag_name}>"
                if start in text and end in text:
                    start_idx = text.find(start) + len(start)
                    end_idx = text.find(end)
                    if start_idx < end_idx:
                        content = text[start_idx:end_idx].strip()
                        parsed[tag_name] = content.replace("\n", " ")

        return (
            parsed
            if isinstance(self.tag_names, tuple)
            else next(iter(parsed.values()), "")
        )

    def format(self, query: Optional[str] = None) -> str:
        format_instructions = f"Please provide output in XML tags: {', '.join(f'<{tag_name}>' for tag_name in (self.tag_names if isinstance(self.tag_names, tuple) else [self.tag_names]))}"
        if query:
            return f"{query}\n{format_instructions}"
        return format_instructions

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


def is_aws_env() -> bool:
    aws_env_vars = [
        "AWS_BATCH_JOB_ID",
        "AWS_LAMBDA_FUNCTION_NAME",
        "ECS_CONTAINER_METADATA_URI",
    ]

    return any(env_var in os.environ for env_var in aws_env_vars)
