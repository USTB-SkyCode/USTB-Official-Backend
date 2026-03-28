#!/usr/bin/env python3
"""
Flask 密钥生成工具 - 直接生成密钥到控制台
"""
import secrets

def generate_secret_key():
    """生成64字符的十六进制密钥"""
    return secrets.token_hex(32)

def main():
    """生成并输出密钥"""
    key = generate_secret_key()
    print(key)

if __name__ == "__main__":
    main()
