from dataclasses import dataclass
from typing import Tuple, Union
from llama_index.core.prompts import ChatMessage, ChatPromptTemplate, MessageRole


@dataclass(frozen=True)
class BasePrompt:
    INPUT_VARIABLES: Union[str, Tuple[str, ...]]
    OUTPUT_VARIABLES: Union[str, Tuple[str, ...]]
    SYSTEM_MESSAGE: str
    HUMAN_MESSAGE: str

    @classmethod
    def get_prompt(cls) -> ChatPromptTemplate:
        return ChatPromptTemplate(
            message_templates=[
                ChatMessage(role=MessageRole.SYSTEM, content=cls.SYSTEM_MESSAGE),
                ChatMessage(role=MessageRole.USER, content=cls.HUMAN_MESSAGE),
            ]
        )


class MainContentExtractionPrompt(BasePrompt):
    INPUT_VARIABLES = "text"
    OUTPUT_VARIABLES = ("main_content", "start_marker", "end_marker")

    SYSTEM_MESSAGE = """
    You are a specialized content extractor for English academic papers. Your task is to precisely identify the 
    boundaries of the main content and extract it accurately.

    EXTRACTION GUIDELINES:
    1. MAIN CONTENT:
       - Starts with the introduction section (typically "Introduction", "1. Introduction", etc.)
       - Ends before the references/bibliography/acknowledgments section
       - Includes all body sections (methodology, results, discussion, conclusion)

    2. BOUNDARY MARKERS:
       - START MARKER: The heading of the introduction section (including formatting)
       - END MARKER: The heading of the references/bibliography section (including formatting)
       - Each marker must be EXACTLY 20 characters including all formatting elements

    3. PRECISE MARKER EXTRACTION:
       - Include ALL formatting characters: markdown (#, *, _), whitespace, newlines, numbering
       - Count characters precisely (exactly 20 characters, no more or less)
       - If exact section can't be found, use most appropriate equivalent in English academic papers

    REQUIRED OUTPUT FORMAT:
    <main_content>
    [The complete main content of the paper]
    </main_content>

    <start_marker>[EXACTLY 20 chars]</start_marker>
    <end_marker>[EXACTLY 20 chars]</end_marker>
    """

    HUMAN_MESSAGE = """
    Extract the main content and precise boundary markers from this academic paper:

    <scratchpad>
    1. START boundary:
       - Locate the first section heading (usually "Introduction" or numbered equivalent)
       - Copy the heading and following text EXACTLY as formatted
       - Include all markdown, whitespace, newlines (\\n), special chars
       - Extract exactly 20 characters that include the heading
       - Verify marker preserves original formatting and is exactly 20 chars

    2. END boundary:
       - Locate the heading that follows the conclusion (usually "References", "Bibliography")
       - Copy the heading and following text EXACTLY as formatted
       - Include all markdown, whitespace, newlines (\\n), special chars
       - Extract exactly 20 characters that include the heading
       - Verify marker preserves original formatting and is exactly 20 chars
    </scratchpad>

    {text}
    """
