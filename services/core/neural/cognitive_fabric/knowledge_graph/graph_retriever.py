from __future__ import annotations

import re
from typing import Any

import networkx as nx

from neural.cognitive_fabric.knowledge_graph.models import EntityType
from neural.cognitive_fabric.knowledge_graph.temporal_graph import TemporalKnowledgeGraph
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.knowledge_graph.retriever")


class GraphRetriever:
    """
    Retrieves knowledge via graph traversal over the TemporalKnowledgeGraph:
    - Entity Extraction from text queries.
    - Local 1-hop and 2-hop neighborhood exploration.
    - Multi-hop path finding connecting entities.
    - Community and cluster summarization.
    """

    def __init__(self, kg: TemporalKnowledgeGraph) -> None:
        self.kg = kg

    def extract_entities_from_text(self, text: str) -> list[str]:
        """
        Extracts known entity IDs or exact names mentioned in text.
        """
        matched_ids = []
        text_lower = text.lower()

        # Check entity IDs and names
        for entity_id, entity in self.kg._entities.items():
            if entity_id.lower() in text_lower or entity.name.lower() in text_lower:
                matched_ids.append(entity_id)

        # Regex heuristics for standard ID patterns (e.g. ANC-EMP-*, TASK-*, PROJ-*)
        pattern = r"\b(ANC-EMP-[A-Z0-9]+|TASK-[A-Z0-9\-]+|PROJ-[A-Z0-9\-]+|DEC-[A-Z0-9\-]+)\b"
        for m in re.finditer(pattern, text, re.IGNORECASE):
            token = m.group(1).upper()
            for entity_id in self.kg._entities:
                if entity_id.upper() == token and entity_id not in matched_ids:
                    matched_ids.append(entity_id)

        return matched_ids

    def get_local_neighborhood(
        self,
        entity_id: str,
        hops: int = 1,
        active_only: bool = True,
    ) -> dict[str, Any]:
        """
        Retrieves 1-hop or 2-hop subgraph around a target entity.
        """
        if entity_id not in self.kg._entities:
            return {"center_entity": None, "neighbors": [], "edges": []}

        center = self.kg._entities[entity_id]
        visited_nodes = {entity_id}
        frontier = {entity_id}
        edges_found = []
        seen_edge_ids: set[str] = set()

        for _ in range(hops):
            next_frontier = set()
            for current in frontier:
                # Outgoing and incoming edges
                for rel in self.kg._relationships.values():
                    if active_only and not rel.is_active:
                        continue
                    if rel.from_entity_id == current:
                        if rel.rel_id not in seen_edge_ids:
                            seen_edge_ids.add(rel.rel_id)
                            edges_found.append(rel.to_dict())
                        target = rel.to_entity_id
                        if target not in visited_nodes:
                            visited_nodes.add(target)
                            next_frontier.add(target)
                    elif rel.to_entity_id == current:
                        if rel.rel_id not in seen_edge_ids:
                            seen_edge_ids.add(rel.rel_id)
                            edges_found.append(rel.to_dict())
                        source = rel.from_entity_id
                        if source not in visited_nodes:
                            visited_nodes.add(source)
                            next_frontier.add(source)
            frontier = next_frontier

        neighbor_entities = [
            self.kg._entities[nid].to_dict()
            for nid in visited_nodes
            if nid != entity_id and nid in self.kg._entities
        ]

        return {
            "center_entity": center.to_dict(),
            "neighbors": neighbor_entities,
            "edges": edges_found,
            "total_nodes": len(neighbor_entities),
            "total_edges": len(edges_found),
        }

    def find_multi_hop_paths(
        self,
        source_entity_id: str,
        target_entity_id: str,
        max_hops: int = 4,
        active_only: bool = True,
    ) -> list[list[dict[str, Any]]]:
        """
        Finds all simple directed or undirected paths connecting two entities up to max_hops.
        """
        if (
            source_entity_id not in self.kg._entities
            or target_entity_id not in self.kg._entities
        ):
            return []

        # Build active subgraph view
        active_g = nx.Graph()
        for eid, ent in self.kg._entities.items():
            active_g.add_node(eid, **ent.to_dict())

        for rel in self.kg._relationships.values():
            if active_only and not rel.is_active:
                continue
            active_g.add_edge(rel.from_entity_id, rel.to_entity_id, **rel.to_dict())

        try:
            raw_paths = list(nx.all_simple_paths(
                active_g,
                source=source_entity_id,
                target=target_entity_id,
                cutoff=max_hops,
            ))
        except (nx.NodeNotFound, nx.NetworkXNoPath):
            return []

        formatted_paths = []
        for path in raw_paths:
            path_details = [self.kg._entities[nid].to_dict() for nid in path]
            formatted_paths.append(path_details)

        return formatted_paths

    def get_community_summary(self, entity_type: EntityType | str) -> dict[str, Any]:
        """
        Generates cluster/community summary of all entities of a given type.
        """
        e_type = entity_type if isinstance(entity_type, EntityType) else EntityType(entity_type.lower())
        matching = [e for e in self.kg._entities.values() if e.entity_type == e_type]
        stats = {
            "entity_type": e_type.value,
            "total_count": len(matching),
            "verified_count": sum(1 for e in matching if e.verification_status == "verified"),
            "entities": [e.name for e in matching[:15]],
        }
        return stats

