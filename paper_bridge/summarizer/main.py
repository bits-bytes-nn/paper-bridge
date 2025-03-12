import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pprint import pformat
from typing import Any, Dict, List, Optional, Union
import boto3
from paper_bridge.summarizer.configs import Config
from paper_bridge.summarizer.src import (
    EnvVars,
    Figure,
    PaperFetcher,
    Retriever,
    arg_as_bool,
    get_ssm_param_value,
    logger,
)

DEFAULT_QUERIES: List[str] = [
    "Please analyze the prior research similar to the paper below, and provide a detailed description in Korean of the key technical similarities and differences between these previous studies and this research.\nContext:"
]


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Union[int, str]]:
    boto3_session = None
    target_date = None

    try:
        config = Config.load()
        profile_name = EnvVars.AWS_PROFILE_NAME.value

        target_date_str = event.get("TARGET_DATE")
        days_to_fetch = event.get("DAYS_TO_FETCH")
        parse_pdf = event.get("PARSE_PDF", False)

        logger.info("PDF parsing mode: %s", "enabled" if parse_pdf else "disabled")

        target_date = parse_target_date(target_date_str)

        boto3_session = boto3.Session(
            region_name=config.resources.default_region_name,
            profile_name=profile_name,
        )

        project_name = config.resources.project_name
        stage = config.resources.stage

        neptune_endpoint = get_ssm_param_value(
            boto3_session, f"/{project_name}-{stage}/neptune/endpoint"
        )
        opensearch_endpoint = get_ssm_param_value(
            boto3_session, f"/{project_name}-{stage}/opensearch/endpoint"
        )

        if neptune_endpoint is None or opensearch_endpoint is None:
            raise ValueError(
                "Neptune or OpenSearch endpoint not found in SSM parameters"
            )

        fetcher = PaperFetcher(config)
        papers = fetcher.fetch_papers_for_date_range(target_date, days_to_fetch)
        flattened_papers = [
            paper for papers_list in papers.values() for paper in papers_list
        ]

        enriched_papers = []
        for paper in flattened_papers:
            enriched_content = _enrich_content_with_figures(
                paper.content, paper.figures, parse_pdf
            )
            paper.content = enriched_content
            enriched_papers.append(paper)

        logger.info(f"Found {len(enriched_papers)} papers to process")
        logger.debug("Paper details: %s", pformat(enriched_papers))

        retriever = Retriever(config, boto3_session)

        responses = []
        for paper in enriched_papers:
            for query in DEFAULT_QUERIES:
                query_text = query + f"\nContext: {paper.content}"
                response = retriever.query(query_text=query_text)
                responses.append({"query": query_text, "response": response})

        logger.debug("RAG Responses: %s", pformat(responses))

        # # Use Claude 3.7 Sonnet to generate enhanced response
        # bedrock_client = boto3_session.client(
        #     service_name="bedrock-runtime",
        #     region_name=config.resources.bedrock_region_name,
        # )

        # prompt = f"""
        # Paper content: {paper.content}

        # Retrieved context: {retrieval_results}

        # Based on the paper and retrieved information, provide a comprehensive analysis
        # of the key contributions, methodology, and findings of this research paper.
        # Format your response in HTML with appropriate headings and structure.
        # """

        # response = bedrock_client.invoke_model(
        #     modelId="anthropic.claude-3-7-sonnet-20240229-v1:0",
        #     contentType="application/json",
        #     accept="application/json",
        #     body=json.dumps(
        #         {
        #             "anthropic_version": "bedrock-2023-05-31",
        #             "max_tokens": 4096,
        #             "messages": [{"role": "user", "content": prompt}],
        #             "temperature": 0.2,
        #         }
        #     ),
        # )

        # response_body = json.loads(response["body"].read())
        # html_content = response_body["content"][0]["text"]

        # rag_results.append(
        #     {
        #         "paper_id": paper.id,
        #         "title": paper.title,
        #         "html_content": html_content,
        #         "retrieval_results": retrieval_results,
        #     }
        # )

        # # 요약 생성 (html 형태)
        # # rag 결과와 요약 합쳐서 html 렌더링 -> 옵션에 따라 jpg 변환 -> s3 저장 (이미지도 함께)
        # # 슬랙으로 메시지 전달

        return {"status": 200, "message": "Success"}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {"status": 500, "message": str(e)}


def parse_target_date(date_str: Optional[str]) -> datetime:
    if not date_str:
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc) - timedelta(days=1)

    try:
        return datetime.strptime(date_str, "%Y-%m-%d").astimezone(timezone.utc)
    except ValueError as e:
        logger.error(f"Invalid date format: {e}")
        sys.exit(1)


def _enrich_content_with_figures(
    text: str, figures: List[Figure], parse_pdf: bool
) -> str:
    if not figures:
        return text

    image_pattern = r"\[Image:\s*alt=(.*?),\s*src=(.*?)\]"
    enriched_text = text

    if parse_pdf:
        matches = list(re.finditer(image_pattern, text))
        if len(matches) != len(figures):
            logger.warning(
                "Number of image placeholders (%s) does not match "
                "number of figures (%s)",
                len(matches),
                len(figures),
            )
            return text

        for match, figure in zip(reversed(matches), reversed(figures)):
            alt = match.group(1)
            replacement = (
                f"[Image: alt={alt}, src={str(figure.path)}, caption={figure.analysis}]"
            )
            start, end = match.span()
            enriched_text = enriched_text[:start] + replacement + enriched_text[end:]

    else:
        for figure in figures:
            figure_path = str(figure.path)
            figure_filename = Path(figure_path).name
            for match in re.finditer(image_pattern, enriched_text):
                src = match.group(2)
                if Path(src).name == figure_filename:
                    alt = match.group(1)
                    replacement = f"[Image: alt={alt}, src={figure_path}, caption={figure.analysis}]"
                    start, end = match.span()
                    enriched_text = (
                        enriched_text[:start] + replacement + enriched_text[end:]
                    )

    return enriched_text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Paper Bridge: A service that curates and summarizes arXiv papers daily"
    )
    parser.add_argument(
        "--target-date",
        type=str,
        help="Target date in 'YYYY-MM-DD' format",
        default=None,
    )
    parser.add_argument(
        "--days-to-fetch",
        type=int,
        help="Number of days to fetch papers for",
        default=None,
    )
    parser.add_argument(
        "--parse-pdf",
        type=arg_as_bool,
        help="Parse PDF instead of HTML content if set to true",
        default=False,
    )

    args = parser.parse_args()
    event = {
        "TARGET_DATE": args.target_date,
        "DAYS_TO_FETCH": args.days_to_fetch,
        "PARSE_PDF": args.parse_pdf,
    }

    result = lambda_handler(event, None)
    exit_code = 0 if result["status"] == 200 else 1
    sys.exit(exit_code)
