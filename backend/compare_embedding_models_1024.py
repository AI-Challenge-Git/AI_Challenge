"""Quick 1024-dimension embedding model comparison on the frozen clustering cases."""

import os
import time
from dataclasses import dataclass

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

from evaluate_clustering_quality import (
    build_similarity_cache,
    calculate_cluster_metrics,
    cluster_agglomerative_candidate,
)
from evaluate_embedding_pairs import CASES

TARGET_DIMENSION = 1024
THRESHOLDS = [value / 100 for value in range(50, 96)]


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    model_id: str
    dimensions: int | None


CANDIDATES = (
    ModelCandidate("text-embedding-3-small", TARGET_DIMENSION),
    ModelCandidate("text-embedding-3-large", TARGET_DIMENSION),
)


def _normalize(vector: list[float]) -> list[float]:
    values = np.asarray(vector, dtype=float)
    norm = np.linalg.norm(values)
    if not np.isfinite(norm) or norm == 0:
        raise ValueError("embedding must have a finite non-zero norm")
    return (values / norm).tolist()


def _embed_batch(client: OpenAI, candidate: ModelCandidate) -> list[list[float]]:
    request = {
        "model": candidate.model_id,
        "input": [case[3] for case in CASES],
    }
    if candidate.dimensions is not None:
        request["dimensions"] = candidate.dimensions

    response = client.embeddings.create(**request)
    ordered = sorted(response.data, key=lambda item: item.index)
    vectors = [_normalize(item.embedding) for item in ordered]
    if len(vectors) != len(CASES):
        raise ValueError(f"expected {len(CASES)} embeddings, received {len(vectors)}")
    dimensions = {len(vector) for vector in vectors}
    if dimensions != {TARGET_DIMENSION}:
        raise ValueError(f"expected {TARGET_DIMENSION} dimensions, received {dimensions}")
    return vectors


def _evaluate(vectors: list[list[float]]) -> tuple:
    reports = []
    case_by_id = {}
    for (case_id, issue_type, cluster_label, symptom), vector in zip(CASES, vectors, strict=True):
        reports.append((case_id, issue_type.value, vector))
        case_by_id[case_id] = {
            "issue_type": issue_type.value,
            "cluster_label": cluster_label,
            "symptom": symptom,
        }

    cache = build_similarity_cache(reports)
    rows = []
    for threshold in THRESHOLDS:
        member_sets = cluster_agglomerative_candidate(
            reports,
            cache,
            threshold,
            linkage="average",
        )
        metrics = calculate_cluster_metrics(member_sets, case_by_id)
        rows.append((threshold, *metrics))

    precision_safe = [row for row in rows if row[5] >= 0.80]
    if not precision_safe:
        return max(rows, key=lambda row: (row[7], row[5], row[6]))
    return max(precision_safe, key=lambda row: (row[7], row[6], row[0]))


def main() -> None:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    client = OpenAI(api_key=api_key, timeout=90.0)
    print(f"cases={len(CASES)} target_dimension={TARGET_DIMENSION}")
    print("model threshold TP FP FN TN precision recall F1 elapsed_seconds")

    for candidate in CANDIDATES:
        started = time.perf_counter()
        try:
            vectors = _embed_batch(client, candidate)
            threshold, tp, fp, fn, tn, precision, recall, f1 = _evaluate(vectors)
            elapsed = time.perf_counter() - started
            print(
                f"{candidate.model_id} {threshold:.2f} {tp} {fp} {fn} {tn} "
                f"{precision:.6f} {recall:.6f} {f1:.6f} {elapsed:.2f}"
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            print(
                f"{candidate.model_id} ERROR {type(exc).__name__}: {exc} "
                f"elapsed_seconds={elapsed:.2f}"
            )


if __name__ == "__main__":
    main()
