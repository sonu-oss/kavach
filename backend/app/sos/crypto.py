"""Location encryption for SOS.

The browser encrypts {lat, lng, accuracy} with the server's RSA-OAEP public key
(WebCrypto) before sending it, so the location is protected end to end even
before TLS, and nothing but this server's private key can read it.
"""

import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app import config, database

_key: rsa.RSAPrivateKey | None = None

# The private key lives in the database (so it survives redeploys of a server with no disk);
# a key file from before that is imported once.
database.schema("secrets", "CREATE TABLE IF NOT EXISTS app_secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL)")


def _key_path():
    return config.DATA_DIR / "sos_key.pem"


def private_key() -> rsa.RSAPrivateKey:
    global _key
    if _key is None:
        r = database.one("SELECT value FROM app_secrets WHERE name='sos_private_key'")
        if r:
            pem = r["value"].encode()
        elif _key_path().exists():
            pem = _key_path().read_bytes()
        else:
            pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        if not r:
            # ON CONFLICT: if another process stored a key first, theirs wins and is read back.
            database.run("INSERT INTO app_secrets (name, value) VALUES ('sos_private_key', ?) ON CONFLICT (name) DO NOTHING",
                         pem.decode())
            pem = database.one("SELECT value FROM app_secrets WHERE name='sos_private_key'")["value"].encode()
        _key = serialization.load_pem_private_key(pem, password=None)
    return _key


def public_key_spki_b64() -> str:
    der = private_key().public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(der).decode()


def decrypt_location(ciphertext_b64: str) -> dict:
    raw = private_key().decrypt(
        base64.b64decode(ciphertext_b64, validate=True),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return json.loads(raw)


def encrypt_location_for_tests(loc: dict) -> str:
    ct = private_key().public_key().encrypt(
        json.dumps(loc).encode(),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode()
