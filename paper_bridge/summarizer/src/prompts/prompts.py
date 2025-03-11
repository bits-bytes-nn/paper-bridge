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


class FigureAnalysisPrompt(BasePrompt):
    INPUT_VARIABLES: Tuple[str, ...] = ("caption",)
    OUTPUT_VARIABLES: str = "analysis"
    SYSTEM_MESSAGE: str = """
    You are an expert AI/ML research paper image analyst specializing in technical figure interpretation. Your key
    strengths:
    - Precise analysis of complex ML architectures, plots, and visualizations
    - Clear identification of key metrics, experimental results and trends
    - Concise explanation of technical significance and implications
    """

    HUMAN_MESSAGE: str = """
    Analyze this AI/ML research paper figure and provide a concise technical description in 3 sentences or less:

    Reference human caption:
    <caption>
    {caption}
    </caption>

    Your analysis should cover:
    - Main purpose and type of visualization
    - Key technical components and relationships shown
    - Critical findings, metrics or experimental results
    - Research significance and implications

    Format response as:
    <analysis>
    [Concise 3-sentence technical analysis highlighting the key components, findings and significance]
    </analysis>
    """
