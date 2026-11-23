"""Minimal Dijkstra shortest-path baseline for non-negative weighted graphs."""

from __future__ import annotations

import heapq
import json
import math
from pathlib import Path
from typing import Any

from ..base import make_result_summary
from ..validators import build_risk_item, check_nan_inf


MODEL_SPEC = {
    "model_id": "graph.shortest_path",
    "model_family": "graph",
    "inputs_schema": {
        "nodes": "optional node list",
        "edges": "list of [source, target, weight]",
        "directed": "boolean, default false",
        "source": "start node",
        "target": "end node",
    },
    "outputs_schema": {
        "shortest_distance": "number",
        "path": "node list",
        "visited_count": "integer",
    },
    "supported": True,
    "limitations": [
        "Dijkstra only.",
        "Non-negative edge weights only.",
        "No stochastic, time-dependent, or multi-objective routing.",
    ],
    "failure_conditions": [
        "negative_edge",
        "source_missing",
        "target_missing",
        "disconnected",
        "output_nan_or_inf",
    ],
}


def _load_graph_config(config: dict[str, Any], case_dir: Path | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    input_path = config.get("input_path")
    if not input_path:
        return config, []
    if not case_dir:
        return config, [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="case_dir is required for graph input_path",
            )
        ]
    path = case_dir / str(input_path)
    if not path.exists():
        return config, [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"graph input file missing: {input_path}",
                path=str(input_path),
            )
        ]
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return config, [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"graph input JSON parse failed: {exc}",
                path=str(input_path),
            )
        ]
    merged = dict(config)
    if isinstance(loaded, dict):
        merged.update(loaded)
    return merged, []


def _parse_edges(config: dict[str, Any]) -> tuple[set[str], dict[str, list[tuple[str, float]]], list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    edges = config.get("edges")
    if not isinstance(edges, list):
        return set(), {}, [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="edges must be a list of [source, target, weight]",
                field="edges",
            )
        ]
    nodes = {str(item) for item in config.get("nodes", [])} if isinstance(config.get("nodes"), list) else set()
    directed = bool(config.get("directed", False))
    graph: dict[str, list[tuple[str, float]]] = {}
    for idx, edge in enumerate(edges):
        if not isinstance(edge, (list, tuple)) or len(edge) != 3:
            risks.append(build_risk_item(severity="fatal", category="input_validation", message=f"edges[{idx}] must have 3 items"))
            continue
        u = str(edge[0])
        v = str(edge[1])
        try:
            weight = float(edge[2])
        except (TypeError, ValueError):
            risks.append(build_risk_item(severity="fatal", category="input_validation", message=f"edges[{idx}] weight is not numeric"))
            continue
        nodes.update([u, v])
        if math.isnan(weight) or math.isinf(weight):
            risks.append(build_risk_item(severity="fatal", category="input_validation", message=f"edges[{idx}] weight is NaN/Inf"))
            continue
        if weight < 0:
            risks.append(build_risk_item(severity="fatal", category="input_validation", message="negative edge weight detected", field=f"edges[{idx}]"))
            continue
        graph.setdefault(u, []).append((v, weight))
        graph.setdefault(v, [])
        if not directed:
            graph.setdefault(v, []).append((u, weight))
            graph.setdefault(u, graph.get(u, []))
    return nodes, graph, risks


def _dijkstra(graph: dict[str, list[tuple[str, float]]], source: str, target: str) -> tuple[float, list[str], int]:
    dist: dict[str, float] = {source: 0.0}
    prev: dict[str, str] = {}
    heap: list[tuple[float, str]] = [(0.0, source)]
    visited: set[str] = set()
    while heap:
        current_dist, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == target:
            break
        for neighbor, weight in graph.get(node, []):
            candidate = current_dist + weight
            if candidate < dist.get(neighbor, float("inf")):
                dist[neighbor] = candidate
                prev[neighbor] = node
                heapq.heappush(heap, (candidate, neighbor))
    if target not in dist:
        return float("inf"), [], len(visited)
    path = [target]
    while path[-1] != source:
        path.append(prev[path[-1]])
    path.reverse()
    return dist[target], path, len(visited)


def run(config: dict[str, Any], *, run_id: str, case_dir: Path | None = None) -> dict[str, Any]:
    case_id = case_dir.name if case_dir else None
    config, risks = _load_graph_config(config, case_dir)
    source = str(config.get("source", ""))
    target = str(config.get("target", ""))
    nodes, graph, edge_risks = _parse_edges(config)
    risks.extend(edge_risks)
    if not source:
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="source is required", field="source"))
    if not target:
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="target is required", field="target"))
    if source and source not in nodes:
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="source node does not exist", field="source"))
    if target and target not in nodes:
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="target node does not exist", field="target"))

    if risks:
        return make_result_summary(
            run_id=run_id,
            case_id=case_id,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="input_invalid",
            metrics={},
            primary_results={},
            risk_items=risks,
            limitations=MODEL_SPEC["limitations"],
            failure_reason="input_invalid",
        )

    distance, path, visited_count = _dijkstra(graph, source, target)
    if math.isinf(distance):
        risks.append(build_risk_item(severity="fatal", category="graph", message="source and target are disconnected"))
        distance_value: float | None = None
    else:
        distance_value = distance
    if check_nan_inf({"shortest_distance": distance_value, "path": path, "visited_count": visited_count}):
        risks.append(build_risk_item(severity="fatal", category="output_validation", message="shortest_path output contains NaN/Inf"))

    return make_result_summary(
        run_id=run_id,
        case_id=case_id,
        model_id=MODEL_SPEC["model_id"],
        model_family=MODEL_SPEC["model_family"],
        success=not any(item.get("fatal") for item in risks),
        status="ok" if not any(item.get("fatal") for item in risks) else "fatal",
        metrics={"shortest_distance": distance_value, "visited_count": visited_count},
        primary_results={"shortest_distance": distance_value, "path": path, "visited_count": visited_count},
        risk_items=risks,
        limitations=MODEL_SPEC["limitations"],
        failure_reason="disconnected" if risks else None,
    )
