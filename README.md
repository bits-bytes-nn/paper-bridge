## 🗞️ PAPER-BRIDGE

**Paper Bridge** is a Graph RAG-based application that analyzes technical trends in key AI/ML papers published on [HuggingFace Daily Papers](https://huggingface.co/papers), providing insights by comparing related papers. It helps answer questions like:
- *"What are the recent major developments in the specific technical field of this paper?"*
- *"What are the key differences between this paper and recent papers that aim to solve similar problems?"*

### Architecture

![AWS Architecture](assets/paper-bridge-architecture.drawio.png)

#### Workflow (hand-drawn)

![Workflow](assets/paper-bridge-workflow.png)

#### GraphRAG pipeline (hand-drawn)

![GraphRAG pipeline](assets/paper-bridge-pipeline.png)

> **Deep dive:** the full line-by-line technical reference lives in
> [`assets/tech-doc.md`](assets/tech-doc.md).

#### Infrastructure Components
- **Network**: VPC, subnets, security groups, Client VPN, NAT, VPC endpoint
- **Database**: Neptune DB (graph), OpenSearch Serverless (vectors)
- **Compute**: AWS Batch (ECS on EC2) for indexer/summarizer, Lambda for cleaner
- **Integration**: Amazon Bedrock, EventBridge schedules, SSM Parameter Store, S3, ECR
- **IaC**: Terraform (`terraform/modules/{base,client,neptune,opensearch}`)

### Workflow

Models are centralized in shared config: **Claude Sonnet 4.6** (summarize / GraphRAG
response), **Claude Haiku 4.5** (main-content extraction, figure captions), and
**Cohere Embed English v3** (1024-dim vectors), all via Amazon Bedrock with
cross-region inference profiles.

#### Indexing Phase (AWS Batch)
1. EventBridge triggers the Batch job at scheduled times
2. Fetches candidate papers from HF Daily Papers, then **selects + de-duplicates
   across days** with a configurable popularity + recency scorer (`shared/paper_selection.py`)
3. Downloads PDFs from the static `arxiv.org/pdf` host (Retry-After aware) and
   fetches metadata in one batched, rate-limit-serialized arXiv API call
   (`shared/arxiv_client.py`)
4. Parses text using [LlamaParse](https://www.llamaindex.ai/llamaparse) or [Unstructured](https://unstructured.io/)
5. Extracts the main content (drops abstract/references) with Haiku 4.5
6. Indexes into Neptune DB + OpenSearch Serverless using the [AWS GraphRAG toolkit](https://github.com/awslabs/graphrag-toolkit)

#### Search / Summarize Phase (AWS Batch)
1. EventBridge triggers the Batch job at scheduled times
2. Selects the day's top papers (same scorer), parses HTML or PDF
3. Extracts figures and generates captions with a VLM
4. Summarizes each paper (Sonnet 4.6) and runs GraphRAG retrieval to compare it to
   related work
5. Renders an HTML report → image, and delivers via **Slack (Block Kit)** or opens a
   **GitHub pull request**

#### Cleanup (AWS Lambda)
A scheduled Lambda prunes documents outside a configurable date window from Neptune
and OpenSearch.

### Development

```bash
poetry install            # install deps (incl. dev group: pytest, ruff, black, mypy)
poetry run pytest         # run the test suite
poetry run ruff check .   # lint
poetry run black --check . # format check
poetry run mypy paper_bridge  # type check
```

CI (lint, format, type-check, tests + coverage, Docker build, `terraform validate`,
security scan) runs via GitHub Actions — see [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
Full build / test / deploy details and the configuration reference are in
[`assets/tech-doc.md`](assets/tech-doc.md) (§11–§12).
