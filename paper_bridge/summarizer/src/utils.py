import functools
import os
import re
import time
from typing import Callable, Dict, Union, Tuple, Type, Optional
from bs4 import BeautifulSoup, NavigableString, Tag
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


def extract_text_from_html(html_content: str) -> str:
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    for tag_name in ["head", "meta", "script", "style", "title"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    def parse_element(element) -> str:
        if isinstance(element, NavigableString):
            return element.strip()

        if not isinstance(element, Tag):
            return ""

        if element.name == "img":
            alt = element.get("alt", "")
            src = element.get("src", "")
            return f"[Image: alt={alt}, src={src}]"

        if element.name == "a":
            href = element.get("href", "")
            link_text = "".join(parse_element(child) for child in element.children)
            return f"{link_text} ({href})" if href else link_text

        if element.name in ["table", "thead", "tbody", "tr", "td", "th"]:
            content = "".join(parse_element(child) for child in element.children)
            return content

        if element.name in ["code", "pre"]:
            code_content = "".join(parse_element(child) for child in element.children)
            return f"`{code_content}`"

        if element.name == "math":
            math_content = "".join(parse_element(child) for child in element.children)
            return f"$$ {math_content} $$"

        return " ".join(parse_element(child) for child in element.children)

    extracted_text = parse_element(soup)

    replacements = {"\\AND": "", "\\n": " ", "\\times": "x", "footnotemark:": ""}
    for old, new in replacements.items():
        extracted_text = extracted_text.replace(old, new)

    extracted_text = re.sub(r"\s+", " ", extracted_text).strip()

    return extracted_text


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
