"""Lexical-graph schema labels and edges (graphrag-lexical-graph v3).

These vertex labels and edge labels are the single load-bearing contract between
the indexer (which writes the graph) and the cleaner/summarizer (which traverse
and delete it). They were previously inlined as string literals inside Gremlin
queries duplicated across modules; centralizing them here keeps the schema
defined once so a future graphrag change is a one-line edit.
"""

from __future__ import annotations

from enum import Enum


class Vertex(str, Enum):
    """Vertex (node) labels written by the graphrag lexical-graph builder."""

    SOURCE = "__Source__"
    CHUNK = "__Chunk__"
    TOPIC = "__Topic__"
    STATEMENT = "__Statement__"
    FACT = "__Fact__"
    ENTITY = "__Entity__"


class Edge(str, Enum):
    """Edge labels connecting the lexical-graph vertices."""

    EXTRACTED_FROM = "__EXTRACTED_FROM__"
    MENTIONED_IN = "__MENTIONED_IN__"
    BELONGS_TO = "__BELONGS_TO__"
    SUPPORTS = "__SUPPORTS__"
    SUBJECT = "__SUBJECT__"
    OBJECT = "__OBJECT__"
