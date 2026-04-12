import os


TRUTHY_ENV_VALUES = ('1', 'true', 'yes', 'on')


def get_env_str(name, default=''):
    value = os.environ.get(name)
    if value is None:
        return default

    normalized = value.strip()
    return normalized if normalized != '' else default


def get_env_int(name, default):
    value = os.environ.get(name)
    if value is None or value.strip() == '':
        return default

    try:
        return int(value)
    except ValueError:
        return default


def get_env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value.strip() == '':
        return default

    return value.strip().lower() in TRUTHY_ENV_VALUES


def get_env_csv(name, default=''):
    value = get_env_str(name, default)
    return [item.strip() for item in value.split(',') if item.strip()]


def first_non_empty_env(*names, default=''):
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            normalized = value.strip()
            if normalized != '':
                return normalized

    return default


def resolve_required_env(name, *, strict_required=False, fallback=''):
    value = os.environ.get(name)
    if value is not None:
        normalized = value.strip()
        if normalized != '':
            return normalized

    if strict_required:
        raise RuntimeError(f'Missing required environment variable: {name}')

    return fallback