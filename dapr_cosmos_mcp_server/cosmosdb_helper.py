import ast
import os
import asyncio
from typing import Annotated
from dotenv import load_dotenv
from azure.identity.aio import AzureCliCredential
from azure.cosmos.aio import CosmosClient
from azure.cosmos import PartitionKey, exceptions
import json
load_dotenv()


async def ensure_container_exists():
    endpoint       = os.getenv("AZURE_COSMOSDB_ENDPOINT")
    db_name        = os.getenv("AZURE_COSMOSDB_DB_NAME")
    container_name = os.getenv("AZURE_COSMOSDB_CONTAINER_NAME")

    global client, container, credential
    print(f"Ensuring Cosmos DB container exists: {container_name} in {db_name} at {endpoint}")
    credential = AzureCliCredential()
    

    try:
        client = CosmosClient(endpoint, credential=credential)
        database = client.get_database_client(db_name)
        try:
            await database.read()
            
        except exceptions.CosmosResourceNotFoundError:
            database = await client.create_database(id=db_name)

        try:
            container = await database.create_container(
                id=container_name,
                partition_key=PartitionKey(path="/user_id"),
                offer_throughput=400,
            )
            print(f"Created container {container_name}")
        
        except Exception as e:

            if isinstance(e, exceptions.CosmosResourceExistsError):
                container = database.get_container_client(container_name)
                print(f"Container {container_name} already exists")
            else:
                print("Error creating container:", e)
                raise

        # return both, so caller can use and later close
        #return client, credential, container
    except Exception as e:
        #await client.close()
        #await credential.close()
        print("Error ensuring container exists:", e)
        raise

#client, credential, container = asyncio.run(ensure_container_exists())

async def cosmosdb_create_item(item: Annotated[dict, "Json object to be inserted"]) -> Annotated[str, "create_item Result"]:
    """
    Create a new item in the Cosmos DB container.
    item is a Json object.
    """
    try:
        if isinstance(item, str):
            try:
                # First try proper JSON
                item = json.loads(item)
            except json.JSONDecodeError:
                # If it's Python dict string with single quotes, fallback
                item = ast.literal_eval(item)

        if not isinstance(item, dict):
            raise ValueError("Item must be a dict or valid JSON string")
        
        response = await container.create_item(item)
        print("Created item:", response)
        return "Item created successfully"
    except Exception as e:
        print("Error creating item:", e)
        raise e


async def cosmosdb_query_items(query: str = "SELECT * FROM c") -> list[dict]:

    items: list[dict] = []
    result_iter = container.query_items(query=query, enable_scan_in_query=True)
    async for page in result_iter.by_page():
        async for it in page:
            items.append(it)
    return items

async def main():
    
    try:
        await ensure_container_exists()
    finally:
        await client.close()
        await credential.close()
        return
    try:
        doc = await container.read_item(
            item="6d302871-038f-4c96-8d46-d4be07a9d8d0",
            partition_key="testuser1@domain.com"
        )
        print("Item:", doc)
    finally:
        await client.close()
        await credential.close()

if __name__ == "__main__":
    asyncio.run(main())
