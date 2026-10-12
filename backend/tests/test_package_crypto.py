import pytest


def crypto():
    try:
        from app.packages.crypto import EnvelopeCipher, AuthenticationFailure
    except ImportError:
        pytest.fail('Authenticated encrypted document storage is missing')
    return EnvelopeCipher, AuthenticationFailure


def test_roundtrip_is_randomized_and_authenticated_before_return():
    cls, rejected = crypto()
    cipher = cls({'v1': bytes(range(32))}, 'v1')
    scope = {'organization_id': 'org', 'project_id': 'project', 'document_id': 'doc', 'object_kind': 'original'}
    original = b'exact original\x00 bytes' * 20000
    first, envelope = cipher.encrypt(original, scope)
    second, other = cipher.encrypt(original, scope)
    assert first != second and envelope != other and original[:100] not in first
    assert cipher.decrypt(first, envelope, scope, len(original)) == original
    for changed in [first[:-1], first + b'garbage', first[:50] + bytes([first[50] ^ 1]) + first[51:]]:
        with pytest.raises(rejected):
            cipher.decrypt(changed, envelope, scope, len(original))
    with pytest.raises(rejected):
        cipher.decrypt(first, other, scope, len(original))
    with pytest.raises(rejected):
        cipher.decrypt(first, envelope, {**scope, 'project_id': 'other'}, len(original))
    with pytest.raises(rejected):
        cls({'v1': b'x' * 32}, 'v1').decrypt(first, envelope, scope, len(original))
    with pytest.raises(rejected):
        cipher.decrypt(first, envelope, scope, len(original) - 1)


def test_rotation_keeps_original_ciphertext_and_scope_binding():
    cls, rejected = crypto()
    cipher = cls({'v1': b'a' * 32, 'v2': b'b' * 32}, 'v1')
    scope = {'organization_id': 'org', 'project_id': 'project', 'document_id': 'doc', 'object_kind': 'original'}
    ciphertext, envelope = cipher.encrypt(b'original', scope)
    rotated = cipher.rewrap(envelope, scope, 'v2')
    assert rotated['master_key_version'] == 'v2'
    assert cls({'v2': b'b' * 32}, 'v2').decrypt(ciphertext, rotated, scope, 8) == b'original'
    with pytest.raises(rejected):
        cipher.decrypt(ciphertext, rotated, {**scope, 'organization_id': 'foreign'}, 8)
