# -*- coding: utf-8 -*-
"""
Production Configurations

"""

from .common import *  # noqa
from environs import Env, EnvValidationError
from core.swiftmanager import SwiftManager

# Normally you should not import ANYTHING from Django directly
# into your settings, but ImproperlyConfigured is an exception.
from django.core.exceptions import ImproperlyConfigured


# Environment variables-based secrets
env = Env()
env.read_env()  # also read .env file, if it exists


def get_secret(setting, secret_type=env):
    """Get the secret variable or return explicit exception."""
    try:
        return secret_type(setting)
    except EnvValidationError as e:
        raise ImproperlyConfigured(str(e))


# SECRET CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/2.2/ref/settings/#secret-key
# Raises ImproperlyConfigured exception if DJANGO_SECRET_KEY not set
SECRET_KEY = get_secret('DJANGO_SECRET_KEY')


# SITE CONFIGURATION
# ------------------------------------------------------------------------------
# Hosts/domain names that are valid for this site
# See https://docs.djangoproject.com/en/2.2/ref/settings/#allowed-hosts
ALLOWED_HOSTS = get_secret('DJANGO_ALLOWED_HOSTS', env.list)
# END SITE CONFIGURATION


# DATABASE CONFIGURATION
# ------------------------------------------------------------------------------
# Raises ImproperlyConfigured exception if DATABASE_URL not set
DATABASES['default']['NAME'] = get_secret('MYSQL_DATABASE')
DATABASES['default']['USER'] = get_secret('MYSQL_USER')
DATABASES['default']['PASSWORD'] = get_secret('MYSQL_PASSWORD')
DATABASES['default']['HOST'] = get_secret('DATABASE_HOST')
DATABASES['default']['PORT'] = get_secret('DATABASE_PORT')


# SWIFT SERVICE CONFIGURATION
# ------------------------------------------------------------------------------
DEFAULT_FILE_STORAGE = 'swift.storage.SwiftStorage'
SWIFT_AUTH_URL = get_secret('SWIFT_AUTH_URL')
SWIFT_USERNAME = get_secret('SWIFT_USERNAME')
SWIFT_KEY = get_secret('SWIFT_KEY')
SWIFT_CONTAINER_NAME = get_secret('SWIFT_CONTAINER_NAME')
SWIFT_CONNECTION_PARAMS = {'user': SWIFT_USERNAME,
                           'key': SWIFT_KEY,
                           'authurl': SWIFT_AUTH_URL}
try:
    SwiftManager(SWIFT_CONTAINER_NAME, SWIFT_CONNECTION_PARAMS).create_container()
except Exception as e:
    raise ImproperlyConfigured(str(e))


# CHRIS STORE SERVICE CONFIGURATION
CHRIS_STORE_URL = get_secret('CHRIS_STORE_URL')


# LOGGING CONFIGURATION
# See http://docs.djangoproject.com/en/2.2/topics/logging for
# more details on how to customize your logging configuration.
ADMINS = [('FNNDSC Developers', 'dev@babymri.org')]
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '[%(levelname)s][%(module)s %(process)d %(thread)d] %(message)s'
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
    },
    'loggers': {
        '': {  # root logger
            'level': 'INFO',
            'handlers': ['console'],
        }
    }
}


# STATIC FILES (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/2.2/howto/static-files/
STATIC_ROOT = get_secret('STATIC_ROOT')


# CORSHEADERS
# ------------------------------------------------------------------------------
CORS_ORIGIN_ALLOW_ALL = get_secret('DJANGO_CORS_ORIGIN_ALLOW_ALL', env.bool)
CORS_ORIGIN_WHITELIST = get_secret('DJANGO_CORS_ORIGIN_WHITELIST', env.list)


# Celery settings

#CELERY_BROKER_URL = 'amqp://guest:guest@localhost'
CELERY_BROKER_URL = get_secret('CELERY_BROKER_URL')

#: Only add pickle to this list if your broker is secured
#: from unwanted access (see userguide/security.html)
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'


# REVERSE PROXY
# ------------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = get_secret('DJANGO_SECURE_PROXY_SSL_HEADER', env.list)
SECURE_PROXY_SSL_HEADER = tuple(SECURE_PROXY_SSL_HEADER) if SECURE_PROXY_SSL_HEADER else None
USE_X_FORWARDED_HOST = get_secret('DJANGO_USE_X_FORWARDED_HOST', env.bool)

