<div align="center">

# 🗞️ Paper Bridge

**Turns the day's most-discussed AI/ML papers into a short, trustworthy briefing — putting each paper in context against related work through a knowledge graph.**

Daily arXiv pipeline on AWS · GraphRAG (Neptune + OpenSearch) · powered by Amazon Bedrock (Claude).

[![CI](https://github.com/bits-bytes-nn/paper-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/bits-bytes-nn/paper-bridge/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Terraform](https://img.shields.io/badge/IaC-Terraform-7B42BC)
![Bedrock](https://img.shields.io/badge/LLM-Amazon%20Bedrock%20(Claude)-green)

🇰🇷 [한국어 README](./README.ko.md)

</div>

---

**Paper Bridge** reads the [HuggingFace Daily Papers](https://huggingface.co/papers) feed, builds a **GraphRAG lexical graph** from the papers it ingests, and then answers questions like:

- *"What are the recent major developments in this paper's specific technical field?"*
- *"How does this paper differ from other recent work tackling the same problem?"*

The result is delivered automatically to **Slack** (or as a **GitHub pull request**) every day.

---

## How it works

Paper Bridge is three independent workflows that share one pair of data stores (a Neptune graph + an OpenSearch vector index). Each runs on its own schedule via Amazon EventBridge.

| Workflow | Runs on | What it does |
|----------|---------|--------------|
| **Indexer** | AWS Batch | Collect candidate papers → select & de-duplicate → download + parse PDFs → extract the main content → build the GraphRAG graph (Neptune) and vectors (OpenSearch). |
| **Summarizer** | AWS Batch | Pick the day's papers → parse + caption figures → summarize with an LLM → run GraphRAG retrieval for related-work context → render a report → deliver to Slack / GitHub. |
| **Cleaner** | AWS Lambda | Delete documents outside a configurable date window from both Neptune and OpenSearch, keeping storage bounded. |

### Architecture

![AWS Architecture](assets/paper-bridge-architecture.drawio.png)

### Data flow

![Data flow](assets/paper-bridge-dataflow.drawio.png)

---

## The three workflows in detail

### 1. Indexing (AWS Batch)

1. EventBridge triggers the Batch job on a schedule.
2. Fetch candidate papers from HuggingFace Daily Papers, then **select and de-duplicate across days** with a configurable popularity + recency scorer (`shared/paper_selection.py`).
3. Download each PDF from the static `arxiv.org/pdf` host (with `Retry-After`-aware backoff) and pull metadata in one batched, rate-limit-serialized arXiv API call (`shared/arxiv_client.py`).
4. Parse the text with [LlamaParse](https://www.llamaindex.ai/llamaparse) or [Unstructured](https://unstructured.io/).
5. Extract the main content (dropping the abstract, references, etc.) with Claude Haiku.
6. Index into Neptune + OpenSearch using the [AWS GraphRAG toolkit](https://github.com/awslabs/graphrag-toolkit).

### 2. Search & Summarize (AWS Batch)

1. EventBridge triggers the Batch job on a schedule.
2. Select the day's top papers (same scorer), then parse the paper's HTML or PDF.
3. Extract figures and generate captions with a vision model.
4. Summarize the paper with Claude Sonnet, then run **GraphRAG retrieval** to compare it against related work already in the graph.
5. Render an HTML report → image, and deliver it via **Slack (Block Kit)** or open a **GitHub pull request**.

### 3. Cleanup (AWS Lambda)

A scheduled Lambda prunes documents outside a configurable date window from both Neptune and OpenSearch, so the stores don't grow without bound.

---

## Models

Model choices are centralized in shared config and resolved to Amazon Bedrock cross-region inference profiles:

| Role | Model |
|------|-------|
| Summarization / GraphRAG response | **Claude Sonnet 4.6** |
| Main-content extraction, figure captions | **Claude Haiku 4.5** |
| Embeddings (1024-dim vectors) | **Cohere Embed English v3** |

---

## Infrastructure

All infrastructure is defined in Terraform (`terraform/modules/{base,client,neptune,opensearch}`):

- **Network** — VPC, public/private subnets, NAT, optional Client VPN, VPC endpoint.
- **Data** — Amazon Neptune (graph) + OpenSearch Serverless (vectors).
- **Compute** — AWS Batch (ECS on EC2) for the indexer/summarizer, Lambda for the cleaner.
- **Integration** — Amazon Bedrock, EventBridge schedules, SSM Parameter Store, S3, ECR, SNS.

---

## Development

```bash
poetry install              # install deps (incl. dev group: pytest, ruff, black, mypy)
poetry run pytest           # run the test suite
poetry run ruff check .     # lint
poetry run black --check .  # format check
poetry run mypy paper_bridge/shared   # type check (shared/ is the blocking gate)
```

CI runs on GitHub Actions — lint, format, type-check, tests + coverage, Docker builds, `terraform validate`, and a security scan. See [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

---

## License

MIT — see [LICENSE](LICENSE).
