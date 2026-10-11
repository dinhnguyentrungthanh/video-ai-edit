# TEST ONLY activation key pair

These two files are the **test** Ed25519 key pair of `biliflow.activation` (plan section 7, C6).
They exist only for the test suite and are public on purpose.

- `TEST-ONLY-private-key.pem`: the test private key (unencrypted). Never use it to issue a real key.
- `TEST-ONLY-public-key.hex`: its public key.

`activation._trusted_public_key()` refuses this public key (its SHA-256 is `TEST_PUBLIC_KEY_SHA256`),
so a release build or a source run never accepts a key signed with it. Tests select it by patching
`_trusted_public_key`. The real issuing key lives only on the user's PC, outside Git and outside every
BiliFlow folder.
