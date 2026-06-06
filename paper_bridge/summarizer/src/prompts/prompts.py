from dataclasses import dataclass
from typing import ClassVar

from llama_index.core.prompts import ChatMessage, ChatPromptTemplate, MessageRole

from ..constants import Format, Language


@dataclass(frozen=True)
class BasePrompt:
    INPUT_VARIABLES: str | tuple[str, ...]
    OUTPUT_VARIABLES: str | tuple[str, ...]
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
    INPUT_VARIABLES: tuple[str, ...] = ("caption",)
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
    OUTPUT_VARIABLES: tuple[str, ...] = ("summary", "tags", "urls")
    SYSTEM_MESSAGE: str = """
    You are an expert in analyzing and summarizing AI/ML research papers. You excel at conveying complex technical
    content in a clear and structured manner while maintaining technical precision.

    Your expertise includes:
    - Identifying core concepts, innovative approaches, and experimental results
    - Providing technical explanations that are precise yet accessible
    - Assessing research strengths, limitations, and trade-offs
    - Recognizing implications and future research directions
    - Contextualizing research within broader AI/ML developments
    """
    HUMAN_MESSAGE: str = ""
    _HUMAN_MESSAGE: ClassVar[dict[Language, str]] = {
        Language.EN: """
    Analyze and summarize the following AI/ML research paper with technical precision and clarity:

    <paper>
    {content}
    </paper>

    <Core Requirements>
    1. Extract key technical concepts, methodologies, and architectural innovations
    2. Analyze implementation details and technical decisions
    3. Highlight the most significant experimental results
    4. Identify limitations and potential improvements
    5. Connect the research to broader AI/ML applications
    6. Provide a concise summary (maximum 2 A4 pages, approximately 2000 characters)
    7. Include relevant figures to enhance understanding

    <Important Note>
    Select only essential visual elements (images, tables, code) that are critical for understanding key concepts.

    <Focus Distribution>
    - Provide DETAILED summaries of the novel solution and implementation methods (sections 2 and 3)
    - Provide BRIEF summaries of the background/motivation, experimental results, and future directions (sections 1, 4,
    and 5)
    - For brief summary sections (1, 4, 5), prefer text-based explanations over images, tables, formulas, or code

    <Output Structure>
    1. Place the entire summary within <summary> tags
    2. Place all technical tags within <tags> tags (maximum 5 relevant technical keywords in Title Case)
    3. Place all reference URLs within <urls> tags as [text](url), [text](url), ...

    <Section Headers>
    Use these exact section headers (skip sections without relevant information):
    <h2>🔍 What motivated this research?</h2> [BRIEF SUMMARY - prefer text over images/tables/formulas/code]
    <h2>💡 What novel solution does this research propose?</h2> [DETAILED SUMMARY]
    <h2>⚙️ How was the proposed method implemented?</h2> [DETAILED SUMMARY]
    <h2>📊 What are the key experimental results?</h2> [BRIEF SUMMARY - prefer text over images/tables/formulas/code]
    <h2>🔮 What is the significance and future direction of this research?</h2> [BRIEF SUMMARY - prefer text over
    images/tables/formulas/code]

    <Formatting Guidelines>
    - Format your response in clean HTML for optimal readability
    - Use <strong> tags for key concepts and <ul>/<ol> tags for lists
    - Include mathematical formulas in LaTeX ($...$ for inline, $$...$$ for display)
    - IMPORTANT: Avoid using LaTeX environments that start with \\begin{...} as they may break. Instead:
      * For matrices, use array environments:
      $$\\left[ \\begin{array}{ccc} a & b & c \\\\ d & e & f \\end{array} \\right]$$
      * For aligned equations, use aligned notation with &: $$a = b \\\\ c = d$$
      * For complex math structures, break them into multiple display equations
    - Enhance understanding with visual elements:
      * Include relevant figures from the paper to illustrate key concepts
      * Use tables for comparative data
      * Use code blocks for algorithms:
        <pre><code>
        def example_algorithm(input):
            # Algorithm implementation
            return output
        </code></pre>
    - Image inclusion guidelines:
      * WARNING: Do NOT confuse local paths with external URLs!
      * If an image path starts with '/' like '/path/to/image.png', it is a local path. Keep it exactly as is:
        <img src="/path/to/image.png" alt="Description" width="600">
      * NEVER add 'https://arxiv.org/html' to local paths
      * ONLY use complete URLs when specifically referencing ar5iv images:
        <img src="https://arxiv.org/html/path/to/image.png" alt="Description" width="600">
      * Clearly distinguish between local paths and external URLs when inserting images
    - Highlight insights using callout boxes:
      <div style="background-color: #f8f9fa; border-left: 4px solid #007bff; padding: 15px; margin-bottom: 20px;">
        [Key insight]
      </div>
    - Do NOT use \bm{} command as it may break rendering
    - Instead, use \boldsymbol{} for bold symbols: $\boldsymbol{\alpha}$ instead of $\bm{\alpha}$
    - For simple variables, \\mathbf{} can also be used: $\\mathbf{A}$ for matrices
    - For vectors, consider using arrow notation: $\vec{v}$ or explicit formatting: $\boldsymbol{v}$

    <Content Style>
    - Prioritize technical accuracy and clarity
    - Explain complex concepts accessibly without oversimplification
    - Focus on core results rather than detailed metrics
    - Balance text and visuals for optimal comprehension
    - Reference figures in your text (e.g., "As shown in Figure 1...")

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
      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">🔍 What motivated this research?</h2>
      <!-- Brief background and motivation with relevant image -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">💡 What novel solution does this research
      propose?</h2>
      <!-- Detailed coverage of novel approach with architecture diagram -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">⚙️ How was the proposed method implemented?
      </h2>
      <!-- Detailed coverage of implementation details with method diagram -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">📊 What are the key experimental results?</h2>
      <!-- Brief summary of key results with selected charts/graphs -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">🔮 What is the significance and future
      direction of this research?</h2>
      <!-- Brief summary of significance and future directions -->
    </body>
    </html>
    </summary>
    <tags>Technical Tag One, Technical Tag Two, Technical Tag Three, Technical Tag Four, Technical Tag Five</tags>
    <urls>[GitHub Repository](repo_url), [Dataset](dataset_url), [Project Page](project_url)</urls>
        """,
        Language.KO: """
    Analyze and summarize the following AI/ML research paper with technical precision and clarity:

    <paper>
    {content}
    </paper>

    <Core Requirements>
    1. Extract key technical concepts, methodologies, and architectural innovations
    2. Analyze implementation details and technical decisions
    3. Highlight the most significant experimental results
    4. Identify limitations and potential improvements
    5. Connect the research to broader AI/ML applications
    6. Provide a concise summary (maximum 2 A4 pages, approximately 2000 characters)
    7. Include relevant figures to enhance understanding

    <Important Note>
    Select only essential visual elements (images, tables, code) that are critical for understanding key concepts.

    <Focus Distribution>
    - Provide DETAILED summaries of the novel solution and implementation methods (sections 2 and 3)
    - Provide BRIEF summaries of the background/motivation, experimental results, and future directions
    (sections 1, 4, and 5)
    - For brief summary sections (1, 4, 5), prefer text-based explanations over images, tables, formulas, or code

    <Output Structure>
    1. Place the entire summary within <summary> tags
    2. Place all technical tags within <tags> tags (maximum 5 relevant technical keywords in English with Title Case)
    3. Place all reference URLs within <urls> tags as [text](url), [text](url), ...

    <Section Headers>
    Use these exact Korean section headers (skip sections without relevant information):
    <h2>🔍 이 연구를 시작하게 된 배경과 동기는 무엇입니까?</h2> [간략한 요약, 이미지/표/수식/코드 대신 텍스트 권장]
    <h2>💡 이 연구에서 제시하는 새로운 해결 방법은 무엇입니까?</h2> [상세한 요약]
    <h2>⚙️ 제안된 방법은 어떻게 구현되었습니까?</h2> [상세한 요약]
    <h2>📊 주요 실험 결과는 무엇입니까?</h2> [간략한 요약, 이미지/표/수식/코드 대신 텍스트 권장]
    <h2>🔮 이 연구의 의의와 향후 연구 방향은 무엇입니까?</h2> [간략한 요약, 이미지/표/수식/코드 대신 텍스트 권장]

    <Formatting Guidelines>
    - Write in Korean, keeping English technical terms as-is
    - End every sentence with a period (.), never a colon (:). Korean prose does
      not use a colon to end a sentence; rephrase so sentences close naturally.
    - Format your response in clean HTML for optimal readability
    - Use <strong> tags for key concepts and <ul>/<ol> tags for lists
    - Include mathematical formulas in LaTeX ($...$ for inline, $$...$$ for display)
    - IMPORTANT: Avoid using LaTeX environments that start with \\begin{...} as they may break. Instead:
      * For matrices, use array environments: $$\\left[ \\begin{array}{ccc} a & b & c \\\\ d & e & f \\end{array} 
      \\right]$$
      * For aligned equations, use aligned notation with &: $$a = b \\\\ c = d$$
      * For complex math structures, break them into multiple display equations
      * If complex environments are absolutely necessary, use \\begin{{aligned}} with double braces
    - Enhance understanding with visual elements:
      * Include relevant figures from the paper to illustrate key concepts
      * Use tables for comparative data
      * Use code blocks for algorithms:
        <pre><code>
        def example_algorithm(input):
            # Algorithm implementation
            return output
        </code></pre>
    - Image inclusion guidelines:
      * WARNING: Do NOT confuse local paths with external URLs!
      * If an image path starts with '/' like '/path/to/image.png', it is a local path. Keep it exactly as is:
        <img src="/path/to/image.png" alt="Description" width="600">
      * NEVER add 'https://arxiv.org/html' to local paths
      * ONLY use complete URLs when specifically referencing ar5iv images:
        <img src="https://arxiv.org/html/path/to/image.png" alt="Description" width="600">
      * Clearly distinguish between local paths and external URLs when inserting images
    - Highlight insights using callout boxes:
      <div style="background-color: #f8f9fa; border-left: 4px solid #007bff; padding: 15px; margin-bottom: 20px;">
        [Key insight]
      </div>
    - Do NOT use \bm{} command as it may break rendering
    - Instead, use \boldsymbol{} for bold symbols: $\boldsymbol{\alpha}$ instead of $\bm{\alpha}$
    - For simple variables, \\mathbf{} can also be used: $\\mathbf{A}$ for matrices
    - For vectors, consider using arrow notation: $\vec{v}$ or explicit formatting: $\boldsymbol{v}$

    <Content Style>
    - Prioritize technical accuracy and clarity
    - Explain complex concepts accessibly without oversimplification
    - Focus on core results rather than detailed metrics
    - Balance text and visuals for optimal comprehension
    - Reference figures in your text (e.g., "그림 1에서 보는 바와 같이...")

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
      <!-- Brief background and motivation with relevant image -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">💡 이 연구에서 제시하는 새로운 해결 방법은 무엇입니까?</h2>
      <!-- Detailed coverage of novel approach with architecture diagram -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">⚙️ 제안된 방법은 어떻게 구현되었습니까?</h2>
      <!-- Detailed coverage of implementation details with method diagram -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">📊 주요 실험 결과는 무엇입니까?</h2>
      <!-- Brief summary of key results with selected charts/graphs -->

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">🔮 이 연구의 의의와 향후 연구 방향은 무엇입니까?</h2>
      <!-- Brief summary of significance and future directions -->
    </body>
    </html>
    </summary>
    <tags>Technical Tag One, Technical Tag Two, Technical Tag Three, Technical Tag Four, Technical Tag Five</tags>
    <urls>[GitHub Repository](repo_url), [Dataset](dataset_url), [Project Page](project_url)</urls>
    """,
    }

    @classmethod
    def for_language(
        cls, language: Language = Language.KO
    ) -> type["PaperSummarizationPrompt"]:
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
    OUTPUT_VARIABLES: tuple[str, ...] = ("summary", "urls")
    SYSTEM_MESSAGE: str = """
    You are an expert AI/ML research paper analyst specializing in comparative analysis. Your task is to analyze queries
    about AI/ML papers, assess paper content, and integrate retrieved information from knowledge graphs to provide
    comprehensive yet structured answers with precise source attribution. You excel at creating detailed technical
    comparisons while maintaining accuracy and presenting information in a visually effective format.
    """
    HUMAN_MESSAGE: str = ""

    _HUMAN_MESSAGE: ClassVar[dict[Language, dict[Format, str]]] = {
        Language.EN: {
            Format.HTML: """
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
    6. Provide a concise analysis (maximum 1 A4 page, approximately 1000 characters)

    <Output Structure>
    1. Place the entire analysis within <summary> tags
    2. Place all reference URLs within <urls> tags in the format: [text](url), [text](url), ...

    <Section Headers>
    Use these exact English section headers in your analysis. Skip any section if there is no relevant information
    available:
    <h2>🚀 What are the recent major developments in the specific technical field of this paper?</h2>
    <h2>💎 What are the key differences between this paper and recent papers that aim to solve similar problems?</h2>

    <Formatting Guidelines>
    - Format your response in clean HTML for optimal readability
    - Use <strong> tags to emphasize key concepts
    - Use <ul> or <ol> tags for organized lists
    - Include mathematical formulas in LaTeX:
    - Use $...$ for inline equations
    - Use $$...$$ for display/block equations
    - Enhance understanding with visual elements:
    - Create comparison tables for technical similarities and differences:
        <table style="border-collapse: collapse; width: 100%; margin-bottom: 20px;">
        <thead>
            <tr style="background-color: #f8f9fa;">
            <th style="padding: 10px; border: 1px solid #dee2e6;">Feature</th>
            <th style="padding: 10px; border: 1px solid #dee2e6;">Current Paper</th>
            <th style="padding: 10px; border: 1px solid #dee2e6;">Comparison Paper</th>
            </tr>
        </thead>
        <tbody>
            <tr>
            <td style="padding: 10px; border: 1px solid #dee2e6; text-align: left;">Methodology</td>
            <td style="padding: 10px; border: 1px solid #dee2e6;">Content</td>
            <td style="padding: 10px; border: 1px solid #dee2e6;">Content</td>
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
    <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">
    🚀 What are the recent major developments in the specific technical field of this paper?
    </h2>
    <div>
        <!-- Recent developments analysis with hyperlinked sources -->
    </div>

    <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">💎 What are the key differences between this
    paper and recently published papers?
    </h2>
    <div>
        <!-- Comparative analysis with hyperlinked sources and visual elements -->
    </div>
    </body>
    </html>
    </summary>
    <urls>[Related Research](https://example.com/research), [GitHub Repository](https://github.com/example/repo)</urls>
    """,
            Format.SLACK: """
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
    6. Provide a concise analysis (maximum 1 A4 page, approximately 1000 characters)

    <Output Structure>
    1. Place the entire analysis within <summary> tags
    2. Place all reference URLs within <urls> tags in the format: [text](url), [text](url), ...

    <Section Headers>
    Use these exact English section headers in your analysis. Skip any section if there is no relevant information
    available:
    *🚀 What are the recent major developments in the specific technical field of this paper?*
    *💎 What are the key differences between this paper and recent papers that aim to solve similar problems?*

    <Formatting Guidelines for Slack - VERY IMPORTANT>
    - Format your response in Slack markdown for optimal readability
    - Use _text_ for italic emphasis
    - Use *text* for bold emphasis
    - Use ``` for code blocks
    - Use > for blockquotes or callouts
    - For bullet points, use • followed by a space (e.g., • text)
    - For numbered lists, start a new line and use "1. ", "2. ", "3. " (note the space after the period)
    - For mathematical equations, use plain text approximations when possible
    - IMPORTANT: DO NOT USE PIPE TABLES IN SLACK - they render incorrectly

    - For comparison data, strictly use this format instead:
      *Feature 1*
      • Current paper: [details]
      • Comparison paper: [details]

      *Feature 2*
      • Current paper: [details]
      • Comparison paper: [details]

    <Source Citation and Hyperlink Formatting>
    - Use standard markdown format for hyperlinks: [Display Text](https://example.com)
    - Always include the full URL starting with https://
    - Format citations as natural parts of the text with markdown links
    - Examples:
      [Latest Diffusion Models](https://arxiv.org/abs/2101.12345)
      [Code Repository](https://github.com/user/repo)

    - When citing within text:
      Recent work by Smith et al. ["Advances in Diffusion Models"](https://arxiv.org/abs/2101.12345) shows...

    <Content Style>
    - Present information directly and efficiently
    - Prioritize technical accuracy and clarity
    - Explain complex concepts in accessible language without oversimplification
    - Maintain an objective, analytical tone
    - Include code snippets only when they clarify algorithms or implementation details
    - DO NOT add number references in square brackets [1] after citations - use only hyperlinks

    <Final Response Format>
    <summary>
    *🚀 What are the recent major developments in the specific technical field of this paper?*

    • Development 1: Description with [properly formatted citation](https://example.com).
    • Development 2: Description with [properly formatted citation](https://example.com).

    *💎 What are the key differences between this paper and recent papers that aim to solve similar problems?*

    *Key Difference 1*
    • Current paper: Description with [properly formatted citation](https://example.com).
    • Comparison paper: Description with [properly formatted citation](https://example.com).

    *Key Difference 2*
    • Current paper: Description with [properly formatted citation](https://example.com).
    • Comparison paper: Description with [properly formatted citation](https://example.com).
    </summary>
    <urls>[Paper Title 1](https://arxiv.org/abs/2401.00000), [GitHub Repository](https://github.com/example/repo)</urls>
    """,
        },
        Language.KO: {
            Format.HTML: """
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
    6. Provide a concise analysis (maximum 1 A4 page, approximately 1000 characters)

    <Output Structure>
    1. Place the entire analysis within <summary> tags
    2. Place all reference URLs within <urls> tags in the format: [text](url), [text](url), ...

    <Section Headers>
    Use these exact Korean section headers in your analysis. Skip any section if there is no relevant information
    available:
    <h2>🚀 이 논문의 세부 기술 분야에서 최근 주요 발전 방향은 무엇인가요?</h2>
    <h2>💎 비슷한 문제를 해결하고자 하는 최근 논문들과 이 논문의 핵심 차이점은 무엇인가요?</h2>

    <Formatting Guidelines>
    - Write the response in Korean, but maintain English technical terms as-is
    - End every sentence with a period (.), never a colon (:). Korean prose does
      not use a colon to end a sentence; rephrase so sentences close naturally.
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
      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">
      🚀 이 논문의 세부 기술 분야에서 최근 주요 발전 방향은 무엇인가요?
      </h2>
      <div>
        <!-- Recent developments analysis with hyperlinked sources -->
      </div>

      <h2 style="color: #2c3e50; margin-top: 25px; margin-bottom: 15px;">
      💎 비슷한 문제를 해결하고자 하는 최근 논문들과 이 논문의 핵심 차이점은 무엇인가요?
      </h2>
      <div>
        <!-- Comparative analysis with hyperlinked sources and visual elements -->
      </div>
    </body>
    </html>
    </summary>
    <urls>[Related Research](https://example.com/research), [GitHub Repository](https://github.com/example/repo)</urls>
    """,
            Format.SLACK: """
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
    6. Provide a concise analysis (maximum 1 A4 page, approximately 1000 characters)

    <Output Structure>
    1. Place the entire analysis within <summary> tags
    2. Place all reference URLs within <urls> tags in the format: [text](url), [text](url), ...

    <Section Headers>
    Use these exact Korean section headers in your analysis. Skip any section if there is no relevant information
    available:
    *🚀 이 논문의 세부 기술 분야에서 최근 주요 발전 방향은 무엇인가요?*
    *💎 비슷한 문제를 해결하고자 하는 최근 논문들과 이 논문의 핵심 차이점은 무엇인가요?*

    <Formatting Guidelines for Slack - VERY IMPORTANT>
    - Write the response in Korean, but maintain English technical terms as-is
    - End every sentence with a period (.), never a colon (:). Korean prose does
      not use a colon to end a sentence; rephrase so sentences close naturally.
    - Format your response in Slack markdown for optimal readability
    - Use _text_ for italic emphasis
    - Use *text* for bold emphasis
    - Use ``` for code blocks
    - Use > for blockquotes or callouts
    - For bullet points, use • followed by a space (e.g., • text)
    - For numbered lists, use numbers followed by a period and space (e.g., 1. text, 2. text, 3. text)
    - For mathematical equations, use plain text approximations when possible
    - IMPORTANT: DO NOT USE PIPE TABLES IN SLACK - they render incorrectly

    - For comparison data, strictly use this format instead:
      *특성 1*
      • 현재 논문: [세부 내용]
      • 비교 논문: [세부 내용]

    <Source Citation and Hyperlink Formatting>
    - Use standard markdown format for hyperlinks: [Display Text](https://example.com)
    - Always include the full URL starting with https://
    - Format citations as natural parts of the text with markdown links
    - Examples:
      [최신 디퓨전 모델](https://arxiv.org/abs/2101.12345)
      [코드 저장소](https://github.com/user/repo)

    - When citing within text:
      Smith 등의 최근 연구 ["디퓨전 모델의 발전"](https://arxiv.org/abs/2101.12345)에서는...

    <Content Style>
    - Present information directly and efficiently
    - Prioritize technical accuracy and clarity
    - Explain complex concepts in accessible language without oversimplification
    - Maintain an objective, analytical tone
    - Include code snippets only when they clarify algorithms or implementation details
    - DO NOT add number references in square brackets [1] after citations - use only hyperlinks

    <Final Response Format>
    <summary>
    *🚀 이 논문의 세부 기술 분야에서 최근 주요 발전 방향은 무엇인가요?*

    • 발전 방향 1: [올바르게 형식이 지정된 인용](https://example.com)과 함께하는 설명.
    • 발전 방향 2: [올바르게 형식이 지정된 인용](https://example.com)과 함께하는 설명.

    *💎 비슷한 문제를 해결하고자 하는 최근 논문들과 이 논문의 핵심 차이점은 무엇인가요?*

    *핵심 차이점 1*
    • 현재 논문: [올바르게 형식이 지정된 인용](https://example.com)과 함께하는 설명.
    • 비교 논문: [올바르게 형식이 지정된 인용](https://example.com)과 함께하는 설명.

    *핵심 차이점 2*
    • 현재 논문: [올바르게 형식이 지정된 인용](https://example.com)과 함께하는 설명.
    • 비교 논문: [올바르게 형식이 지정된 인용](https://example.com)과 함께하는 설명.
    </summary>
    <urls>[논문 제목 1](https://arxiv.org/abs/2401.00000), [GitHub 저장소](https://github.com/example/repo)</urls>
    """,
        },
    }

    @classmethod
    def for_language_and_format(
        cls, language: Language = Language.KO, output_format: Format = Format.HTML
    ) -> type["RetrievalSummarizationPrompt"]:
        class_name = f"{language.name.capitalize()}{output_format.name.capitalize()}RetrievalSummarizationPrompt"
        prompt_class = type(
            class_name,
            (cls,),
            {
                "SYSTEM_MESSAGE": cls.SYSTEM_MESSAGE,
                "HUMAN_MESSAGE": cls._HUMAN_MESSAGE[language][output_format],
            },
        )
        return prompt_class
