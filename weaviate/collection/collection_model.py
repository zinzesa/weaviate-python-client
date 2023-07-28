import hashlib
import typing
import uuid as uuid_package
from dataclasses import dataclass
from typing import Type, Optional, Any, List, Set, Dict, Generic, TypeAlias, TypeVar, Tuple, Union

from pydantic import BaseModel, Field, create_model, field_validator
from requests.exceptions import ConnectionError as RequestsConnectionError

from weaviate.collection.collection_base import CollectionBase, CollectionObjectBase
from weaviate.collection.collection_classes import Errors
from weaviate.connect import Connection
from weaviate.data.replication import ConsistencyLevel
from weaviate.exceptions import UnexpectedStatusCodeException
from weaviate.util import _to_beacons
from weaviate.weaviate_classes import (
    CollectionConfigBase,
    PYTHON_TYPE_TO_DATATYPE,
    Metadata,
    MetadataReturn,
    PropertyConfig,
)
from weaviate.weaviate_types import UUID, UUIDS, BEACON


@dataclass
class ReferenceTo:
    ref_type: Union[Type, str]

    @property
    def name(self) -> str:
        if isinstance(self.ref_type, type):
            return self.ref_type.__name__.lower().capitalize()
        else:
            assert isinstance(self.ref_type, str)
            return self.ref_type.lower().capitalize()


@dataclass
class BatchReference:
    from_uuid: UUID
    to_uuid: UUID


class BaseProperty(BaseModel):
    uuid: UUID = Field(default=uuid_package.uuid4())
    vector: Optional[List[float]] = None

    # def __new__(cls, *args, **kwargs):
    #     #
    #     build = super().__new__(cls)
    #     # fields, class_vars = collect_model_fields(cls)
    #     for name, field in build.model_fields.items():
    #         if name not in BaseProperty.model_fields:
    #             field_type = build._remove_optional_type(field.annotation)
    #             if inspect.isclass(field_type):
    #                 if field.annotation not in PYTHON_TYPE_TO_DATATYPE:
    #                     build.model_fields[name] = fields.FieldInfo(annotation=typing.Optional[UUID], default=None)
    #
    #     build.__class_vars__.update(build.__class_vars__)
    #     return build
    #
    #
    # make references optional by default - does not work
    def __init__(self, **data) -> None:
        super().__init__(**data)
        self._reference_fields: Set[str] = self.get_ref_fields(type(self))

        self._reference_to_class: Dict[str, str] = {}
        for ref in self._reference_fields:
            self._reference_to_class[ref] = self.model_fields[ref].metadata[0].name

    @staticmethod
    def get_ref_fields(model: Type["BaseProperty"]) -> Set[str]:
        return {
            name
            for name, field in model.model_fields.items()
            if (
                field.metadata is not None
                and len(field.metadata) > 0
                and isinstance(field.metadata[0], ReferenceTo)
            )
            and name not in BaseProperty.model_fields
        }

    @staticmethod
    def get_non_ref_fields(model: Type["BaseProperty"]) -> Set[str]:
        return {
            name
            for name, field in model.model_fields.items()
            if (
                field.metadata is None
                or len(field.metadata) == 0
                or isinstance(field.metadata[0], PropertyConfig)
            )
            and name not in BaseProperty.model_fields
        }

    def props_to_dict(self, update: bool = False) -> Dict[str, Any]:
        fields_to_exclude: Set[str] = self._reference_fields.union({"uuid", "vector"})
        if update:
            fields_to_exclude.union(
                {field for field in self.model_fields.keys() if field not in self.model_fields_set}
            )

        c = self.model_dump(exclude=fields_to_exclude)
        for ref in self._reference_fields:
            val = getattr(self, ref, None)
            if val is not None:
                c[ref] = _to_beacons(val, self._reference_to_class[ref])
        return c

    @field_validator("uuid")
    def create_valid_uuid(cls, input_uuid: UUID) -> uuid_package.UUID:
        if isinstance(input_uuid, uuid_package.UUID):
            return input_uuid

        # see if str is already a valid uuid
        try:
            return uuid_package.UUID(input_uuid)
        except ValueError:
            hex_string = hashlib.md5(input_uuid.encode("UTF-8")).hexdigest()
            return uuid_package.UUID(hex=hex_string)

    @staticmethod
    def type_to_dict(model: Type["BaseProperty"]) -> List[Dict[str, Any]]:
        types = typing.get_type_hints(model)

        non_optional_types = {
            name: BaseProperty._remove_optional_type(tt)
            for name, tt in types.items()
            if name not in BaseProperty.model_fields
        }

        non_ref_fields = model.get_non_ref_fields(model)
        properties = []
        for name in non_ref_fields:
            prop = {
                "name": name.capitalize(),
                "dataType": [PYTHON_TYPE_TO_DATATYPE[non_optional_types[name]]],
            }
            metadata_list = model.model_fields[name].metadata
            if metadata_list is not None and len(metadata_list) > 0:
                metadata = metadata_list[0]
                if isinstance(metadata, PropertyConfig):
                    prop.update(metadata.model_dump(exclude_unset=True, exclude_none=True))

            properties.append(prop)

        reference_fields = model.get_ref_fields(model)
        properties.extend(
            {
                "name": name.capitalize(),
                "dataType": [model.model_fields[name].metadata[0].name],
            }
            for name in reference_fields
        )

        return properties

    @staticmethod
    def _remove_optional_type(python_type: type) -> type:
        args = typing.get_args(python_type)
        if len(args) == 0:
            return python_type

        return_type = [t for t in args if t is not None][0]
        return return_type


Model = TypeVar("Model", bound=BaseProperty)


class RefToObjectModel(BaseModel, Generic[Model]):
    uuids_to: Union[List[UUID], UUID] = Field()

    def to_beacon(self) -> List[Dict[str, str]]:
        return _to_beacons(self.uuids_to)


UserModelType: TypeAlias = Type[BaseProperty]


class CollectionConfigModel(CollectionConfigBase):
    pass


@dataclass
class _Object(Generic[Model]):
    data: Model
    metadata: MetadataReturn


class CollectionObjectModel(CollectionObjectBase, Generic[Model]):
    def __init__(self, connection: Connection, name: str, dynamic_model: Type[Model]) -> None:
        super().__init__(connection, name)
        self._model: Type[Model] = dynamic_model

    def with_tenant(self, tenant: Optional[str] = None) -> "CollectionObjectModel":
        return self._with_tenant(tenant)

    def with_consistency_level(
        self, consistency_level: Optional[ConsistencyLevel] = None
    ) -> "CollectionObjectModel":
        return self._with_consistency_level(consistency_level)

    def insert(self, obj: Model) -> uuid_package.UUID:
        self._model.model_validate(obj)

        weaviate_obj: Dict[str, Any] = {
            "class": self._name,
            "properties": obj.props_to_dict(),
            "id": str(obj.uuid),
        }
        if obj.vector is not None:
            weaviate_obj["vector"] = obj.vector

        self._insert(weaviate_obj)
        return uuid_package.UUID(str(obj.uuid))

    def insert_many(self, objects: List[Model]) -> List[Union[uuid_package.UUID, Errors]]:
        for obj in objects:
            self._model.model_validate(obj)

        weaviate_objs: List[Dict[str, Any]] = [
            {
                "class": self._name,
                "properties": obj.props_to_dict(),
                "id": str(obj.uuid),
            }
            for obj in objects
        ]
        return self._insert_many(weaviate_objs)

    def replace(self, obj: Model, uuid: UUID) -> None:
        self._model.model_validate(obj)

        weaviate_obj: Dict[str, Any] = {
            "class": self._name,
            "properties": obj.props_to_dict(),
        }
        if obj.vector is not None:
            weaviate_obj["vector"] = obj.vector

        self._replace(weaviate_obj, uuid)

    def update(self, obj: Model, uuid: UUID) -> None:
        self._model.model_validate(obj)

        weaviate_obj: Dict[str, Any] = {
            "class": self._name,
            "properties": obj.props_to_dict(update=True),
        }
        if obj.vector is not None:
            weaviate_obj["vector"] = obj.vector

        self._update(weaviate_obj, uuid)

    def get_by_id(
        self, uuid: UUID, metadata: Optional[Metadata] = None
    ) -> Optional[_Object[Model]]:
        ret = self._get_by_id(uuid=uuid, metadata=metadata)
        if ret is None:
            return None
        return self._json_to_object(ret)

    def get(self, metadata: Optional[Metadata] = None) -> Optional[List[_Object[Model]]]:
        ret = self._get(metadata=metadata)
        if ret is None:
            return None

        return [self._json_to_object(obj) for obj in ret["objects"]]

    def reference_add(self, from_uuid: UUID, from_property: str, to_uuids: UUIDS) -> None:
        self._reference_add(
            from_uuid=from_uuid, from_property_name=from_property, to_uuids=to_uuids
        )

    def reference_delete(self, from_uuid: UUID, from_property: str, to_uuids: UUIDS) -> None:
        self._reference_delete(
            from_uuid=from_uuid, from_property_name=from_property, to_uuids=to_uuids
        )

    def reference_replace(self, from_uuid: UUID, from_property: str, to_uuids: UUIDS) -> None:
        self._reference_replace(
            from_uuid=from_uuid, from_property_name=from_property, to_uuids=to_uuids
        )

    def reference_batch_add(self, from_property: str, refs: List[BatchReference]) -> None:
        refs_dict = [
            {
                "from": BEACON + f"{self._name}/{ref.from_uuid}/{from_property}",
                "to": BEACON + str(ref.to_uuid),
            }
            for ref in refs
        ]
        self._reference_batch_add(refs_dict)

    def _json_to_object(self, obj: Dict[str, Any]) -> _Object[Model]:
        for ref in self._model.get_ref_fields(self._model):
            if ref not in obj["properties"]:
                continue

            beacons = obj["properties"][ref]
            uuids = []
            for beacon in beacons:
                uri = beacon["beacon"]
                assert isinstance(uri, str)
                uuids.append(uri.split("/")[-1])

            obj["properties"][ref] = uuids

        return _Object[Model](data=self._model(**obj["properties"]), metadata=MetadataReturn(**obj))


class CollectionModel(CollectionBase, Generic[Model]):
    def __init__(self, connection: Connection, model: Type[Model]):
        super().__init__(connection)
        self._model = model

    def create(self, config: CollectionConfigModel) -> CollectionObjectModel[Model]:
        name = super()._create(config, self._model.type_to_dict(self._model), self._model.__name__)

        return CollectionObjectModel[Model](self._connection, name, self._model)

    def get(self) -> CollectionObjectModel[Model]:
        collection_name = self._model.__name__.capitalize()
        path = f"/schema/{collection_name}"

        try:
            response = self._connection.get(path=path)
        except RequestsConnectionError as conn_err:
            raise RequestsConnectionError("Schema could not be retrieved.") from conn_err
        if response.status_code != 200:
            raise UnexpectedStatusCodeException("Get schema", response)

        response_json = response.json()
        model_props = self._model.type_to_dict(self._model)
        schema_props = [
            {"name": prop["name"], "dataType": prop["dataType"]}
            for prop in response_json["properties"]
        ]

        def compare(s: List[Any], t: List[Any]) -> bool:
            t = list(t)  # make a mutable copy
            try:
                for elem in s:
                    t.remove(elem)
            except ValueError:
                return False
            return not t

        if compare(model_props, schema_props):
            raise TypeError("Schemas not compatible")
        return CollectionObjectModel[Model](self._connection, collection_name, self._model)

    def get_dynamic(
        self, collection_name: str
    ) -> Tuple[CollectionObjectModel[Model], UserModelType]:
        path = f"/schema/{collection_name.capitalize()}"

        try:
            response = self._connection.get(path=path)
        except RequestsConnectionError as conn_err:
            raise RequestsConnectionError("Schema could not be retrieved.") from conn_err
        if response.status_code != 200:
            raise UnexpectedStatusCodeException("Get schema", response)

        response_json = response.json()
        fields: Dict[str, Any] = {
            prop["name"]: (PYTHON_TYPE_TO_DATATYPE[prop["dataType"][0]], None)
            for prop in response_json["properties"]
        }
        model = create_model(response_json["class"], **fields, __base__=self._model)

        return CollectionObjectModel(self._connection, collection_name, model), model