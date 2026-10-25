#!/usr/bin/env python3
"""Benchmark fixed, already-loaded RAG models over authenticated HTTPS (stdlib only)."""

import argparse
import concurrent.futures
import json
import math
import os
import ssl
import statistics
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

DOCUMENTS = [
    "Llameros manages local Ollama models with an HTTPS dashboard and bearer-key inference gateway.",
    "API keys grant model and hardware permissions. Only SHA-256 digests are stored, not original keys.",
    "Deleting a key blocks new requests. Historical token counts and timings remain without prompt content.",
    "Document ingestion splits text into chunks, embeds each chunk, and stores vectors for retrieval.",
    "A RAG question is embedded, matched to document vectors, and sent with retrieved context to the LLM.",
    "CPU embedding and GPU generation can run independently. Models on one GPU share its execution queue.",
    "Model residency requires space for weights, context caches and runtime overhead, not only weights.",
    "TLS clients must trust the server certificate. Do not disable TLS verification or expose Ollama ports.",
]
QUERIES = [
    "How are API keys stored securely?",
    "What are the steps for answering a question with RAG?",
    "Why might embeddings run on the CPU while the LLM runs on a GPU?",
    "What memory is required to keep several models resident?",
]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, url, key, ca_cert=None, timeout=600):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Use an HTTPS inference base URL without credentials, query or fragment"
            )
        if not key:
            raise ValueError("Set LLAMEROS_API_KEY")
        self.url, self.key, self.timeout = url.rstrip("/"), key, timeout
        self.context = ssl.create_default_context(cafile=ca_cert or None)

    def open(self, path, body=None, target=None):
        # Separate opener per request so concurrent benchmark workers share no connection state.
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=self.context), NoRedirect())
        headers = {"Authorization": "Bearer " + self.key}
        if target:
            headers["X-Llameros-Target"] = target
        if body is not None:
            headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode() if body is not None else None
        with_error = Request(self.url + path, data=data, headers=headers)
        return opener.open(with_error, timeout=self.timeout)

    def models(self):
        with self.open("/v1/models") as response:
            return json.load(response)["data"]

    def embed(self, model, texts, target):
        with self.open(
            "/api/embed", {"model": model, "input": texts, "truncate": False}, target
        ) as response:
            result = json.load(response)
        vectors = result["embeddings"]
        if len(vectors) != len(texts):
            raise ValueError("Embedding response count mismatch")
        return [unit_vector(vector) for vector in vectors]

    def generate(self, model, question, documents, target, max_tokens):
        prompt = (
            "Use only the following context to answer briefly. If insufficient, say so.\n\n"
            + "\n\n".join(documents)
            + "\n\nQuestion: "
            + question
        )
        start, first, tokens, done = time.perf_counter(), None, None, False
        with self.open(
            "/api/chat",
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "options": {"num_predict": max_tokens, "temperature": 0},
            },
            target,
        ) as response:
            for line in response:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise ValueError("Generation stream returned an error")
                message = event.get("message", {})
                if first is None and (message.get("content") or message.get("thinking")):
                    first = time.perf_counter() - start
                if event.get("done"):
                    tokens = event.get("eval_count")
                    done = True
                    break
        if not done:
            raise ValueError("Generation stream ended without a terminal event")
        return {
            "generation_seconds": time.perf_counter() - start,
            "ttft_seconds": first,
            "output_tokens": tokens if type(tokens) is int and tokens >= 0 else None,
        }


def unit_vector(vector):
    if (
        not isinstance(vector, list)
        or not vector
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)
    ):
        raise ValueError("Invalid embedding vector")
    norm = math.hypot(*vector)
    if not norm:
        raise ValueError("Zero embedding vector")
    return [value / norm for value in vector]


def select_target(models, model, target):
    matches = [
        item
        for item in models
        if item["id"] == model and (not target or item.get("target") == target)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Choose exactly one permitted loaded target for {model}; use explicit target arguments"
        )
    return matches[0]["target"]


def summarize(values):
    if not values:
        return None
    ordered = sorted(values)
    return {
        "mean": statistics.mean(values),
        "p50": statistics.median(values),
        "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
    }


def benchmark(
    client,
    embedding_model,
    llm_model,
    embedding_target=None,
    llm_target=None,
    repeats=3,
    concurrency=1,
    warmup=1,
    top_k=3,
    max_tokens=128,
    query_prefix="",
    document_prefix="",
    documents=None,
    queries=None,
):
    documents = DOCUMENTS if documents is None else documents
    queries = QUERIES if queries is None else queries
    if (
        not documents
        or not queries
        or any(not isinstance(text, str) or not text.strip() for text in [*documents, *queries])
    ):
        raise ValueError("Corpus and queries must contain non-empty text strings")
    if (
        not 1 <= repeats <= 10000
        or not 1 <= concurrency <= 16
        or not 0 <= warmup <= 100
        or not 1 <= top_k <= len(documents)
        or not 1 <= max_tokens <= 4096
    ):
        raise ValueError(
            "Invalid benchmark limits (concurrency 1–16; output tokens 1–4096; top-k within corpus)"
        )
    models = client.models()
    embedding_target = select_target(models, embedding_model, embedding_target)
    llm_target = select_target(models, llm_model, llm_target)
    index_start = time.perf_counter()
    vectors = []
    for offset in range(0, len(documents), 16):
        vectors.extend(
            client.embed(
                embedding_model,
                [document_prefix + doc for doc in documents[offset : offset + 16]],
                embedding_target,
            )
        )
    if len({len(vector) for vector in vectors}) != 1:
        raise ValueError("Inconsistent document embedding dimensions")
    index_seconds = time.perf_counter() - index_start

    def query(index):
        text = queries[index % len(queries)]
        start = time.perf_counter()
        vector = client.embed(embedding_model, [query_prefix + text], embedding_target)[0]
        embedded = time.perf_counter()
        if len(vector) != len(vectors[0]):
            raise ValueError("Query/document embedding dimensions differ")
        scores = [sum(a * b for a, b in zip(vector, doc, strict=True)) for doc in vectors]
        selected = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        retrieved = time.perf_counter()
        result = client.generate(
            llm_model, text, [documents[i] for i in selected], llm_target, max_tokens
        )
        result.update(
            query_id=index % len(queries),
            embedding_seconds=embedded - start,
            retrieval_seconds=retrieved - embedded,
            total_seconds=time.perf_counter() - start,
        )
        return result

    for index in range(warmup):
        query(index)
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(query, range(repeats * len(queries))))
    elapsed = time.perf_counter() - start
    # Detect evictions or changed placement rather than silently benchmarking model swapping.
    after = client.models()
    select_target(after, embedding_model, embedding_target)
    select_target(after, llm_model, llm_target)
    return {
        "embedding_model": embedding_model,
        "llm_model": llm_model,
        "embedding_target": embedding_target,
        "llm_target": llm_target,
        "documents": len(documents),
        "queries": len(results),
        "concurrency": concurrency,
        "warmup_queries": warmup,
        "top_k": top_k,
        "max_tokens": max_tokens,
        "index_seconds": index_seconds,
        "wall_seconds": elapsed,
        "queries_per_second": len(results) / elapsed,
        "known_output_tokens": sum(result["output_tokens"] or 0 for result in results),
        "unknown_token_requests": sum(result["output_tokens"] is None for result in results),
        "timings": {
            field: summarize([result[field] for result in results if result[field] is not None])
            for field in (
                "embedding_seconds",
                "retrieval_seconds",
                "ttft_seconds",
                "generation_seconds",
                "total_seconds",
            )
        },
        "requests": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://127.0.0.1:8443/inference")
    parser.add_argument("--ca-cert", default=os.environ.get("LLAMEROS_CA_CERT"))
    parser.add_argument("--embedding-model", required=True, help="Exact already-loaded model:tag")
    parser.add_argument("--llm-model", required=True, help="Exact already-loaded model:tag")
    parser.add_argument("--embedding-target")
    parser.add_argument("--llm-target")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument(
        "--query-prefix", default="", help="Model-specific query prefix, if required"
    )
    parser.add_argument(
        "--document-prefix", default="", help="Model-specific document prefix, if required"
    )
    parser.add_argument(
        "--corpus", help='Optional JSON file with "documents" and "queries" string arrays'
    )
    args = parser.parse_args(argv)
    try:
        corpus = {}
        if args.corpus:
            with open(args.corpus) as stream:
                corpus = json.load(stream)
            if (
                not isinstance(corpus, dict)
                or not isinstance(corpus.get("documents"), list)
                or not isinstance(corpus.get("queries"), list)
            ):
                raise ValueError("Corpus must contain documents and queries arrays")
        client = Client(args.base_url, os.environ.get("LLAMEROS_API_KEY", ""), args.ca_cert)
        result = benchmark(
            client,
            args.embedding_model,
            args.llm_model,
            args.embedding_target,
            args.llm_target,
            args.repeats,
            args.concurrency,
            args.warmup,
            args.top_k,
            args.max_tokens,
            args.query_prefix,
            args.document_prefix,
            **corpus,
        )
        print(json.dumps(result, indent=2, allow_nan=False))
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
