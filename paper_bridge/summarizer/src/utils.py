import argparse
import functools
import json
import re
import requests
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union
from bs4 import BeautifulSoup, NavigableString, Tag
from llama_index.core.types import BaseOutputParser
from .logger import logger

DEFAULT_MESSAGE: str = "This is a summary of the paper."


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
    if value is None or value.lower() == "none":
        return None

    try:
        items = json.loads(value)
        if isinstance(items, str):
            return [items.strip()]
        if isinstance(items, list):
            return items
    except json.JSONDecodeError:
        if value.strip().startswith("[") and value.strip().endswith("]"):
            try:
                import ast

                items = ast.literal_eval(value)
                if isinstance(items, list):
                    return items
            except (SyntaxError, ValueError):
                pass
        return [value.strip()]


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


def send_files_to_slack(
    file_paths: List[Path],
    slack_token: str,
    channel_id: str,
    message: str = DEFAULT_MESSAGE,
) -> None:
    headers = {
        "Authorization": f"Bearer {slack_token}",
    }

    if message:
        try:
            message_headers = {
                "Authorization": f"Bearer {slack_token}",
                "Content-Type": "application/json",
            }
            message_payload = {"channel": channel_id, "text": message}
            message_response = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers=message_headers,
                json=message_payload,
            )

            if not message_response.ok or not message_response.json().get("ok"):
                logger.error(
                    "Failed to send message: %s",
                    message_response.json().get("error", "Unknown error"),
                )
        except Exception as e:
            logger.error("Error sending message: %s", str(e))

    for file_path in file_paths:
        if not file_path.exists():
            logger.warning(f"File not found: {file_path}")
            continue

        try:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            get_url_payload = {
                "filename": file_path.name,
                "length": file_path.stat().st_size,
            }

            url_response = requests.post(
                "https://slack.com/api/files.getUploadURLExternal",
                headers=headers,
                data=get_url_payload,
            )

            logger.debug("Request payload: %s", get_url_payload)

            if not url_response.ok:
                logger.error(
                    "HTTP error: %s - %s", url_response.status_code, url_response.text
                )
                continue

            url_data = url_response.json()
            if not url_data.get("ok"):
                logger.error("Slack API error: %s", url_data.get("error"))
                continue

            upload_url = url_data.get("upload_url")
            file_id = url_data.get("file_id")

            if not upload_url or not file_id:
                logger.error("Missing upload_url or file_id in Slack response")
                continue

            with open(file_path, "rb") as f:
                file_content = f.read()

            upload_response = requests.post(
                upload_url,
                files={"file": file_content},
            )

            if not upload_response.ok:
                logger.error(
                    "Upload failed: %s - %s",
                    upload_response.status_code,
                    upload_response.text,
                )
                continue

            headers["Content-Type"] = "application/json"
            complete_payload = {
                "files": [{"id": file_id, "title": file_path.name}],
                "channel_id": channel_id,
            }

            complete_response = requests.post(
                "https://slack.com/api/files.completeUploadExternal",
                headers=headers,
                json=complete_payload,
            )

            if complete_response.ok:
                result = complete_response.json()
                if not result.get("ok"):
                    logger.error("Completion error: %s", result.get("error"))
                else:
                    logger.info("Successfully uploaded %s to Slack", file_path.name)
            else:
                logger.error(
                    "Completion HTTP error: %s - %s",
                    complete_response.status_code,
                    complete_response.text,
                )
        except Exception as e:
            logger.error("Failed to upload file %s to Slack: %s", file_path, str(e))
