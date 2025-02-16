import os
import boto3
import nest_asyncio
from dataclasses import dataclass
from typing import Any, Dict
import graphrag_toolkit.storage.opensearch_vector_indexes as osvi
import urllib3
from graphrag_toolkit import LexicalGraphIndex
from graphrag_toolkit.storage import GraphStoreFactory, VectorStoreFactory
from llama_index.readers.web import SimpleWebPageReader
from opensearchpy import (
    AsyncHttpConnection,
    AsyncOpenSearch,
    AWSV4SignerAsyncAuth,
    AWSV4SignerAuth,
    OpenSearch,
    RequestsHttpConnection,
)
from urllib.parse import urlparse
import urllib.parse
from paper_bridge.indexer.src import is_aws_env

DEFAULT_REGION = "us-west-2"
DEFAULT_SERVICE = "aoss"
DEFAULT_TIMEOUT = 30
DEFAULT_PORT = 8443

import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("opensearch").setLevel(logging.DEBUG)
logging.getLogger("urllib3").setLevel(logging.DEBUG)

os.environ["AWS_REGION"] = DEFAULT_REGION


@dataclass
class OpenSearchClientConfig:
    endpoint: str
    region: str = DEFAULT_REGION
    service: str = DEFAULT_SERVICE
    timeout: int = DEFAULT_TIMEOUT
    port: int = DEFAULT_PORT
    verify_certs: bool = True

    def get_client_kwargs(self, actual_host: str, auth: Any) -> Dict[str, Any]:
        return {
            "hosts": [{"host": "localhost", "port": self.port}],
            "http_auth": auth,
            "use_ssl": True,
            "verify_certs": self.verify_certs,
            "timeout": self.timeout,
            "headers": {"Host": actual_host},
        }


class PatchedAsyncHttpConnection(AsyncHttpConnection):
    async def perform_request(
        self,
        method,
        url,
        params=None,
        body=None,
        timeout=None,
        ignore=None,
        headers=None,
        **kwargs
    ):
        if headers is None:
            headers = {}
        # 실제 요청을 보낼 때도 Host 헤더를 강제로 설정합니다.
        headers["Host"] = (
            self.actual_host
            if hasattr(self, "actual_host") and self.actual_host
            else headers.get("Host", "")
        )
        return await super().perform_request(
            method,
            url,
            params=params,
            body=body,
            timeout=timeout,
            ignore=ignore,
            headers=headers,
            **kwargs
        )


# 2. Patched Async Auth 클래스: 서명 전에 headers에 올바른 Host 값을 주입합니다.
class PatchedAWSV4SignerAsyncAuth(AWSV4SignerAsyncAuth):
    actual_host: str = None  # 실제 도메인을 저장할 속성

    def __call__(self, *args, **kwargs):
        # 기본적으로 AWSV4SignerAsyncAuth는 (method, url, query_string, body, headers)
        # 형태로 호출될 수 있습니다. 여기서 query_string은 서명 시 URL에 병합합니다.
        # 최종적으로 부모에는 (method, new_url, body, headers) 순서로 positional 인수로 전달합니다.
        if len(args) == 5:
            method, url, query_string, body, headers = args
        else:
            # 인수가 부족하면 기본값으로 채웁니다.
            method = args[0]
            url = args[1]
            query_string = args[2] if len(args) > 2 else {}
            body = args[3] if len(args) > 3 else None
            headers = args[4] if len(args) > 4 else {}
        # URL의 netloc을 actual_host로 교체합니다.
        parsed = urllib.parse.urlparse(url)
        new_parsed = parsed._replace(netloc=self.actual_host)
        new_url = new_parsed.geturl()
        if query_string:
            sep = "&" if "?" in new_url else "?"
            new_url = new_url + sep + urllib.parse.urlencode(query_string)
        if headers is None:
            headers = {}
        # 서명 계산에 올바른 Host 헤더를 사용하도록 합니다.
        headers["Host"] = self.actual_host
        # 부모 __call__은 keyword 인수를 받지 않으므로 positional 인수만 사용합니다.
        return super().__call__(method, new_url, body, headers)


def get_aws_credentials(
    region: str,
) -> Any:
    session = boto3.Session(region_name=region)
    creds = session.get_credentials().get_frozen_credentials()
    return creds


def parse_endpoint(endpoint: str) -> str:
    if endpoint.startswith("aoss://"):
        endpoint = endpoint[len("aoss://") :]
    parsed = urlparse(endpoint)
    if not parsed.hostname:
        raise ValueError("Could not extract hostname from endpoint")
    return parsed.hostname


if not is_aws_env():
    urllib3.disable_warnings()

    def new_create_os_client(endpoint: str, **kwargs) -> OpenSearch:
        config = OpenSearchClientConfig(endpoint, verify_certs=False)
        creds = get_aws_credentials(config.region)
        auth = AWSV4SignerAuth(
            credentials=creds, region=config.region, service=config.service
        )
        actual_host = parse_endpoint(endpoint)

        client_kwargs = config.get_client_kwargs(actual_host=actual_host, auth=auth)
        client_kwargs["connection_class"] = RequestsHttpConnection

        return OpenSearch(**client_kwargs)

    def new_create_os_async_client(endpoint: str, **kwargs) -> AsyncOpenSearch:
        config = OpenSearchClientConfig(endpoint, verify_certs=False)
        creds = get_aws_credentials(config.region)
        actual_host = parse_endpoint(endpoint)  # 실제 도메인 추출

        # 패치된 인증 클래스 생성 및 실제 도메인 저장
        auth = PatchedAWSV4SignerAsyncAuth(
            credentials=creds, region=config.region, service=config.service
        )
        auth.actual_host = actual_host

        client_kwargs = config.get_client_kwargs(actual_host=actual_host, auth=auth)
        client_kwargs["connection_class"] = PatchedAsyncHttpConnection

        client = AsyncOpenSearch(**client_kwargs)

        # 생성된 각 연결 객체에 실제 도메인 값을 설정합니다.
        for conn in client.transport.connection_pool.connections:
            conn.actual_host = actual_host

        return client

    osvi.create_os_client = new_create_os_client
    osvi.create_os_async_client = new_create_os_async_client

    _original_client = boto3.Session(region_name=DEFAULT_REGION).client

    def patched_client(service_name: str, *args, **kwargs) -> Any:
        if service_name in ["neptunedata", "neptune-graph"]:
            kwargs["verify"] = False
        return _original_client(service_name, *args, **kwargs)

    boto3.client = patched_client

nest_asyncio.apply()


def run_extract_and_build():

    graph_store = GraphStoreFactory.for_graph_store("neptune-db://localhost:8182")

    vector_store = VectorStoreFactory.for_vector_store(
        "aoss://https://2r94ylx9l78nar1kpp16.us-west-2.aoss.amazonaws.com"
    )

    graph_index = LexicalGraphIndex(graph_store, vector_store)

    doc_urls = [
        "https://docs.aws.amazon.com/neptune/latest/userguide/intro.html",
        "https://docs.aws.amazon.com/neptune-analytics/latest/userguide/what-is-neptune-analytics.html",
        "https://docs.aws.amazon.com/neptune-analytics/latest/userguide/neptune-analytics-features.html",
        "https://docs.aws.amazon.com/neptune-analytics/latest/userguide/neptune-analytics-vs-neptune-database.html",
    ]

    docs = SimpleWebPageReader(
        html_to_text=True, metadata_fn=lambda url: {"url": url}
    ).load_data(doc_urls)

    graph_index.extract_and_build(docs, show_progress=True)


if __name__ == "__main__":
    run_extract_and_build()
