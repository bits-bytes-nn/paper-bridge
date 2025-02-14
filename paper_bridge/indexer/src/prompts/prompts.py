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
    OUTPUT_VARIABLES = ("start_marker", "end_marker")

    SYSTEM_MESSAGE = """
    You are an academic paper content extraction specialist. Your task is to identify and extract the main content 
    sections of academic papers with these specific rules:

    Main Content Definition:
    1. START boundary must be one of:
        - First character of "1. Introduction" or equivalent section
        - First character of "Introduction" if no numbering
        - First character of paper body if no explicit introduction

    2. END boundary must be:
        - Last character before References/Bibliography
        - Last character before Acknowledgments if present
        - Last character of Discussion/Conclusion

    3. Explicit exclusions:
        - Title, authors, affiliations
        - Abstract, keywords
        - References, citations
        - Acknowledgments
        - Appendices
        - Supplementary materials

    Character-Level Extraction Rules:
    - Each marker must be EXACTLY 20 characters
    - Count and include ALL characters:
        * Whitespace (spaces, tabs)
        * Newlines (\n)
        * Punctuation marks (.,;:!?)
        * Special characters (@#$%&*)
        * Brackets/parentheses ([{<>}])
        * Any Unicode characters
    - Preserve original formatting completely
    - No pattern matching or assumptions

    Required Process:
    1. First use <scratchpad> tags to:
        - Copy the full paragraph containing each candidate boundary
        - Underline (with _) the exact boundary position
        - For chosen boundaries, show:
            * Full paragraph context
            * Marker extraction with character-by-character count
            * Verification of 20 char total
    2. Only then output final XML markers

    Validation Steps:
    1. Locate precise section boundaries
    2. Copy full paragraphs to verify context
    3. Count characters individually, including ALL special chars
    4. Double-check marker length is exactly 20
    5. Validate XML tags
    """

    HUMAN_MESSAGE = """
    Extract the main content boundaries from this academic paper using these precise steps:

    First, show your work in <scratchpad> tags:
    1. For START boundary:
        a) Copy full paragraph containing "Introduction" or equivalent
        b) Underline (_) the exact starting position
        c) Show the next 20 characters with count: [char1]|[char2]|...|[char20]
        d) Verify count = 20

    2. For END boundary:
        a) Copy full paragraph before References/Acknowledgments
        b) Underline (_) the exact ending position
        c) Show the previous 20 characters with count: [char1]|[char2]|...|[char20]
        d) Verify count = 20

    Then extract markers:
    START marker:
    1. Find exact starting point using boundary rules
    2. Extract next 20 characters verbatim including ALL special chars
    3. Verify in source text
    4. Format as <start_marker>...</start_marker>

    END marker:
    1. Find exact ending point using boundary rules
    2. Extract previous 20 characters verbatim including ALL special chars
    3. Verify in source text
    4. Format as <end_marker>...</end_marker>

    Required Validations:
    - Markers are exactly 20 characters (including ALL special chars)
    - Content exists verbatim in source text
    - Proper XML formatting
    - No example text used

    Input Paper:
    {text}

    Important: Show complete paragraphs in <scratchpad> first, then return ONLY the XML marker tags. No other 
    explanations.
    """
