"""Official Cobblestone AES-256 streaming recipe with versioned DEK wrapping.

The complete plaintext is returned only after successful finalize; callers never
stream unauthenticated bytes to HTTP. Plaintext/ciphertext are strictly bounded.
"""
import base64
import json
import os

from cryptography.cobblestone import Cobblestone256Encryptor, Cobblestone256Decryptor
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_DOCUMENT_BYTES = 50_000_000
CHUNK = 64 * 1024
ENVELOPE_VERSION = 1


class AuthenticationFailure(Exception):
    pass


def context(scope):
    if set(scope) != {'organization_id', 'project_id', 'document_id', 'object_kind'} or any(
        not isinstance(v, str) or not v or len(v) > 64 for v in scope.values()
    ):
        raise AuthenticationFailure()
    return json.dumps({'envelope_version': ENVELOPE_VERSION, **scope}, sort_keys=True,
                      separators=(',', ':')).encode()


class EnvelopeCipher:
    def __init__(self, keys, active_version):
        if active_version not in keys or any(not isinstance(k, bytes) or len(k) != 32 for k in keys.values()):
            raise ValueError('Encryption requires versioned 256-bit master keys')
        self.keys, self.active_version = dict(keys), active_version

    def _wrap(self, key, scope, version):
        nonce = os.urandom(12)
        wrapped = AESGCM(self.keys[version]).encrypt(nonce, key, context(scope))
        return {'version': ENVELOPE_VERSION, 'algorithm': 'cobblestone256', 'master_key_version': version,
                'nonce': base64.b64encode(nonce).decode(), 'wrapped_key': base64.b64encode(wrapped).decode()}

    def _unwrap(self, envelope, scope):
        try:
            if set(envelope) != {'version', 'algorithm', 'master_key_version', 'nonce', 'wrapped_key'} or envelope['version'] != ENVELOPE_VERSION or envelope['algorithm'] != 'cobblestone256':
                raise AuthenticationFailure()
            nonce = base64.b64decode(envelope['nonce'], validate=True)
            wrapped = base64.b64decode(envelope['wrapped_key'], validate=True)
            if len(nonce) != 12 or len(wrapped) != 48:
                raise AuthenticationFailure()
            return AESGCM(self.keys[envelope['master_key_version']]).decrypt(nonce, wrapped, context(scope))
        except Exception:
            raise AuthenticationFailure() from None

    def encrypt(self, original, scope):
        if not 0 < len(original) <= MAX_DOCUMENT_BYTES:
            raise ValueError('Document byte limit exceeded')
        key = Cobblestone256Encryptor.generate_key()
        encryptor = Cobblestone256Encryptor(key, context(scope))
        chunks = [encryptor.update(original[offset:offset + CHUNK]) for offset in range(0, len(original), CHUNK)]
        chunks.append(encryptor.finalize())
        return b''.join(chunks), self._wrap(key, scope, self.active_version)

    def decrypt(self, ciphertext, envelope, scope, expected_size):
        if type(expected_size) is not int or not 0 < expected_size <= MAX_DOCUMENT_BYTES or len(ciphertext) > MAX_DOCUMENT_BYTES + 100_000:
            raise AuthenticationFailure()
        try:
            decryptor = Cobblestone256Decryptor(self._unwrap(envelope, scope), context(scope))
            result = bytearray()
            for offset in range(0, len(ciphertext), CHUNK):
                result.extend(decryptor.update(ciphertext[offset:offset + CHUNK]))
                if len(result) > expected_size:
                    raise AuthenticationFailure()
            result.extend(decryptor.finalize())
            if len(result) != expected_size:
                raise AuthenticationFailure()
            return bytes(result)
        except Exception:
            raise AuthenticationFailure() from None

    def rewrap(self, envelope, scope, version):
        if version not in self.keys:
            raise AuthenticationFailure()
        return self._wrap(self._unwrap(envelope, scope), scope, version)
