#!/usr/bin/env python3
"""Generate production secrets for Official-backend."""

import secrets


def generate_secret_key():
    """Generate a 64-character hex secret."""
    return secrets.token_hex(32)


def generate_pgsql_password(length=32):
    """Generate a URL-safe database password."""
    alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def main():
    """Print env-ready values for the required production secrets."""
    values = {
        'SECRET_KEY': generate_secret_key(),
        'FILE_DOWNLOAD_TOKEN_SECRET': generate_secret_key(),
        'PGSQL_PASSWORD': generate_pgsql_password(),
    }

    for key, value in values.items():
        print(f'{key}={value}')


if __name__ == '__main__':
    main()
