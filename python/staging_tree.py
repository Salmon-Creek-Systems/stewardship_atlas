"""What publish asks of staging, answered from a directory or from the staging catalog.

Publish used to walk a hydrated staging directory and hash every file. Since
2026-11-18 staging has its own STAC catalog (``staging_catalog``), so publish
can answer the same questions — which files are under this prefix, how big,
what checksum, where are the bytes — without hydrating anything. The scanners
and upload planners in ``atlas_catalog`` and ``atlas_store`` take a tree rather
than a path; paths still work, wrapped in a ``LocalTree``.

``source(rel)`` is either a local ``Path`` (upload it) or a ``StagingObject``
(server-side copy it from the private bucket). That difference is the whole of
"publish copies instead of uploading".
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib


@dataclass(frozen=True)
class StagingObject:
    """An object in the private bucket's staging tree."""
    bucket: str
    key: str
    size: int = 0


class LocalTree:
    """A staging directory on disk. ``rel`` paths are relative to it."""

    def __init__(self, root):
        self.root = Path(root)
        self._sums = {}

    def rels(self, prefix: str) -> list:
        base = self.root / prefix
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix()
                      for p in base.rglob('*') if p.is_file())

    def is_file(self, rel: str) -> bool:
        return (self.root / rel).is_file()

    def size(self, rel: str) -> int:
        return (self.root / rel).stat().st_size

    def checksum(self, rel: str) -> str:
        """sha2-256 multihash, as STAC's ``file:checksum`` wants."""
        if rel not in self._sums:
            digest = hashlib.sha256()
            with open(self.root / rel, 'rb') as handle:
                for block in iter(lambda: handle.read(1 << 20), b''):
                    digest.update(block)
            self._sums[rel] = '1220' + digest.hexdigest()
        return self._sums[rel]

    def source(self, rel: str):
        return self.root / rel


class CatalogTree:
    """Staging as its STAC catalog describes it: ``{rel: {'sha256', 'size', 'etag'}}``."""

    def __init__(self, files: dict, private_bucket: str, atlas_name: str):
        self.files = dict(files)
        self.private_bucket = private_bucket
        self.atlas_name = atlas_name

    @classmethod
    def load(cls, client, outlets_bucket: str, private_bucket: str, atlas_name: str):
        import staging_catalog
        return cls(staging_catalog.load_tree(client, outlets_bucket, atlas_name),
                   private_bucket, atlas_name)

    def rels(self, prefix: str) -> list:
        prefix = prefix.rstrip('/') + '/'
        return sorted(rel for rel in self.files if rel.startswith(prefix))

    def is_file(self, rel: str) -> bool:
        return rel in self.files

    def size(self, rel: str) -> int:
        return self.files[rel]['size']

    def checksum(self, rel: str) -> str:
        return '1220' + self.files[rel]['sha256']

    def source(self, rel: str):
        return StagingObject(self.private_bucket, f"{self.atlas_name}/staging/{rel}",
                             self.files[rel].get('size') or 0)


def as_tree(source):
    """A tree for ``source``: already a tree, or a staging directory path."""
    if isinstance(source, (LocalTree, CatalogTree)):
        return source
    return LocalTree(source)


def children(tree, prefix: str) -> list:
    """Names directly under ``prefix/`` that are files (like ``iterdir`` + ``is_file``)."""
    prefix = prefix.rstrip('/') + '/'
    return sorted({rel[len(prefix):] for rel in tree.rels(prefix)
                   if '/' not in rel[len(prefix):]})
