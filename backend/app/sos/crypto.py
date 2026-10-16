"""Location encryption for SOS.

The browser encrypts {lat, lng, accuracy} with the server's RSA-OAEP public key
(WebCrypto) before sending it, so the location is protected end to end even
before TLS, and nothing but this server's private key can read it.
"""

import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app import config

_key: rsa.RSAPrivateKey | None = None


def _key_path():
    return config.DATA_DIR / "sos_key.pem"


def private_key() -> rsa.RSAPrivateKey:
    global _key
    if _key is None:
        path = _key_path()
        if path.exists():
            _key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            _key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ))
            path.chmod(0o600)
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
