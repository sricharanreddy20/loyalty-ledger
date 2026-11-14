"""In-memory dataset: one list of row dicts per table, in database load order."""
import hashlib
import json
from dataclasses import dataclass, field, fields


@dataclass
class Dataset:
    tier: list = field(default_factory=list)
    rule: list = field(default_factory=list)
    reward: list = field(default_factory=list)
    customer: list = field(default_factory=list)
    loyalty_account: list = field(default_factory=list)
    tier_change: list = field(default_factory=list)
    expiry_batch: list = field(default_factory=list)
    orders: list = field(default_factory=list)
    order_line: list = field(default_factory=list)
    line_return: list = field(default_factory=list)
    redemption: list = field(default_factory=list)
    points_transaction: list = field(default_factory=list)
    redemption_lot: list = field(default_factory=list)

    def tables(self) -> dict[str, list]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def row_counts(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self.tables().items()}

    def fingerprint(self) -> str:
        """SHA-256 over every row of every table; equal datasets give equal fingerprints."""
        digest = hashlib.sha256()
        for name, rows in self.tables().items():
            digest.update(name.encode())
            for row in rows:
                digest.update(json.dumps(row, default=str, separators=(",", ":")).encode())
        return digest.hexdigest()
