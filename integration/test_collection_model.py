import sys
from typing import List, Optional

from weaviate import Config
from weaviate.collection.grpc import MetadataQuery

if sys.version_info < (3, 9):
    from typing_extensions import Annotated
else:
    from typing import Annotated
import pytest as pytest
import uuid

import weaviate
from weaviate.weaviate_classes import (
    BaseProperty,
    CollectionModelConfig,
    MultiTenancyConfig,
    PropertyConfig,
    ReferenceTo,
    Tenant,
    Vectorizer,
)
from weaviate.weaviate_types import UUIDS

REF_TO_UUID = uuid.uuid4()


class Group(BaseProperty):
    name: str


@pytest.fixture(scope="module")
def client():
    client = weaviate.Client(
        "http://localhost:8080", additional_config=Config(grpc_port_experimental=50051)
    )
    client.schema.delete_all()
    collection = client.collection_model.create(
        CollectionModelConfig(model=Group, vectorizer=Vectorizer.NONE)
    )
    collection.data.insert(obj=Group(name="Name", uuid=REF_TO_UUID))

    yield client
    client.schema.delete_all()


def test_with_existing_collection(client: weaviate.Client):
    obj = client.collection_model.get(Group).data.get_by_id(REF_TO_UUID)
    assert obj.data.name == "Name"


@pytest.mark.parametrize(
    "member_type,value",
    [
        (str, "1"),
        (int, 1),
        (float, 0.5),
        (List[str], ["1", "2"]),
        (List[int], [1, 2]),
        (List[float], [1.0, 2.1]),
    ],
)
@pytest.mark.parametrize("optional", [True, False])
def test_types(client: weaviate.Client, member_type, value, optional: bool):
    if optional:
        member_type = Optional[member_type]

    class ModelTypes(BaseProperty):
        name: member_type

    client.collection_model.delete(ModelTypes)
    collection = client.collection_model.create(
        CollectionModelConfig(model=ModelTypes, vectorizer=Vectorizer.NONE)
    )
    assert collection.model == ModelTypes

    uuid_object = collection.data.insert(ModelTypes(name=value))
    assert type(uuid_object) is uuid.UUID

    object_get = collection.data.get_by_id(uuid_object)
    assert object_get.data == ModelTypes(name=value, uuid=uuid_object)

    if optional:
        uuid_object_optional = collection.data.insert(ModelTypes(name=None))
        object_get_optional = collection.data.get_by_id(uuid_object_optional)
        assert object_get_optional.data == ModelTypes(name=None, uuid=uuid_object_optional)


@pytest.mark.parametrize(
    "member_type, annotation ,value,expected",
    [
        (str, PropertyConfig(indexFilterable=False), "value", "text"),
        (UUIDS, ReferenceTo(Group), [str(REF_TO_UUID)], "Group"),
        (Optional[UUIDS], ReferenceTo(Group), [str(REF_TO_UUID)], "Group"),
    ],
)
def test_types_annotates(client: weaviate.Client, member_type, annotation, value, expected: str):
    class ModelTypes(BaseProperty):
        name: Annotated[member_type, annotation]

    client.collection_model.delete(ModelTypes)
    collection = client.collection_model.create(
        CollectionModelConfig(model=ModelTypes, vectorizer=Vectorizer.NONE)
    )
    assert collection.model == ModelTypes

    uuid_object = collection.data.insert(ModelTypes(name=value))

    object_get = collection.data.get_by_id(uuid_object)
    assert type(object_get.data) is ModelTypes

    assert object_get.data.name == value


def test_create_and_delete(client: weaviate.Client):
    class DeleteModel(BaseProperty):
        name: int

    client.collection_model.delete(DeleteModel)
    client.collection_model.create(
        CollectionModelConfig(model=DeleteModel, vectorizer=Vectorizer.NONE)
    )

    assert client.collection_model.exists(DeleteModel)
    client.collection_model.delete(DeleteModel)
    assert not client.collection_model.exists(DeleteModel)


def test_search(client: weaviate.Client):
    class SearchTest(BaseProperty):
        name: str

    client.collection_model.delete(SearchTest)
    collection = client.collection_model.create(
        CollectionModelConfig(model=SearchTest, vectorizer=Vectorizer.NONE)
    )

    collection.data.insert(SearchTest(name="test name"))
    collection.data.insert(SearchTest(name="other words"))

    objects = collection.query.bm25_flat(query="test")
    assert type(objects[0].data) is SearchTest
    assert objects[0].data.name == "test name"


def test_tenants(client: weaviate.Client):
    class TenantsTest(BaseProperty):
        name: str

    client.collection_model.delete(TenantsTest)
    collection = client.collection_model.create(
        CollectionModelConfig(
            vectorizer=Vectorizer.NONE,
            multiTenancyConfig=MultiTenancyConfig(enabled=True),
            model=TenantsTest,
        )
    )

    collection.tenants.add([Tenant(name="tenant1")])

    tenants = collection.tenants.get()
    assert len(tenants) == 1
    assert type(tenants[0]) is Tenant
    assert tenants[0].name == "tenant1"

    collection.tenants.remove(["tenant1"])

    tenants = collection.tenants.get()
    assert len(tenants) == 0


def test_multi_searches(client: weaviate.Client):
    class TestMultiSearches(BaseProperty):
        name: Optional[str] = None

    client.collection_model.delete(TestMultiSearches)
    collection = client.collection_model.create(
        CollectionModelConfig(model=TestMultiSearches, vectorizer=Vectorizer.NONE)
    )

    collection.data.insert(TestMultiSearches(name="some word"))
    collection.data.insert(TestMultiSearches(name="other"))

    objects = collection.query.bm25_flat(
        query="word",
        return_properties=["name"],
        return_metadata=MetadataQuery(last_update_time_unix=True),
    )
    assert objects[0].data.name == "some word"
    assert objects[0].metadata.last_update_time_unix is not None

    objects = collection.query.bm25_flat(query="other", return_metadata=MetadataQuery(uuid=True))
    assert objects[0].data.name is None
    assert objects[0].metadata.uuid is not None
    assert objects[0].metadata.last_update_time_unix is None


@pytest.mark.skip()
def test_multi_searches_with_references(client: weaviate.Client):
    class TestMultiSearchesWithReferences(BaseProperty):
        name: Optional[str] = None
        group: Annotated[Optional[UUIDS], ReferenceTo(Group)] = None

    client.collection_model.delete(TestMultiSearchesWithReferences)
    collection = client.collection_model.create(
        CollectionModelConfig(model=TestMultiSearchesWithReferences, vectorizer=Vectorizer.NONE)
    )

    collection.data.insert(TestMultiSearchesWithReferences(name="some word", group=REF_TO_UUID))
    collection.data.insert(TestMultiSearchesWithReferences(name="other", group=REF_TO_UUID))

    objects = collection.query.bm25_flat(
        query="word",
        return_properties=["name", "group"],
        return_metadata=MetadataQuery(last_update_time_unix=True),
    )
    assert objects[0].data.name == "some word"
    assert objects[0].data.group == REF_TO_UUID
    assert objects[0].metadata.last_update_time_unix is not None

    objects = collection.query.bm25_flat(
        query="other",
        return_metadata=MetadataQuery(uuid=True),
    )
    assert objects[0].data.name is None
    assert objects[0].data.group is None
    assert objects[0].metadata.uuid is not None
    assert objects[0].metadata.last_update_time_unix is None


def test_search_with_tenant(client: weaviate.Client):
    class TestTenantSearch(BaseProperty):
        name: str

    client.collection_model.delete(TestTenantSearch)
    collection = client.collection_model.create(
        CollectionModelConfig(
            model=TestTenantSearch,
            vectorizer=Vectorizer.NONE,
            multiTenancyConfig=MultiTenancyConfig(enabled=True),
        )
    )

    collection.tenants.add([Tenant(name="Tenant1"), Tenant(name="Tenant2")])
    tenant1 = collection.with_tenant("Tenant1")
    tenant2 = collection.with_tenant("Tenant2")
    uuid1 = tenant1.data.insert(TestTenantSearch(name="some"))
    objects1 = tenant1.query.bm25_flat(query="some", return_metadata=MetadataQuery(uuid=True))
    assert len(objects1) == 1
    assert objects1[0].metadata.uuid == uuid1

    objects2 = tenant2.query.bm25_flat(query="some", return_metadata=MetadataQuery(uuid=True))
    assert len(objects2) == 0
