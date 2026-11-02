"""Use verified TLS even when a standalone Python install lacks system CAs."""

import os
import ssl

import certifi


def tls_context():
    return ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
