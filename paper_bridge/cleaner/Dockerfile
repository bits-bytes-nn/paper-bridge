FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.10

COPY main.py requirements.txt ./
COPY configs/ paper_bridge/cleaner/configs/
COPY src/ paper_bridge/cleaner/src/

RUN pip install --no-cache-dir -r requirements.txt -t .

CMD ["main.lambda_handler"]
