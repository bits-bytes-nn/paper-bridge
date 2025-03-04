from pprint import pformat
from paper_bridge.indexer.configs import Config
from paper_bridge.indexer.src import (
    EnvVars,
    PaperFetcher,
    logger,
    run_extract_and_build,
)

if __name__ == "__main__":
    config = Config.load()
    profile_name = EnvVars.AWS_PROFILE_NAME.value

    fetcher = PaperFetcher(config)
    papers = fetcher.fetch_papers_for_date_range()
    logger.info("Papers: %s", pformat(papers))

    run_extract_and_build(
        [paper for papers in papers.values() for paper in papers],
        config,
        profile_name=profile_name,
    )
