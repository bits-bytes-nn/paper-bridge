"""GitHub output handler for Markdown + GitHub PR workflow."""

import asyncio
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from github import Auth, Github, GithubException

from paper_bridge.shared import extract_unique_urls

from ..aws_helpers import get_ssm_param_value
from ..constants import EnvVars, SSMParams
from ..logger import is_aws_env, logger
from .base import BaseOutputHandler

if TYPE_CHECKING:
    from ...configs.config import Config
    from ..fetcher import Paper
    from ..renderer import Result


COVER_IMAGES_MAP: dict[str, str] = {
    "language-models": "language-models.jpg",
    "multimodal-learning": "multimodal-learning.jpg",
    "retrieval-augmented-generation": "retrieval-augmented-generation.jpg",
    "computer-vision": "computer-vision.jpg",
    "natural-language-processing": "natural-language-processing.jpg",
}


class GitHubOutputHandler(BaseOutputHandler):
    """Handler for Markdown rendering and GitHub PR output."""

    def __init__(
        self,
        config: "Config",
        boto3_session: boto3.Session | None = None,
        root_dir: Path | None = None,
    ):
        super().__init__(config, boto3_session)
        self.github_config = config.output.github
        self.root_dir = root_dir or Path("/tmp")
        self._github_token: str | None = None

    @property
    def github_token(self) -> str | None:
        """Get GitHub token from SSM or environment."""
        if self._github_token is None:
            if is_aws_env() and self.boto3_session:
                base_path = f"/{self.config.resources.project_name}-{self.config.resources.stage}"
                try:
                    self._github_token = get_ssm_param_value(
                        self.boto3_session,
                        f"{base_path}/{SSMParams.GITHUB_TOKEN.value}",
                    )
                except Exception as e:
                    logger.warning("Failed to get GitHub token from SSM: %s", e)
                    self._github_token = EnvVars.GITHUB_TOKEN.env_value
            else:
                self._github_token = EnvVars.GITHUB_TOKEN.env_value
        return self._github_token

    async def process(
        self,
        papers: list["Paper"],
        results: list["Result"],
        output_dir: Path,
        retrievals: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """Process papers and create GitHub PRs.

        Args:
            papers: List of Paper objects
            results: List of Result objects with summaries
            output_dir: Directory for output files
            retrievals: Optional retrieval results
        """
        if not self.github_config.repo_name:
            logger.error("GitHub repository not configured")
            return

        if not self.github_token:
            logger.error("GitHub token not available")
            return

        output_dir.mkdir(parents=True, exist_ok=True)

        for paper, result in zip(papers, results, strict=False):
            retrieval = retrievals.get(paper.arxiv_id) if retrievals else None
            markdown_path = await self._create_markdown(
                paper, result, output_dir, retrieval
            )

            if markdown_path and markdown_path.exists():
                await self._create_github_pr(paper, markdown_path)

    async def send_single(
        self,
        paper: "Paper",
        result: "Result",
        output_path: Path,
        retrieval: dict[str, str] | None = None,
    ) -> bool:
        """Send a single paper as GitHub PR.

        Args:
            paper: Paper object
            result: Result object with summary
            output_path: Path to markdown output file
            retrieval: Optional retrieval result

        Returns:
            True if successful, False otherwise
        """
        if not self.github_config.repo_name:
            logger.error("GitHub repository not configured")
            return False

        if not self.github_token:
            logger.error("GitHub token not available")
            return False

        try:
            if output_path.suffix == ".md" and output_path.exists():
                await self._create_github_pr(paper, output_path)
                return True

            markdown_path = await self._create_markdown(
                paper, result, output_path.parent, retrieval
            )
            if markdown_path and markdown_path.exists():
                await self._create_github_pr(paper, markdown_path)
                return True

            return False

        except Exception as e:
            logger.error("Failed to create GitHub PR: %s", e)
            return False

    async def _create_markdown(
        self,
        paper: "Paper",
        result: "Result",
        output_dir: Path,
        retrieval: dict[str, str] | None = None,
    ) -> Path | None:
        """Create markdown file for paper summary.

        Args:
            paper: Paper object
            result: Result object with summary
            output_dir: Directory for output file
            retrieval: Optional retrieval result

        Returns:
            Path to created markdown file
        """
        try:
            content = self._format_markdown(paper, result, retrieval)
            safe_title = re.sub(r"[\s,:?]", "-", paper.title.lower()).strip("-")[:50]
            file_name = f"{datetime.now().strftime('%Y-%m-%d')}-{safe_title}.md"

            markdown_path = output_dir / file_name
            await asyncio.to_thread(markdown_path.write_text, content, encoding="utf-8")

            logger.info("Created markdown file: %s", markdown_path)
            return markdown_path

        except Exception as e:
            logger.error("Failed to create markdown: %s", e)
            return None

    def _format_markdown(
        self,
        paper: "Paper",
        result: "Result",
        retrieval: dict[str, str] | None = None,
    ) -> str:
        """Format paper summary as markdown with Jekyll front matter."""
        tags = result.tags or []
        keywords_str = ", ".join([f'"{tag.replace(" ", "-")}"' for tag in tags])

        category = tags[0] if tags else "Machine Learning"
        category_str = category.replace(" ", "-")
        cover_image = COVER_IMAGES_MAP.get(category_str.lower(), "default.jpg")

        escaped_title = paper.title.replace('"', '\\"')
        date_str = paper.published_at.strftime("%Y-%m-%d %H:%M:%S")

        front_matter = f"""---
layout: post
title: "{escaped_title}"
date: {date_str}
author: "Paper Bridge"
categories: ["Paper Reviews", "{category_str}"]
tags: [{keywords_str}]
cover: /assets/images/{cover_image}
use_math: true
---
"""

        summary_text = result.summary or ""

        retrieval_section = ""
        if retrieval:
            retrieval_summary = retrieval.get("summary", "")
            if retrieval_summary:
                retrieval_section = f"\n\n### Related Works\n{retrieval_summary}"

            urls = retrieval.get("urls", "")
            if urls:
                retrieval_section += "\n\n### References\n"
                for url in self._extract_unique_urls(urls):
                    retrieval_section += f"* {url}\n"

        body = f"### Summary\n{summary_text}{retrieval_section}"
        references = f"\n---\n### Paper\n* [{paper.title}]({paper.pdf_url})"

        return f"{front_matter}{body}{references}"

    async def _create_github_pr(self, paper: "Paper", markdown_path: Path) -> None:
        """Create GitHub pull request with paper summary.

        Args:
            paper: Paper object
            markdown_path: Path to markdown file
        """
        if not self.github_token:
            logger.error("GitHub token not available")
            return

        clone_dir = self.root_dir / "github_clone"

        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            branch_name = (
                f"{self.github_config.branch_prefix}/{paper.arxiv_id}-{timestamp}"
            )

            commit_message = f"feat: Add paper summary for '{paper.title}'"
            pr_title = f"Paper Summary: {paper.title}"
            pr_body = (
                f"This PR adds an AI-generated summary for the paper: **{paper.title}**\n\n"
                f"- **ArXiv ID**: {paper.arxiv_id}\n"
                f"- **PDF URL**: {paper.pdf_url}\n"
                f"- **Published**: {paper.published_at.strftime('%Y-%m-%d')}\n"
                f"- **Upvotes**: {paper.upvotes}\n\n"
                f"This pull request was automatically generated by Paper Bridge."
            )

            await asyncio.to_thread(
                self._git_operations,
                clone_dir,
                branch_name,
                commit_message,
                markdown_path,
            )

            logger.info("Creating pull request on GitHub...")

            auth = Auth.Token(self.github_token)
            g = Github(auth=auth)
            gh_repo = g.get_repo(self.github_config.repo_name)

            try:
                pr = gh_repo.create_pull(
                    title=pr_title,
                    body=pr_body,
                    head=branch_name,
                    base=self.github_config.base_branch,
                )
                logger.info("Successfully created pull request: %s", pr.html_url)

            except GithubException as e:
                if e.status == 422 and "A pull request already exists" in str(e.data):
                    logger.warning(
                        "Pull request for branch '%s' already exists", branch_name
                    )
                else:
                    raise

        except Exception as e:
            logger.error("Failed to create GitHub pull request: %s", e, exc_info=True)

        finally:
            if clone_dir.exists():
                await asyncio.to_thread(shutil.rmtree, clone_dir, ignore_errors=True)
                logger.debug("Cleaned up clone directory: %s", clone_dir)

    def _git_operations(
        self,
        clone_dir: Path,
        branch_name: str,
        commit_message: str,
        markdown_path: Path,
    ) -> None:
        """Perform git operations for PR creation.

        Args:
            clone_dir: Directory to clone repo into
            branch_name: Name of branch to create
            commit_message: Commit message
            markdown_path: Path to markdown file to add
        """
        # Lazy import: GitPython probes for a git executable at import time and
        # raises if none is on PATH. Only the GitHub PR path needs it, so import
        # here (not at module top) — that keeps the summarizer importable for the
        # retrieve/summarize → Slack path on an image without the git binary.
        from git import Repo

        repo_url = f"https://oauth2:{self.github_token}@github.com/{self.github_config.repo_name}.git"

        if clone_dir.exists():
            shutil.rmtree(clone_dir)

        logger.info("Cloning repository '%s'", self.github_config.repo_name)
        repo = Repo.clone_from(repo_url, clone_dir)

        if branch_name in repo.heads:
            new_branch = repo.heads[branch_name]
        else:
            new_branch = repo.create_head(
                branch_name,
                repo.remotes.origin.refs[self.github_config.base_branch],
            )
        new_branch.checkout()

        posts_dir = clone_dir / self.github_config.posts_dir
        posts_dir.mkdir(exist_ok=True)
        shutil.copy(markdown_path, posts_dir)
        logger.info("Copied markdown file to '%s'", posts_dir)

        figures_dir = markdown_path.parent / "figures"
        if figures_dir.exists():
            file_name_stem = markdown_path.stem
            assets_target_dir = (
                clone_dir / self.github_config.assets_dir / file_name_stem
            )
            assets_target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(figures_dir, assets_target_dir, dirs_exist_ok=True)
            logger.info("Copied figures to '%s'", assets_target_dir)

        if not repo.is_dirty(untracked_files=True):
            logger.warning("No changes to commit")
            return

        logger.info("Committing changes...")
        repo.git.add(all=True)

        author_name = self.github_config.author_name
        author_email = self.github_config.author_email
        if author_email:
            author_actor = f"{author_name} <{author_email}>"
        else:
            author_actor = author_name

        repo.git.commit("-m", commit_message, f"--author={author_actor}")

        logger.info("Pushing to branch '%s'", branch_name)
        origin = repo.remote(name="origin")
        origin.push(refspec=f"{branch_name}:{branch_name}", force=True)

    # Delegates to the shared, tested implementation (deduplicates Markdown links
    # by their underlying URL, preserving order).
    _extract_unique_urls = staticmethod(extract_unique_urls)
