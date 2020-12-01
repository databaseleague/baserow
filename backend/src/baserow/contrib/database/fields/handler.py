import logging
from copy import deepcopy

from django.db import connections
from django.db.utils import ProgrammingError, DataError
from django.conf import settings

from baserow.core.exceptions import UserNotInGroupError
from baserow.core.utils import extract_allowed, set_allowed_attrs
from baserow.contrib.database.db.schema import lenient_schema_editor
from baserow.contrib.database.views.handler import ViewHandler

from .exceptions import (
    PrimaryFieldAlreadyExists, CannotDeletePrimaryField, CannotChangeFieldType,
    FieldDoesNotExist, IncompatiblePrimaryFieldTypeError
)
from .registries import field_type_registry, field_converter_registry
from .models import Field


logger = logging.getLogger(__name__)


class FieldHandler:
    def get_field(self, user, field_id, field_model=None, base_queryset=None):
        """
        Selects a field with a given id from the database.

        :param user: The user on whose behalf the field is requested.
        :type user: User
        :param field_id: The identifier of the field that must be returned.
        :type field_id: int
        :param field_model: If provided that model's objects are used to select the
            field. This can for example be useful when you want to select a TextField or
            other child of the Field model.
        :type field_model: Field
        :param base_queryset: The base queryset from where to select the field.
            object. This can for example be used to do a `select_related`. Note that
            if this is used the `field_model` parameter doesn't work anymore.
        :type base_queryset: Queryset
        :raises FieldDoesNotExist: When the field with the provided id does not exist.
        :raises UserNotInGroupError: When the user does not belong to the field.
        :return: The requested field instance of the provided id.
        :rtype: Field
        """

        if not field_model:
            field_model = Field

        if not base_queryset:
            base_queryset = field_model.objects

        try:
            field = base_queryset.select_related('table__database__group').get(
                id=field_id
            )
        except Field.DoesNotExist:
            raise FieldDoesNotExist(f'The field with id {field_id} does not exist.')

        group = field.table.database.group
        if not group.has_user(user):
            raise UserNotInGroupError(user, group)

        return field

    def create_field(self, user, table, type_name, primary=False,
                     do_schema_change=True, **kwargs):
        """
        Creates a new field with the given type for a table.

        :param user: The user on whose behalf the field is created.
        :type user: User
        :param table: The table that the field belongs to.
        :type table: Table
        :param type_name: The type name of the field. Available types can be found in
            the field_type_registry.
        :type type_name: str
        :param primary: Every table needs at least a primary field which cannot be
            deleted and is a representation of the whole row.
        :type primary: bool
        :param do_schema_change: Indicates whether or not he actual database schema
            change has be made.
        :type do_schema_change: bool
        :param kwargs: The field values that need to be set upon creation.
        :type kwargs: object
        :raises UserNotInGroupError: When the user does not belong to the related group.
        :raises PrimaryFieldAlreadyExists: When we try to create a primary field,
            but one already exists.
        :return: The created field instance.
        :rtype: Field
        """

        group = table.database.group
        if not group.has_user(user):
            raise UserNotInGroupError(user, group)

        # Because only one primary field per table can exist and we have to check if one
        # already exists. If so the field cannot be created and an exception is raised.
        if primary and Field.objects.filter(table=table, primary=True).exists():
            raise PrimaryFieldAlreadyExists(f'A primary field already exists for the '
                                            f'table {table}.')

        # Figure out which model to use and which field types are allowed for the given
        # field type.
        field_type = field_type_registry.get(type_name)
        model_class = field_type.model_class
        allowed_fields = ['name'] + field_type.allowed_fields
        field_values = extract_allowed(kwargs, allowed_fields)
        last_order = model_class.get_last_order(table)

        field_values = field_type.prepare_values(field_values, user)
        field_type.before_create(table, primary, field_values, last_order, user)

        instance = model_class.objects.create(table=table, order=last_order,
                                              primary=primary, **field_values)

        # Add the field to the table schema.
        connection = connections[settings.USER_TABLE_DATABASE]
        with connection.schema_editor() as schema_editor:
            to_model = table.get_model(field_ids=[], fields=[instance])
            model_field = to_model._meta.get_field(instance.db_column)

            if do_schema_change:
                schema_editor.add_field(to_model, model_field)

        field_type.after_create(instance, to_model, user, connection)

        return instance

    def update_field(self, user, field, new_type_name=None, **kwargs):
        """
        Updates the values of the given field, if provided it is also possible to change
        the type.

        :param user: The user on whose behalf the table is updated.
        :type user: User
        :param field: The field instance that needs to be updated.
        :type field: Field
        :param new_type_name: If the type needs to be changed it can be provided here.
        :type new_type_name: str
        :param kwargs: The field values that need to be updated
        :type kwargs: object
        :raises ValueError: When the provided field is not an instance of Field.
        :raises UserNotInGroupError: When the user does not belong to the related group.
        :raises CannotChangeFieldType: When the database server responds with an
            error while trying to change the field type. This should rarely happen
            because of the lenient schema editor, which replaces the value with null
            if it ould not be converted.
        :return: The updated field instance.
        :rtype: Field
        """

        if not isinstance(field, Field):
            raise ValueError('The field is not an instance of Field.')

        group = field.table.database.group
        if not group.has_user(user):
            raise UserNotInGroupError(user, group)

        old_field = deepcopy(field)
        field_type = field_type_registry.get_by_model(field)
        old_field_type = field_type
        from_model = field.table.get_model(field_ids=[], fields=[field])
        from_field_type = field_type.type

        # If the provided field type does not match with the current one we need to
        # migrate the field to the new type. Because the type has changed we also need
        # to remove all view filters.
        if new_type_name and field_type.type != new_type_name:
            field_type = field_type_registry.get(new_type_name)

            if field.primary and not field_type.can_be_primary_field:
                raise IncompatiblePrimaryFieldTypeError(new_type_name)

            new_model_class = field_type.model_class
            field.change_polymorphic_type_to(new_model_class)

            # If the field type changes it could be that some dependencies,
            # like filters or sortings need to be changed.
            ViewHandler().field_type_changed(field)

        allowed_fields = ['name'] + field_type.allowed_fields
        field_values = extract_allowed(kwargs, allowed_fields)

        field_values = field_type.prepare_values(field_values, user)
        field_type.before_update(old_field, field_values, user)

        field = set_allowed_attrs(field_values, allowed_fields, field)
        field.save()

        connection = connections[settings.USER_TABLE_DATABASE]

        # If no converter is found we are going to convert to field using the
        # lenient schema editor which will alter the field's type and set the data
        # value to null if it can't be converted.
        to_model = field.table.get_model(field_ids=[], fields=[field])
        from_model_field = from_model._meta.get_field(field.db_column)
        to_model_field = to_model._meta.get_field(field.db_column)

        # Before a field is updated we are going to call the before_schema_change
        # method of the old field because some cleanup of related instances might
        # need to happen.
        old_field_type.before_schema_change(old_field, field, from_model, to_model,
                                            from_model_field, to_model_field, user)

        # Try to find a data converter that can be applied.
        converter = field_converter_registry.find_applicable_converter(
            from_model,
            old_field,
            field
        )

        if converter:
            # If a field data converter is found we are going to use that one to alter
            # the field and maybe do some data conversion.
            converter.alter_field(
                old_field,
                field,
                from_model,
                to_model,
                from_model_field,
                to_model_field,
                user,
                connection
            )
        else:
            # If no field converter is found we are going to alter the field using the
            # the lenient schema editor.
            with lenient_schema_editor(
                connection,
                field_type.get_alter_column_type_function(connection, field)
            ) as schema_editor:
                try:
                    schema_editor.alter_field(from_model, from_model_field,
                                              to_model_field)
                except (ProgrammingError, DataError):
                    # If something is going wrong while changing the schema we will
                    # just raise a specific exception. In the future we want to have
                    # some sort of converter abstraction where the values of certain
                    # types can be converted to another value.
                    message = f'Could not alter field when changing field type ' \
                              f'{from_field_type} to {new_type_name}.'
                    logger.error(message)
                    raise CannotChangeFieldType(message)

        from_model_field_type = from_model_field.db_parameters(connection)['type']
        to_model_field_type = to_model_field.db_parameters(connection)['type']
        altered_column = from_model_field_type != to_model_field_type

        field_type.after_update(old_field, field, from_model, to_model, user,
                                connection, altered_column)

        return field

    def delete_field(self, user, field):
        """
        Deletes an existing field if it is not a primary field.

        :param user: The user on whose behalf the table is created.
        :type user: User
        :param field: The field instance that needs to be deleted.
        :type field: Field
        :raises ValueError: When the provided field is not an instance of Field.
        :raises UserNotInGroupError: When the user does not belong to the related group.
        :raises CannotDeletePrimaryField: When we try to delete the primary field
            which cannot be deleted.
        """

        if not isinstance(field, Field):
            raise ValueError('The field is not an instance of Field')

        group = field.table.database.group
        if not group.has_user(user):
            raise UserNotInGroupError(user, group)

        if field.primary:
            raise CannotDeletePrimaryField('Cannot delete the primary field of a '
                                           'table.')

        field = field.specific
        field_type = field_type_registry.get_by_model(field)

        # Remove the field from the table schema.
        connection = connections[settings.USER_TABLE_DATABASE]
        with connection.schema_editor() as schema_editor:
            from_model = field.table.get_model(field_ids=[], fields=[field])
            model_field = from_model._meta.get_field(field.db_column)
            schema_editor.remove_field(from_model, model_field)

        field.delete()

        # After the field is deleted we are going to to call the after_delete method of
        # the field type because some instance cleanup might need to happen.
        field_type.after_delete(field, from_model, user, connection)
