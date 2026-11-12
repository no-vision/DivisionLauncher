"""Preserve native client tutorial history across isolated Proton launches.

The client names its file <login-name>_cicerodata. Our login name is a one-use
grant, so retaining just the old filename or prefix cannot restore that history.
Store the client's own file under the authenticated realm/account pair and copy
it to the new grant's filename before starting the client. This is UI preference
state, not server-authoritative inventory, progression, or tutorial mission flags.
No lessons are fabricated or decoded/rewritten, and no grant enters the cache key.
"""
import hashlib
import os
from pathlib import Path
import re
import tempfile
import uuid


class TutorialHistory:
    MaximumBytes = 2 * 1024 * 1024

    def __init__(self, storage: Path, run: Path, realm: str, account: str, grant: str, documents: Path | None = None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{24}', grant):
            raise ValueError('Invalid tutorial history launch identity')
        self.storage = storage / uuid.UUID(realm).hex / uuid.UUID(account).hex
        self.cached = self.storage / 'cicerodata.bin'
        self.run = run.resolve()
        self.documents = documents if documents is not None else self.run / 'compat/pfx/drive_c/users/steamuser/Documents/My Games/Tom Clancy\'s The Division'
        self.current = self.documents / (grant + '_cicerodata')
        self.last_saved = None
        self.pending = None

    @staticmethod
    def validate(data: bytes) -> None:
        # Observed format version, not a claim to have decoded every native row.
        # An unsupported client version must not overwrite the previous history.
        if not 8 <= len(data) <= TutorialHistory.MaximumBytes or data[:4] != b'\x01\0\0\0':
            raise ValueError('Unsupported native tutorial history')

    @staticmethod
    def read(path: Path) -> bytes | None:
        if not path.exists():
            return None
        with path.open('rb') as source:
            before = os.fstat(source.fileno())
            data = source.read(TutorialHistory.MaximumBytes + 1)
            after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None
        TutorialHistory.validate(data)
        return data

    @staticmethod
    def write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='.tutorial-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def check_documents(self) -> None:
        # Do not traverse a Wine Documents link into the user's real Documents.
        if not self.documents.resolve().is_relative_to(self.run) or not self.current.resolve().is_relative_to(self.run):
            raise ValueError('Tutorial documents escape the isolated client prefix')

    def restore(self) -> bool:
        self.check_documents()
        data = self.read(self.cached)
        if data is None:
            return False
        if self.current.exists():
            raise ValueError('Restore must precede client startup')
        self.write(self.current, data)
        self.last_saved = hashlib.sha256(data).digest()
        return True

    def save(self, *, final: bool = False) -> bool:
        self.check_documents()
        data = self.read(self.current)
        if data is None:
            return False
        digest = hashlib.sha256(data).digest()
        if digest == self.last_saved:
            return False
        # While the game is running, observe identical bytes on two separate
        # polls before publishing. On shutdown the prefix has already stopped.
        if not final and digest != self.pending:
            self.pending = digest
            return False
        self.write(self.cached, data)
        self.last_saved = digest
        self.pending = None
        return True
