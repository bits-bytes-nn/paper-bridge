from dataclasses import dataclass
from typing import ClassVar, Dict, Tuple, Type, Union
from llama_index.core.prompts import ChatMessage, ChatPromptTemplate, MessageRole
from ..constants import Language


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


class PaperSummarizationPrompt(BasePrompt):
    INPUT_VARIABLES: str = "content"
    OUTPUT_VARIABLES: Tuple[str, ...] = ("summary", "tags", "urls")
    SYSTEM_MESSAGE: str = """
    You are an expert in analyzing and summarizing AI/ML research papers. You excel at conveying complex technical
    content in a clear and structured manner while maintaining technical precision.

    Your expertise includes:
    - Identifying core concepts, innovative approaches, and experimental results with accuracy
    - Providing technical explanations that are both precise and accessible
    - Offering balanced assessments of research strengths, limitations, and trade-offs
    - Recognizing implications and potential future research directions
    - Contextualizing research within broader AI/ML trends and developments
    """
    HUMAN_MESSAGE: str = ""
    _HUMAN_MESSAGE: ClassVar[Dict[Language, str]] = {
        Language.KO: """
    Analyze and summarize the following AI/ML research paper with technical precision and clarity:

    <paper>
    {content}
    </paper>

    <Core Requirements>
    1. Extract and explain key technical concepts, methodologies, and architectural innovations
    2. Analyze the implementation details, algorithms, and technical decisions
    3. Evaluate experimental results, metrics, and their significance
    4. Identify limitations, trade-offs, and potential areas for improvement
    5. Connect the research to broader AI/ML trends and applications
    6. Maintain technical accuracy while ensuring clarity
    7. Provide a concise yet comprehensive summary (maximum 2 A4 pages)

    <Output Structure>
    1. Place the entire summary within <summary> tags
    2. Place all technical tags within <tags> tags (maximum 5 relevant technical keywords in English with Title Case format)
    3. Place all reference URLs within <urls> tags in the format: [text](url), [text](url), ...

    <Section Headers>
    Use these exact Korean section headers in your summary (do not add a title). Skip any section if the paper does not contain relevant information for that section:
    <h2>🔍 이 연구를 시작하게 된 배경과 동기는 무엇입니까?</h2>
    <h2>💡 이 연구에서 제시하는 새로운 해결 방법은 무엇입니까?</h2>
    <h2>⚙️ 제안된 방법은 어떻게 구현되었습니까?</h2>
    <h2>📊 주요 실험 결과는 무엇입니까?</h2>
    <h2>🔮 이 연구의 의의와 향후 연구 방향은 무엇입니까?</h2>

    <Formatting Guidelines>
    - Write the response in Korean, but maintain English technical terms as-is
    - Format your response in clean HTML for optimal readability
    - Use <strong> tags to emphasize key concepts
    - Use <ul> or <ol> tags for organized lists
    - Include mathematical formulas in LaTeX:
      * Use $...$ for inline equations
      * Use $$...$$ for display/block equations
    - Enhance understanding with visual elements:
      * Include relevant figures and diagrams from the paper
      * Create tables for comparative data or results
      * Use code blocks for algorithms or pseudocode:
        <pre><code>
        def example_algorithm(input):
            # Algorithm implementation
            return output
        </code></pre>
    - Include images only from local paths or https://ar5iv.labs.arxiv.org
      * Format: <img src="path/to/image.png" alt="Description" width="600">
    - Highlight key insights using callout boxes:
      <div style="background-color: #f8f9fa; border-left: 4px solid #007bff; padding: 15px; margin-bottom: 20px;">
        [Key insight content here]
      </div>

    <Content Style>
    - Present information directly and efficiently
    - Prioritize technical accuracy and clarity
    - Explain complex concepts in accessible language without oversimplification
    - Maintain an objective, analytical tone
    - Use visual elements strategically to complement textual explanations
    - Include code snippets only when they clarify algorithms or implementation details

    <Final Response Format>
    <summary>
    <html>
    <head>
      <script src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/2.7.7/MathJax.js?config=TeX-MML-AM_CHTML"></script>
      <script type="text/x-mathjax-config">
        MathJax.Hub.Config({
          tex2jax: {
            inlineMath: [['$','$']],
            displayMath: [['$$','$$']],
            processEscapes: true
          }
        });
      </script>
    </head>
    <body>
      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">🔍 이 연구를 시작하게 된 배경과 동기는 무엇입니까?</h2>
      <!-- Background and motivation content -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">💡 이 연구에서 제시하는 새로운 해결 방법은 무엇입니까?</h2>
      <!-- Novel approach and solutions content -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">⚙️ 제안된 방법은 어떻게 구현되었습니까?</h2>
      <!-- Implementation details content -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">📊 주요 실험 결과는 무엇입니까?</h2>
      <!-- Experimental results content -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">🔮 이 연구의 의의와 향후 연구 방향은 무엇입니까?</h2>
      <!-- Significance and future directions content -->
    </body>
    </html>
    </summary>
    <tags>Technical Tag One, Technical Tag Two, Technical Tag Three, Technical Tag Four, Technical Tag Five</tags>
    <urls>[GitHub Repository](repo_url), [Dataset](dataset_url), [Project Page](project_url)</urls>
    """
    }

    @classmethod
    def for_language(
        cls, language: Language = Language.KO
    ) -> Type["PaperSummarizationPrompt"]:
        prompt_class = type(
            f"{language.name.capitalize()}PaperSummarizationPrompt",
            (cls,),
            {
                "SYSTEM_MESSAGE": cls.SYSTEM_MESSAGE,
                "HUMAN_MESSAGE": cls._HUMAN_MESSAGE[language],
            },
        )
        return prompt_class


class RetrievalSummarizationPrompt(BasePrompt):
    INPUT_VARIABLES: str = "context"
    OUTPUT_VARIABLES: Tuple[str, ...] = ("summary", "urls")
    SYSTEM_MESSAGE: str = """
    You are an expert AI/ML research paper analyst specializing in comparative analysis. Your task is to analyze queries
    about AI/ML papers, assess paper content, and integrate retrieved information from knowledge graphs to provide
    comprehensive yet structured answers with precise source attribution. You excel at creating detailed technical
    comparisons while maintaining accuracy and presenting information in a visually effective format.
    """
    HUMAN_MESSAGE: str = ""

    _HUMAN_MESSAGE: ClassVar[Dict[Language, str]] = {
        Language.KO: """
    Analyze this AI/ML research paper query along with the provided paper content and knowledge graph information.
    Review all context thoroughly, including the query, paper content, search results, and sources.

    <context>
    {context}
    </context>

    <Core Requirements>
    1. Identify recent major developments in the paper's technical field
    2. Compare the paper with recently published papers to highlight key differences
    3. Support your analysis with evidence from the provided sources
    4. Maintain technical accuracy while ensuring clarity
    5. Present information in a visually structured format with proper source attribution
    6. Provide a concise analysis (maximum 1 A4 page)

    <Output Structure>
    1. Place the entire analysis within <summary> tags
    2. Place all reference URLs within <urls> tags in the format: [text](url), [text](url), ...

    <Section Headers>
    Use these exact Korean section headers in your analysis. Skip any section if there is no relevant information
    available:
    <h2>🚀 이 논문의 기술 분야에서 최근 주요 발전 방향은 무엇인가요?</h2>
    <h2>💎 최근 발표된 논문들과 이 논문의 핵심 차이점은 무엇인가요?</h2>

    <Formatting Guidelines>
    - Write the response in Korean, but maintain English technical terms as-is
    - Format your response in clean HTML for optimal readability
    - Use <strong> tags to emphasize key concepts
    - Use <ul> or <ol> tags for organized lists
    - Include mathematical formulas in LaTeX:
      * Use $...$ for inline equations
      * Use $$...$$ for display/block equations
    - Enhance understanding with visual elements:
      * Create comparison tables for technical similarities and differences:
        <table style="border-collapse: collapse; width: 100%; margin-bottom: 20px;">
          <thead>
            <tr style="background-color: #f8f9fa;">
              <th style="padding: 10px; border: 1px solid #dee2e6;">특성</th>
              <th style="padding: 10px; border: 1px solid #dee2e6;">현재 논문</th>
              <th style="padding: 10px; border: 1px solid #dee2e6;">비교 논문</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style="padding: 10px; border: 1px solid #dee2e6; text-align: left;">방법론</td>
              <td style="padding: 10px; border: 1px solid #dee2e6;">내용</td>
              <td style="padding: 10px; border: 1px solid #dee2e6;">내용</td>
            </tr>
          </tbody>
        </table>
      * Use code blocks for algorithms or pseudocode when relevant:
        <pre><code>
        def example_algorithm(input_data):
            # Process the data
            result = process(input_data)
            return result
        </code></pre>
    - Highlight key insights using callout boxes:
      <div style="background-color: #f8f9fa; border-left: 4px solid #007bff; padding: 15px; margin-bottom: 20px;">
        [Key insight content here]
      </div>

    <Source Citation>
    - Embed source references as hyperlinks directly in the text: <a href="source_url">relevant text</a>
    - Only use hyperlinks with URLs starting with http:// or https://
    - Prioritize information with higher relevance scores when available
    - Apply hyperlinks naturally within sentences to maintain reading flow

    <Content Style>
    - Present information directly and efficiently
    - Prioritize technical accuracy and clarity
    - Explain complex concepts in accessible language without oversimplification
    - Maintain an objective, analytical tone
    - Use visual elements (tables, code, equations) strategically to complement textual explanations
    - Include code snippets only when they clarify algorithms or implementation details

    <Final Response Format>
    <summary>
    <html>
    <head>
      <script src="https://cdnjs.cloudflare.com/ajax/libs/mathjax/2.7.7/MathJax.js?config=TeX-MML-AM_CHTML"></script>
      <script type="text/x-mathjax-config">
        MathJax.Hub.Config({
          tex2jax: {
            inlineMath: [['$','$']],
            displayMath: [['$$','$$']],
            processEscapes: true
          }
        });
      </script>
    </head>
    <body>
      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">🚀 이 논문의 기술 분야에서 최근 주요 발전 방향은 무엇인가요?
      </h2>
      <div>
        <!-- Recent developments analysis with hyperlinked sources -->
      </div>

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">💎 최근 발표된 논문들과 이 논문의 핵심 차이점은 무엇인가요?
      </h2>
      <div>
        <!-- Comparative analysis with hyperlinked sources and visual elements -->
      </div>
    </body>
    </html>
    </summary>
    <urls>[Related Research](https://example.com/research), [GitHub Repository](https://github.com/example/repo)</urls>
    """
    }

    @classmethod
    def for_language(
        cls, language: Language = Language.KO
    ) -> Type["RetrievalSummarizationPrompt"]:
        prompt_class = type(
            f"{language.name.capitalize()}RetrievalSummarizationPrompt",
            (cls,),
            {
                "SYSTEM_MESSAGE": cls.SYSTEM_MESSAGE,
                "HUMAN_MESSAGE": cls._HUMAN_MESSAGE[language],
            },
        )
        return prompt_class
