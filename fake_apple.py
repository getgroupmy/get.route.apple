"""A fake Apple Maps Server API for the tests (httpx MockTransport), and a
throwaway signing key so the real token signing is exercised."""
import json
import os
import tempfile

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

TEAM, KEY_ID = "TEAM123456", "KEY1234567"
ANSWER = {
    "origin": {"coordinate": {"latitude": 3.15, "longitude": 101.71}},
    "destination": {"coordinate": {"latitude": 2.93, "longitude": 101.87}},
    "routes": [{"name": "E7", "distanceMeters": 48213, "durationSeconds": 2940, "hasTolls": True,
                "stepIndexes": [0]}],
    "stepPaths": [[{"latitude": 3.15, "longitude": 101.71}, {"latitude": 2.93, "longitude": 101.87}]],
}


def make_key_file():
    """(path to a .p8 file, its public key)."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd, path = tempfile.mkstemp(suffix=".p8")
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return path, key.public_key()


class FakeApple:
    """Answers /v1/token and /v1/directions; records every request."""

    def __init__(self, public_key):
        self.public_key = public_key
        self.requests = []
        self.directions = [(200, ANSWER)]  # queue of (status, body); the last one repeats
        self.token_status = 200
        self.issued = 0
        self.valid_tokens = set()
        self.retry_after = None

    def handler(self, request):
        self.requests.append(request)
        auth = request.headers.get("Authorization", "")
        if request.url.path == "/v1/token":
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": {"message": "Not Authorized"}})
            claims = jwt.decode(auth.removeprefix("Bearer "), self.public_key, algorithms=["ES256"])
            header = jwt.get_unverified_header(auth.removeprefix("Bearer "))
            assert claims["iss"] == TEAM and header["kid"] == KEY_ID, (claims, header)
            self.issued += 1
            token = f"access-{self.issued}"
            self.valid_tokens.add(token)
            return httpx.Response(200, json={"accessToken": token, "expiresInSeconds": 1800})
        if request.url.path == "/v1/directions":
            if auth.removeprefix("Bearer ") not in self.valid_tokens:
                return httpx.Response(401, json={"error": {"message": "Not Authorized"}})
            status, body = self.directions.pop(0) if len(self.directions) > 1 else self.directions[0]
            headers = {"Retry-After": str(self.retry_after)} if self.retry_after else {}
            return httpx.Response(status, content=json.dumps(body), headers=headers)
        return httpx.Response(404)

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
