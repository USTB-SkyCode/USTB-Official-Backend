#!/usr/bin/env python3
"""Persist runtime secrets for the production compose stack."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


SECRET_DIR = Path(os.environ.get('RUNTIME_SECRET_DIR', '/run-secrets'))
ENV_FILE = SECRET_DIR / 'runtime-secrets.env'
PGSQL_PASSWORD_FILE = SECRET_DIR / 'PGSQL_PASSWORD'
SECRET_KEY_FILE = SECRET_DIR / 'SECRET_KEY'
FILE_DOWNLOAD_TOKEN_SECRET_FILE = SECRET_DIR / 'FILE_DOWNLOAD_TOKEN_SECRET'
PASSWORD_ALPHABET = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_'


def generate_secret_key() -> str:
    return secrets.token_hex(32)


def generate_pgsql_password(length: int = 32) -> str:
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def read_secret(path: Path) -> str:
    if not path.exists():
        return ''

    return path.read_text(encoding='utf-8').strip()


def write_secret(path: Path, value: str) -> None:
    path.write_text(value + '\n', encoding='utf-8')
    os.chmod(path, 0o600)


def resolve_secret(name: str, path: Path, generator) -> str:
    persisted = read_secret(path)
    if persisted:
        return persisted

    configured = os.environ.get(name, '').strip()
    if configured:
        return configured

    return generator()


def main() -> None:
    SECRET_DIR.mkdir(parents=True, exist_ok=True)

    values = {
        'SECRET_KEY': resolve_secret('SECRET_KEY', SECRET_KEY_FILE, generate_secret_key),
        'FILE_DOWNLOAD_TOKEN_SECRET': resolve_secret(
            'FILE_DOWNLOAD_TOKEN_SECRET',
            FILE_DOWNLOAD_TOKEN_SECRET_FILE,
            generate_secret_key,
        ),
        'PGSQL_PASSWORD': resolve_secret('PGSQL_PASSWORD', PGSQL_PASSWORD_FILE, generate_pgsql_password),
    }

    write_secret(SECRET_KEY_FILE, values['SECRET_KEY'])
    write_secret(FILE_DOWNLOAD_TOKEN_SECRET_FILE, values['FILE_DOWNLOAD_TOKEN_SECRET'])
    write_secret(PGSQL_PASSWORD_FILE, values['PGSQL_PASSWORD'])

    ENV_FILE.write_text(
        ''.join(f'{key}={value}\n' for key, value in values.items()),
        encoding='utf-8',
    )
    os.chmod(ENV_FILE, 0o600)

    print(f'Runtime secrets ready in {SECRET_DIR}')


if __name__ == '__main__':
    main()