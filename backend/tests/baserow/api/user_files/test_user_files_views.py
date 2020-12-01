import pytest
import responses
from unittest.mock import patch
from freezegun import freeze_time

from PIL import Image

from django.shortcuts import reverse
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import FileSystemStorage

from rest_framework.status import (
    HTTP_200_OK, HTTP_400_BAD_REQUEST, HTTP_413_REQUEST_ENTITY_TOO_LARGE
)

from baserow.core.models import UserFile


@pytest.mark.django_db
def test_upload_file(api_client, data_fixture, tmpdir):
    user, token = data_fixture.create_user_and_token(
        email='test@test.nl', password='password', first_name='Test1')

    response = api_client.post(
        reverse('api:user_files:upload_file'),
        format='multipart',
        HTTP_AUTHORIZATION=f'JWT {token}'
    )
    assert response.status_code == HTTP_400_BAD_REQUEST
    assert response.json()['error'] == 'ERROR_INVALID_FILE'

    response = api_client.post(
        reverse('api:user_files:upload_file'),
        data={'file': ''},
        format='multipart',
        HTTP_AUTHORIZATION=f'JWT {token}'
    )
    assert response.status_code == HTTP_400_BAD_REQUEST
    assert response.json()['error'] == 'ERROR_INVALID_FILE'

    old_limit = settings.USER_FILE_SIZE_LIMIT
    settings.USER_FILE_SIZE_LIMIT = 6
    response = api_client.post(
        reverse('api:user_files:upload_file'),
        data={'file': SimpleUploadedFile('test.txt', b'Hello World')},
        format='multipart',
        HTTP_AUTHORIZATION=f'JWT {token}'
    )
    settings.USER_FILE_SIZE_LIMIT = old_limit
    assert response.status_code == HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert response.json()['error'] == 'ERROR_FILE_SIZE_TOO_LARGE'
    assert response.json()['detail'] == (
        'The provided file is too large. Max 0MB is allowed.'
    )

    response = api_client.post(
        reverse('api:user_files:upload_file'),
        data={'file': 'not a file'},
        format='multipart',
        HTTP_AUTHORIZATION=f'JWT {token}'
    )
    assert response.status_code == HTTP_400_BAD_REQUEST
    assert response.json()['error'] == 'ERROR_INVALID_FILE'

    storage = FileSystemStorage(location=str(tmpdir), base_url='http://localhost')

    with patch('baserow.core.user_files.handler.default_storage', new=storage):
        with freeze_time('2020-01-01 12:00'):
            file = SimpleUploadedFile('test.txt', b'Hello World')
            response = api_client.post(
                reverse('api:user_files:upload_file'),
                data={'file': file},
                format='multipart',
                HTTP_AUTHORIZATION=f'JWT {token}'
            )

    response_json = response.json()
    assert response.status_code == HTTP_200_OK
    assert response_json['size'] == 11
    assert response_json['mime_type'] == 'text/plain'
    assert response_json['is_image'] is False
    assert response_json['image_width'] is None
    assert response_json['image_height'] is None
    assert response_json['uploaded_at'] == '2020-01-01T12:00:00Z'
    assert response_json['thumbnails'] is None
    assert response_json['original_name'] == 'test.txt'
    assert 'localhost:8000' in response_json['url']

    user_file = UserFile.objects.all().last()
    assert user_file.name == response_json['name']
    assert response_json['url'].endswith(response_json['name'])
    file_path = tmpdir.join('user_files', user_file.name)
    assert file_path.isfile()

    with patch('baserow.core.user_files.handler.default_storage', new=storage):
        file = SimpleUploadedFile('test.txt', b'Hello World')
        response_2 = api_client.post(
            reverse('api:user_files:upload_file'),
            data={'file': file},
            format='multipart',
            HTTP_AUTHORIZATION=f'JWT {token}'
        )

    # The old file should be provided.
    assert response_2.json()['name'] == response_json['name']
    assert response_json['original_name'] == 'test.txt'

    image = Image.new('RGB', (100, 140), color='red')
    file = SimpleUploadedFile('test.png', b'')
    image.save(file, format='PNG')
    file.seek(0)

    with patch('baserow.core.user_files.handler.default_storage', new=storage):
        response = api_client.post(
            reverse('api:user_files:upload_file'),
            data={'file': file},
            format='multipart',
            HTTP_AUTHORIZATION=f'JWT {token}'
        )

    response_json = response.json()
    assert response.status_code == HTTP_200_OK
    assert response_json['mime_type'] == 'image/png'
    assert response_json['is_image'] is True
    assert response_json['image_width'] == 100
    assert response_json['image_height'] == 140
    assert len(response_json['thumbnails']) == 1
    assert 'localhost:8000' in response_json['thumbnails']['tiny']['url']
    assert 'tiny' in response_json['thumbnails']['tiny']['url']
    assert response_json['thumbnails']['tiny']['width'] == 21
    assert response_json['thumbnails']['tiny']['height'] == 21
    assert response_json['original_name'] == 'test.png'

    user_file = UserFile.objects.all().last()
    file_path = tmpdir.join('user_files', user_file.name)
    assert file_path.isfile()
    file_path = tmpdir.join('thumbnails', 'tiny', user_file.name)
    assert file_path.isfile()
    thumbnail = Image.open(file_path.open('rb'))
    assert thumbnail.height == 21
    assert thumbnail.width == 21


@pytest.mark.django_db
@responses.activate
def test_upload_file_via_url(api_client, data_fixture, tmpdir):
    user, token = data_fixture.create_user_and_token(
        email='test@test.nl', password='password', first_name='Test1'
    )

    response = api_client.post(
        reverse('api:user_files:upload_via_url'),
        data={},
        HTTP_AUTHORIZATION=f'JWT {token}'
    )
    assert response.status_code == HTTP_400_BAD_REQUEST
    assert response.json()['error'] == 'ERROR_REQUEST_BODY_VALIDATION'

    response = api_client.post(
        reverse('api:user_files:upload_via_url'),
        data={'url': 'NOT_A_URL'},
        HTTP_AUTHORIZATION=f'JWT {token}'
    )
    assert response.status_code == HTTP_400_BAD_REQUEST
    assert response.json()['error'] == 'ERROR_REQUEST_BODY_VALIDATION'

    response = api_client.post(
        reverse('api:user_files:upload_via_url'),
        data={'url': 'http://localhost/test2.txt'},
        HTTP_AUTHORIZATION=f'JWT {token}'
    )
    assert response.status_code == HTTP_400_BAD_REQUEST
    assert response.json()['error'] == 'ERROR_FILE_URL_COULD_NOT_BE_REACHED'

    responses.add(
        responses.GET,
        'http://localhost/test.txt',
        body=b'Hello World',
        status=200,
        content_type="text/plain",
        stream=True,
    )

    old_limit = settings.USER_FILE_SIZE_LIMIT
    settings.USER_FILE_SIZE_LIMIT = 6
    response = api_client.post(
        reverse('api:user_files:upload_via_url'),
        data={'url': 'http://localhost/test.txt'},
        HTTP_AUTHORIZATION=f'JWT {token}'
    )
    assert response.status_code == HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert response.json()['error'] == 'ERROR_FILE_SIZE_TOO_LARGE'
    settings.USER_FILE_SIZE_LIMIT = old_limit

    storage = FileSystemStorage(location=str(tmpdir), base_url='http://localhost')

    with patch('baserow.core.user_files.handler.default_storage', new=storage):
        response = api_client.post(
            reverse('api:user_files:upload_via_url'),
            data={'url': 'http://localhost/test.txt'},
            HTTP_AUTHORIZATION=f'JWT {token}'
        )
        response_json = response.json()

    assert response.status_code == HTTP_200_OK
    assert response_json['size'] == 11
    assert response_json['mime_type'] == 'text/plain'
    assert response_json['is_image'] is False
    assert response_json['image_width'] is None
    assert response_json['image_height'] is None
    assert response_json['thumbnails'] is None
    assert response_json['original_name'] == 'test.txt'
    assert 'localhost:8000' in response_json['url']
    user_file = UserFile.objects.all().last()
    file_path = tmpdir.join('user_files', user_file.name)
    assert file_path.isfile()
