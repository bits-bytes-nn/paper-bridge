# syntax=docker/dockerfile:1
#
# Build context: the REPO ROOT (buildspec runs `docker build -f paper_bridge/cleaner/Dockerfile .`),
# so that the shared package (paper_bridge/shared/) is included in the image.
#
# NOTE: This image uses the AWS Lambda Python base image. Lambda already runs the
# container as an unprivileged sandbox user with a fixed /var/task layout and a
# handler-based CMD, so we deliberately do NOT create our own user or change the
# working directory -- doing so would break the Lambda runtime contract. We only
# pin the base image, bump to Python 3.11 (to match pyproject's python = "^3.11"),
# and order layers so the dependency layer caches across application code changes.
ARG TARGET_PLATFORM=linux/amd64
FROM --platform=${TARGET_PLATFORM} public.ecr.aws/lambda/python:3.11

# Install Python dependencies first so this layer is cached when only app code changes.
COPY paper_bridge/cleaner/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Application source (changes most often -> copied last).
COPY paper_bridge/cleaner/main.py ${LAMBDA_TASK_ROOT}/
COPY paper_bridge/shared/ ${LAMBDA_TASK_ROOT}/paper_bridge/shared/
COPY paper_bridge/cleaner/configs/ ${LAMBDA_TASK_ROOT}/paper_bridge/cleaner/configs/
COPY paper_bridge/cleaner/src/ ${LAMBDA_TASK_ROOT}/paper_bridge/cleaner/src/

# Lambda handler entrypoint (do not change -- this is the Lambda contract).
CMD ["main.lambda_handler"]
