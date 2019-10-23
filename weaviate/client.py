import json
import os
import requests
import sys
import validators

from .connect import *
from .exceptions import *

SCHEMA_CLASS_TYPE_THINGS = "things"
SCHEMA_CLASS_TYPE_ACTIONS = "actions"


class Client:
    """ A python native weaviate client
    """

    def __init__(self, url, auth_client_secret=""):
        """ New weaviate client

        :param url: To the weaviate instance
        :type url: str
        :param auth_client_secret: Authentification client secret
        :type auth_client_secret: str
        """
        if url is None:
            raise TypeError("URL is expected to be string but is None")
        if not isinstance(url, str):
            raise TypeError("URL is expected to be string but is "+str(type(url)))
        if not validators.url(url):
            # IPs ending with 0 are not seen as valid URL
            # Lets check if a valid URL is in place
            ip = url
            if ip.startswith("http://"):
                ip = ip[7:]
            if ip.startswith("https://"):
                ip = ip[8:]
            ip = ip.split(':')[0]
            if not validators.ip_address.ipv4(ip):
                raise ValueError("URL has no propper form: " + url)

        self.connection = connection.Connection(url=url, auth_client_secret=auth_client_secret)

    def create_thing(self, thing, class_name, uuid=None):
        """ Takes a dict describing the thing and adds it to weaviate

        :param thing: Thing to be added
        :type thing: dict
        :param class_name: Associated with the thing given
        :type class_name: str
        :param uuid: Thing will be created under this uuid if it is provided
        :type uuid: str
        :return: Returns the id of the created thing if successful
        :raises: TypeError, ValueError, ThingAlreadyExistsException, UnexpectedStatusCodeException
        """
        if not isinstance(thing, dict):
            raise TypeError("Expected thing to be of type dict instead it was: "+str(type(thing)))
        if not isinstance(class_name, str):
            raise TypeError("Expected class_name of type str but was: "+str(type))

        weaviate_obj = {
            "class": class_name,
            "schema": thing
        }
        if uuid is not None:
            if not isinstance(uuid, str):
                raise TypeError("Expected uuid to be of type str but was: "+str(type(uuid)))
            if not validators.uuid(uuid):
                raise ValueError("Given uuid does not have a valid form")

            weaviate_obj["id"] = uuid

        try:
            response = self.connection.run_rest("/things", REST_METHOD_POST, weaviate_obj)
        except ConnectionError as conn_err:
            raise type(conn_err)(str(conn_err) + ' Connection error, thing was not added to weaviate.').with_traceback(sys.exc_info()[2])

        if response.status_code == 200:
            return response.json()["id"]

        else:
            thing_does_already_exist = False
            try:
                if 'already exists' in response.json()['error'][0]['message']:
                    thing_does_already_exist = True
            except KeyError:
                pass
            except Exception as e:
                raise type(e)(str(e) + ' Unexpected exception.').with_traceback(sys.exc_info()[2])

            if thing_does_already_exist:
                raise ThingAlreadyExistsException

            raise UnexpectedStatusCodeException(response.json())

    def create_things_in_batch(self, things_batch_request):
        """ Creates multiple things at once in weaviate

        :param things_batch_request: The batch of things that should be added
        :type things_batch_request: ThingsBatchRequest
        :return:
        """

        path = "/batching/things"

        try:
            response = self.connection.run_rest(path, REST_METHOD_POST, things_batch_request.get_request_body())
        except ConnectionError as conn_err:
            raise type(conn_err)(str(conn_err) + ' Connection error, batch was not added to weaviate.').with_traceback(
                sys.exc_info()[2])

    # Updates an already existing thing
    # thing contains a dict describing the new values
    def update_thing(self, thing, class_name, uuid):

        weaviate_obj = {
            "id": uuid,
            "class": class_name,
            "schema": thing
        }

        try:
            response = self.connection.run_rest("/things/"+uuid, REST_METHOD_PUT, weaviate_obj)
        except ConnectionError as conn_err:
            raise type(conn_err)(str(conn_err) + ' Connection error, thing was not updated.').with_traceback(
            sys.exc_info()[2])

        if response.status_code == 200:
            return

        else:
            raise UnexpectedStatusCodeException(response.json())

    # Add a property reference to a thing
    # thing_uuid the thing that should have the reference as part of its properties
    # the name of the property within the thing
    # The beacon dict takes the form: [{
    #                     "beacon": "weaviate://localhost/things/uuid",
    #                     ...
    #                 }]
    def add_property_reference_to_thing(self, thing_uuid, property_name, property_beacons):

        path = "/things/" + thing_uuid + "/references/" + property_name

        try:
            response = self.connection.run_rest(path, REST_METHOD_POST, property_beacons)
        except ConnectionError as conn_err:
            raise type(conn_err)(
                str(conn_err) + ' Connection error, reference was not added to weaviate.').with_traceback(
                sys.exc_info()[2])

        if response.status_code == 200:
            return
        elif response.status_code == 401:
            raise UnauthorizedRequest401Exception
        elif response.status_code == 403:
            raise ForbiddenRequest403Exception
        elif response.status_code == 422:
            raise SemanticError422Exception
        elif response.status_code == 500:
            raise ServerError500Exception(response.json())
        else:
            raise UnexpectedStatusCodeException(response.json())

    def add_references_in_batch(self, reference_batch_request):
        """ Batch loading references
        Loading batch references is faster by ignoring some validations.
        Loading inconsistent data may ends up in an invalid graph.
        If the consistency of the references is not guaranied use
        add_property_reference_to_thing to have additional validation instead.

        :param reference_batch_request: contains all the references that should be added in one batch
        :type reference_batch_request: weaviate.batch.ReferenceBatchRequest
        :return: None
        :raises: ConnectionError, UnauthorizedRequest401Exception, ForbiddenRequest403Exception
        """

        if reference_batch_request.get_batch_size() == 0:
            return  # No data in batch

        path = "/batching/references"

        try:
            response = self.connection.run_rest(path, REST_METHOD_POST, reference_batch_request.get_request_body())
        except ConnectionError as conn_err:
            raise type(conn_err)(str(conn_err) + ' Connection error, reference was not added to weaviate.').with_traceback(
                sys.exc_info()[2])

        if response.status_code == 200:
            return
        elif response.status_code == 401:
            raise UnauthorizedRequest401Exception
        elif response.status_code == 403:
            raise ForbiddenRequest403Exception
        elif response.status_code == 422:
            raise SemanticError422Exception
        elif response.status_code == 500:
            raise ServerError500Exception(response.json())
        else:
            raise UnexpectedStatusCodeException(json())

    # Returns true if a thing exists in weaviate
    def thing_exists(self, uuid_thing):
        response = self._get_thing_response(uuid_thing)

        if response.status_code == 200:
            return True
        elif response.status_code == 404:
            return False
        else:
            raise UnexpectedStatusCodeException(response.json())

    # Gets a thing as dict
    def get_thing(self, uuid_thing, meta=False):
        response = self._get_thing_response(uuid_thing, meta)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return None
        else:
            raise UnexpectedStatusCodeException(response.json())

    # Returns the response object
    def _get_thing_response(self, uuid_thing, meta=False):
        params = {}
        if meta:
            params['meta'] = True
        if not isinstance(uuid_thing, str):
            uuid_thing = str(uuid_thing)
        try:
            response = self.connection.run_rest("/things/"+uuid_thing, REST_METHOD_GET, params=params)
        except ConnectionError as conn_err:
            raise type(conn_err)(str(conn_err) + ' Connection error not sure if thing exists').with_traceback(sys.exc_info()[2])
        else:
            return response

    # Retrieves the vector representation of the given word
    # The word can be CamelCased for a compound vector
    # Returns the vector or throws and error, the vector might be empty if the c11y does not contain it
    def get_c11y_vector(self, word):
        path = "/c11y/words/" + word
        try:
            response = self.connection.run_rest(path, REST_METHOD_GET)
        except AttributeError:

            raise
        except Exception as e:
            raise type(e)(
                str(e) + ' Unexpected exception.').with_traceback(
                sys.exc_info()[2])
        else:
            if response.status_code == 200:
                return response.json()
            else:
                raise UnexpectedStatusCodeException(response.json())

    # Create the schema at the weaviate instance
    # schema can either be the path to a json file, a url of a json file or a dict
    # throws exceptions:
    # - ValueError if input is wrong
    # - IOError if file could not be read
    def create_schema(self, schema):
        loaded_schema = None

        # check if things files is url
        if schema == None:
            raise TypeError("Schema is None")

        if isinstance(schema, dict):
            # Schema is already a dict
            loaded_schema = schema
        elif isinstance(schema, str):

            if validators.url(schema):
                # Schema is URL
                f = requests.get(schema)
                if f.status_code == 200:
                    loaded_schema = f.json()
                else:
                    raise ValueError("Could not download file")

            elif not os.path.isfile(schema):
                # Schema is neither file nor URL
                raise ValueError("No schema file found at location")
            else:
                # Schema is file
                try:
                    with open(schema, 'r') as file:
                        loaded_schema = json.load(file)
                except IOError:
                    raise
        else:
            raise TypeError("Schema is not of a supported type. Supported types are url or file path as string or schema as dict.")

        # TODO validate the schema e.g. small parser?

        if SCHEMA_CLASS_TYPE_THINGS in loaded_schema:
            self._create_class(SCHEMA_CLASS_TYPE_THINGS, loaded_schema[SCHEMA_CLASS_TYPE_THINGS]["classes"])
        if SCHEMA_CLASS_TYPE_ACTIONS in loaded_schema:
            self._create_class(SCHEMA_CLASS_TYPE_ACTIONS, loaded_schema[SCHEMA_CLASS_TYPE_ACTIONS]["classes"])
        if SCHEMA_CLASS_TYPE_THINGS in loaded_schema:
            self._create_properties(SCHEMA_CLASS_TYPE_THINGS, loaded_schema[SCHEMA_CLASS_TYPE_THINGS]["classes"])
        if SCHEMA_CLASS_TYPE_ACTIONS in loaded_schema:
            self._create_properties(SCHEMA_CLASS_TYPE_ACTIONS, loaded_schema[SCHEMA_CLASS_TYPE_ACTIONS]["classes"])


    # Create all the classes in the list
    # This function does not create properties,
    # to avoid references to classes that do not yet exist
    # Takes:
    # - schema_class_type which can be found as constants in this file
    # - schema_classes_list a list of classes as it is found in a schema json description
    def _create_class(self, schema_class_type, schema_classes_list):

        for weaviate_class in schema_classes_list:

            schema_class = {
                "class": weaviate_class['class'],
                "description": weaviate_class['description'],
                "properties": [],
                "keywords": []
            }

            # Add the item
            response = self.connection.run_rest("/schema/"+schema_class_type, REST_METHOD_POST, schema_class)
            if response.status_code != 200:
                raise UnexpectedStatusCodeException(response.json())

    def _create_properties(self, schema_class_type, schema_classes_list):
        for schema_class in schema_classes_list:
            for property in schema_class["properties"]:

                # create the property object
                schema_property = {
                    "dataType": [],
                    "cardinality": property["cardinality"],
                    "description": property["description"],
                    "name": property["name"]
                }

                # add the dataType(s)
                for datatype in property["dataType"]:
                    schema_property["dataType"].append(datatype)

                # add keywords
                if "keywords" in property:
                    schema_property["keywords"] = property["keywords"]

                path = "/schema/"+schema_class_type+"/"+schema_class["class"]+"/properties"
                response = self.connection.run_rest(path, REST_METHOD_POST, schema_property)
                if response.status_code != 200:
                    raise UnexpectedStatusCodeException(response.json())

    # Starts a knn classification based on the given parameters
    # Returns a dict with the answer from weaviate
    def start_knn_classification(self, schema_class_name, k, based_on_properties, classify_properties):
        if not isinstance(schema_class_name, str):
            raise ValueError("Schema class name must be of type string")
        if not isinstance(k, int):
            raise ValueError("K must be of type integer")
        if isinstance(based_on_properties, str):
            based_on_properties = [based_on_properties]
        if isinstance(classify_properties, str):
            classify_properties = [classify_properties]
        if not isinstance(based_on_properties, list):
            raise ValueError("Based on properties must be of type string or list of strings")
        if not isinstance(classify_properties, list):
            raise ValueError("Classify properties must be of type string or list of strings")

        payload = {
            "class": schema_class_name,
            "k": k,
            "basedOnProperties": based_on_properties,
            "classifyProperties": classify_properties
        }

        response = self.connection.run_rest("/classifications", REST_METHOD_POST, payload)

        if response.status_code == 201:
            return response.json()
        else:
            raise UnexpectedStatusCodeException(response.json())

    # Polls the current state of the given classification
    # Returns a dict containing the weaviate answer
    def get_knn_classification_status(self, classification_uuid):
        if not validators.uuid(classification_uuid):
            raise ValueError("Given UUID does not have a proper form")

        response = self.connection.run_rest("/classifications/"+classification_uuid, REST_METHOD_GET)
        if response.status_code == 200:
            return response.json()
        else:
            raise UnexpectedStatusCodeException(response.json())

    # Returns true if the classification has finished
    def is_classification_complete(self, classification_uuid):
        response = self.get_knn_classification_status(classification_uuid)
        if response["status"] == "completed":
            return True
        return False

    def is_reachable(self):
        """ Ping weaviate

        :return: True if weaviate could be reached False otherwise
        """
        try:
            response = self.connection.run_rest("/meta", REST_METHOD_GET)
        except ConnectionError:
            return False
        if response.status_code == 200:
            return True
        return False