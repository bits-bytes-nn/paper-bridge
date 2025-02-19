from graphrag_toolkit import LexicalGraphIndex
from graphrag_toolkit.storage import GraphStoreFactory
from graphrag_toolkit.storage import VectorStoreFactory

from llama_index.readers.web import SimpleWebPageReader

import nest_asyncio

nest_asyncio.apply()


def run_extract_and_build():

    graph_store = GraphStoreFactory.for_graph_store(
        "neptune-db://paper-bridge-dev-cluster.cluster-cyq3catzzgsc.us-west-2.neptune.amazonaws.com951"
    )

    vector_store = VectorStoreFactory.for_vector_store(
        "aoss://https://utzst204slqpizxwm9z0.us-west-2.aoss.amazonaws.com"
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
