"""Shared I2P helpers: decode the .b32.i2p tunnel address.

The i2pd_service_setup task and the deployed address command
(pyntara.i2pd_address) both need the address of the SSH tunnel identity.
The address is derived from the binary PrivateKeys record i2pd writes,
so the decoder lives here, shared and imported, never copied
(architecture contract section 3).
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

# The IdentityEx record of the i2pd PrivateKeys file (Identity.h):
# publicKey[256] + signingKey[128] + certificate[3]. The certificate
# starts with the type byte; type KEY means the signing and crypto key
# types follow in an extended block, whose length is the big-endian
# uint16 at certificate offset 1. The I2P address is the SHA-256 of the
# whole IdentityEx (DEFAULT_IDENTITY_SIZE plus the extended block).
I2PD_IDENTITY_SIZE = 387
I2PD_CERTIFICATE_TYPE_KEY = 5


def b32_address(keys_path: Path) -> str | None:
    """The .b32.i2p address of the tunnel keys file, or None.

    The keys file is the binary PrivateKeys record i2pd writes: its
    first bytes are the IdentityEx, and the I2P address is the lowercase
    unpadded base32 of the SHA-256 hash of that IdentityEx. The extended
    block length comes from the certificate, so the hash covers exactly
    the identity bytes. A missing file, a file too short or a record
    without the KEY certificate yields None, so the caller reports that
    the address is not available yet instead of failing.
    """

    try:
        data = keys_path.read_bytes()
    except OSError:
        return None
    if len(data) < I2PD_IDENTITY_SIZE:
        return None
    certificate_type = data[I2PD_IDENTITY_SIZE - 3]
    extended_len = int.from_bytes(
        data[I2PD_IDENTITY_SIZE - 2 : I2PD_IDENTITY_SIZE], "big"
    )
    if certificate_type != I2PD_CERTIFICATE_TYPE_KEY:
        return None
    identity_len = I2PD_IDENTITY_SIZE + extended_len
    if len(data) < identity_len:
        return None
    digest = hashlib.sha256(data[:identity_len]).digest()
    encoded = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    return f"{encoded}.b32.i2p"
