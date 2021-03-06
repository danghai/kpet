# Copyright (c) 2019 Red Hat, Inc. All rights reserved. This copyrighted
# material is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General Public
# License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
"""Database schema"""

import re
import os
import yaml

# pylint: disable=raising-format-tuple


# The type returned by re.compile(). Different between Python 2 and 3
# TODO Switch to just using re.Pattern once upgraded to Python 3.7 or later
_RE = type(re.compile(""))


def _get_re_error_type():
    """
    Get the type of the exception produced when an invalid regular expression
    is compiled.

    Returns:
        The "invalid regular expression" exception type.
    Raises:
        Exception   when the type cannot be discovered.
    """
    try:
        re.compile("(")
    except Exception as exc:    # pylint: disable=broad-except
        return type(exc)
    raise Exception("\"Invalid regex\" exception type not found")


# The exception type produced by re.compile() on invalid regex.
# Different between Python 2 and 3, and unavailable directly in Python 2
# TODO Switch to just using re.error once upgraded to Python 3.7 or later
_ReError = _get_re_error_type()


class Invalid(Exception):
    """Invalid data exception"""

    def __init__(self, fmt, *args):
        super().__init__(fmt.format(*args))

    def __str__(self):
        return super().__str__() + \
               (":\n" + str(self.__context__) if self.__context__ else "")


class Node:
    """
    Abstract node schema validating the data to be an instance of specified
    type and resolving to the same.
    """
    def __init__(self, data_type):
        """
        Initialize a node schema.

        Args:
            data_type:  The type the data should be instance of.
        """
        self.data_type = data_type

    def validate(self, data):
        """
        Validate data according to the schema.

        Args:
            The data to validate.

        Raises:
            Invalid:    The data didn't match the schema.
        """
        if not isinstance(data, self.data_type):
            raise Invalid("Invalid type: {}, expecting {}",
                          type(data).__name__, self.data_type.__name__)

    def recognize(self):
        """
        Recognize the schema - return the schema that resolved data would
        have.

        Returns:
            The schema the resolved data would have.
        """
        return self

    def resolve(self, data):
        """
        Resolve (validate and massage) data according to the schema.

        Args:
            data:   The data to resolve.

        Returns:
            The resolved data. Will match the recognized schema.
        """
        self.validate(data)
        return data


class Ancestry(Node):
    """
    A schema describing a succession of accepted schema versions and means to
    inherit the legacy data. Validates against one of the schema versions.
    Resolves to the data matching the last schema.
    """
    def __init__(self, *args):
        """
        Initialize an ancestry schema.

        Args:
            args:   A list of schemas and functions which could be used to
                    convert the data, in the order of succession. Can be mixed
                    in any order, except the first and the last items must be
                    schemas. Cannot be empty. Converter functions must accept
                    the original data argument and return the converted data.
        """
        assert args
        assert isinstance(args[0], Node)
        assert isinstance(args[-1], Node)
        super().__init__(object)
        self.ancestry = args

    def validate(self, data):
        super().validate(data)
        last_exc = None
        # For each schema/converter in the ancestry
        for item in self.ancestry:
            # If it's a schema
            if isinstance(item, Node):
                try:
                    item.validate(data)
                    return
                except Invalid as exc:
                    last_exc = exc
        raise last_exc

    def recognize(self):
        return self.ancestry[-1].recognize()

    def resolve(self, data):
        # Last valid schema
        last_valid_schema = None
        # Last validation failure
        last_exc = None
        # We find the first matching schema, then proceed converting and
        # validating until we get to the last schema.
        # For each schema/converter in the ancestry
        for item in self.ancestry:
            # If it's a schema
            if isinstance(item, Node):
                try:
                    # Validate the data
                    item.validate(data)
                    last_valid_schema = item
                except Invalid as exc:
                    # If we already got a valid schema
                    if last_valid_schema:
                        raise exc
                    last_exc = exc
            # Else it's a conversion function, and if we found valid schema
            elif last_valid_schema:
                # Convert the data for the next schema/converter
                data = item(data)
        # If no schema matched
        if not last_valid_schema:
            raise last_exc
        # Resolve the data with the last schema
        return last_valid_schema.resolve(data)


class String(Node):
    """String schema"""
    def __init__(self):
        super().__init__(str)


class Int(Node):
    """Integer number schema"""
    def __init__(self):
        super().__init__(int)


class Float(Node):
    """Floating-point number schema"""
    def __init__(self):
        super().__init__(float)


class Boolean(Node):
    """Boolean schema"""
    def __init__(self):
        super().__init__(bool)


class Regex(String):
    """
    Regular expression string schema.
    """
    def validate(self, data):
        super().validate(data)
        try:
            re.compile(data)
        except _ReError:
            raise Invalid("Invalid regular expression")

    def recognize(self):
        return Node(_RE)

    def resolve(self, data):
        self.validate(data)
        return re.compile(data)


class RelativeFilePath(String):
    """
    Relative file path schema, resolved to the same schema and absolute file
    path.
    """
    def resolve(self, data):
        self.validate(data)
        return os.path.abspath(data)


class YAMLFile(String):
    """
    YAML file path schema, resolved to the file contents according to
    specified schema.
    """
    def __init__(self, contents_schema):
        assert isinstance(contents_schema, Node)
        super().__init__()
        self.contents_schema = contents_schema

    def recognize(self):
        return self.contents_schema.recognize()

    def resolve(self, data):
        self.validate(data)
        file_path = os.path.abspath(data)

        # Load the data
        with open(file_path, "r") as resolved_data_file:
            resolved_data = yaml.load(resolved_data_file)

        # Resolve loaded data
        try:
            return self.contents_schema.resolve(resolved_data)
        except Invalid:
            raise Invalid("Invalid contents of {}", file_path)


class ScopedYAMLFile(YAMLFile):
    """
    YAML file path schema, resolved to file contents according to specified
    schema, changes the current directory to the file's directory when
    resolving the contents.
    """
    def resolve(self, data):
        self.validate(data)
        file_path = os.path.abspath(data)
        dir_path = os.path.dirname(file_path)

        # Load the data
        with open(file_path, "r") as resolved_data_file:
            resolved_data = yaml.load(resolved_data_file)

        # Validate and resolve loaded data
        orig_dir_path = os.getcwd()
        os.chdir(dir_path)
        try:
            return self.contents_schema.resolve(resolved_data)
        except Invalid:
            raise Invalid("Invalid contents of {}", file_path)
        finally:
            os.chdir(orig_dir_path)


class List(Node):
    """
    List schema, with every element matching a single specified schema.
    """
    def __init__(self, element_schema):
        assert isinstance(element_schema, Node)
        super().__init__(list)
        self.element_schema = element_schema

    def validate(self, data):
        super().validate(data)
        for index, value in enumerate(data):
            try:
                self.element_schema.validate(value)
            except Invalid:
                raise Invalid("Invalid value at index {}", index)

    def recognize(self):
        return List(self.element_schema.recognize())

    def resolve(self, data):
        self.validate(data)
        return [self.element_schema.resolve(value) for value in data]


class Dict(Node):
    """
    Dictionary schema, with string keys and every value matching a single
    specified schema.
    """
    def __init__(self, value_schema):
        assert isinstance(value_schema, Node)
        super().__init__(dict)
        self.value_schema = value_schema

    def validate(self, data):
        super().validate(data)
        for key, value in data.items():
            if not isinstance(key, str):
                raise Invalid("Key \"{}\" is {}, expecting a string",
                              key, type(key).__name__)
            try:
                self.value_schema.validate(value)
            except Invalid:
                raise Invalid("Invalid value with key \"{}\"", key)

    def recognize(self):
        return Dict(self.value_schema.recognize())

    def resolve(self, data):
        self.validate(data)
        resolved_data = {}
        for key, value in data.items():
            resolved_data[key] = self.value_schema.resolve(value)
        return resolved_data


class Struct(Dict):
    """
    Dictionary schema, with string keys and each key having values with its
    own schema.
    """
    def __init__(self, required=None, optional=None):
        """
        Initialize a struct schema.

        Args:
            required:   A dictionary of keys required to be present in the
                        dictionary, mapped to their value schemas.
            optional:   A dictionary of keys that can be present in the
                        dictionary, but are not required, mapped to their
                        value schemas.
        """
        assert required is None or isinstance(required, dict)
        assert optional is None or isinstance(optional, dict)
        super().__init__(Node(object))
        if required is None:
            required = {}
        if optional is None:
            optional = {}
        for key, value in required.items():
            assert isinstance(key, str)
            assert isinstance(value, Node)
        for key, value in optional.items():
            assert isinstance(key, str)
            assert isinstance(value, Node)
        assert not (set(required.keys()) & set(optional.keys())), \
            "Some keys are both required and optional"
        self.required = required
        self.optional = optional

    def validate(self, data):
        super().validate(data)
        for name, schema in self.required.items():
            if name not in data:
                raise Invalid("Member \"{}\" is missing", name)
            value = data[name]
            try:
                schema.validate(value)
            except Invalid:
                raise Invalid("Member \"{}\" is invalid", name)
        for name, schema in self.optional.items():
            if name in data:
                value = data[name]
                try:
                    schema.validate(value)
                except Invalid:
                    raise Invalid("Member \"{}\" is invalid", name)
        for key in data.keys():
            if key not in self.required and key not in self.optional:
                raise Invalid("Unexpected member \"{}\" encountered", key)

    def recognize(self):
        recognized_required = {}
        recognized_optional = {}

        for name, schema in self.required.items():
            recognized_required[name] = schema.recognize()
        for name, schema in self.optional.items():
            recognized_optional[name] = schema.recognize()

        return Struct(required=recognized_required,
                      optional=recognized_optional)

    def resolve(self, data):
        self.validate(data)
        resolved_data = {}

        for name, schema in self.required.items():
            resolved_data[name] = schema.resolve(data[name])
        for name, schema in self.optional.items():
            if name in data:
                resolved_data[name] = schema.resolve(data[name])

        return resolved_data


class StrictStruct(Struct):
    """
    Struct schema with required keys only.
    """
    def __init__(self, **kwargs):
        super().__init__(required=kwargs)


class Class(Node):
    """
    Class instance schema, resolves to a class instance with (arbitrary) data
    as the creation argument.
    """
    def __init__(self, instance_type):
        super().__init__(object)
        self.instance_type = instance_type

    def recognize(self):
        return Node(object)

    def resolve(self, data):
        self.validate(data)
        return self.instance_type(data)
