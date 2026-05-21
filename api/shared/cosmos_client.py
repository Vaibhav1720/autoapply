"""Cosmos DB client for the AutoApply API."""

import os
from azure.cosmos import CosmosClient, PartitionKey, exceptions


_client = None
_database = None


def get_cosmos_client():
    """Get or create the singleton Cosmos DB client."""
    global _client, _database
    if _client is None:
        endpoint = os.environ["COSMOS_ENDPOINT"]
        key = os.environ["COSMOS_KEY"]
        db_name = os.environ.get("COSMOS_DATABASE", "autoapply")
        _client = CosmosClient(endpoint, credential=key)
        _database = _client.get_database_client(db_name)
    return _database


def get_container(name: str):
    """Get a container client by name."""
    db = get_cosmos_client()
    return db.get_container_client(name)


def create_item(container_name: str, item: dict) -> dict:
    """Create an item in a container."""
    container = get_container(container_name)
    return container.create_item(body=item)


def read_item(container_name: str, item_id: str, partition_key: str) -> dict | None:
    """Read a single item by ID and partition key."""
    container = get_container(container_name)
    try:
        return container.read_item(item=item_id, partition_key=partition_key)
    except exceptions.CosmosResourceNotFoundError:
        return None


def upsert_item(container_name: str, item: dict) -> dict:
    """Create or update an item."""
    container = get_container(container_name)
    return container.upsert_item(body=item)


def query_items(container_name: str, query: str, parameters: list | None = None,
                partition_key: str | None = None) -> list:
    """Query items with optional parameters."""
    container = get_container(container_name)
    kwargs = {"query": query, "enable_cross_partition_query": partition_key is None}
    if parameters:
        kwargs["parameters"] = parameters
    if partition_key is not None:
        kwargs["partition_key"] = partition_key
    return list(container.query_items(**kwargs))


def delete_item(container_name: str, item_id: str, partition_key: str) -> None:
    """Delete an item by ID and partition key."""
    container = get_container(container_name)
    container.delete_item(item=item_id, partition_key=partition_key)


def vector_search(container_name: str, embedding: list[float], vector_field: str,
                  top_k: int = 20, select_fields: list[str] | None = None) -> list:
    """Run a native Cosmos DB vector search using VectorDistance().

    Returns items sorted by similarity (highest first) with a similarityScore field.
    """
    container = get_container(container_name)
    select_clause = ", ".join(f"c.{f}" for f in select_fields) if select_fields else "c"
    query = (
        f"SELECT TOP @topK {select_clause}, "
        f"VectorDistance(c.{vector_field}, @embedding) AS similarityScore "
        f"FROM c "
        f"ORDER BY VectorDistance(c.{vector_field}, @embedding)"
    )
    params = [
        {"name": "@topK", "value": top_k},
        {"name": "@embedding", "value": embedding},
    ]
    return list(container.query_items(
        query=query,
        parameters=params,
        enable_cross_partition_query=True,
    ))
