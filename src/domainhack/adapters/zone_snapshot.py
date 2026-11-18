"""Zone snapshots: the names delegated under a TLD, imported from the registry's zone file.

Some registries publish their whole zone (.ch and .li, .se and .nu, and the
generic TLDs through ICANN's CZDS). A name in the zone has nameservers, so it
is certainly registered and there is nothing to ask the registry. A name that
is *not* in the zone may still be registered (without nameservers, on hold, in
redemption), so it is always checked live: a snapshot can only save queries,
never produce a false "available".

``import_zone`` reads a zone file (``dig AXFR`` output or any master-format
file, gzip or not; a plain list of names works too) and writes the snapshot:
one second-level label per line, sorted, after a header line. ``ZoneSnapshot``
answers "is this label in the zone?" with a binary search over the
memory-mapped file, so even a zone of millions of names costs no memory.
"""

from __future__ import annotations

import gzip
import mmap
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO

from domainhack.domain.entities import TLD

ZONE_ENV = "DOMAINHACK_ZONES"
# A snapshot older than this is not used: names registered or deleted since
# then would be answered from stale data.
ZONE_MAX_AGE_DAYS = 7
_HEADER = re.compile(rb"# domainhack-zone tld=(\S+) imported=(\d{4}-\d{2}-\d{2}) names=(\d+)\n")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_RECORD_TYPES = frozenset({"NS", "DS", "A", "AAAA", "SOA", "RRSIG", "NSEC", "NSEC3", "DNSKEY"})


class ZoneError(Exception):
    """A zone file or snapshot cannot be used; the message is meant for the end user."""


def default_zone_dir() -> Path:
    if from_env := os.environ.get(ZONE_ENV):
        return Path(from_env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "domainhack" / "zones"


def _open_text(path: Path) -> IO[str]:
    with open(path, "rb") as probe:
        gzipped = probe.read(2) == b"\x1f\x8b"
    if gzipped:
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def delegated_labels(lines: Iterable[str], suffix: str) -> Iterator[str]:
    """The second-level labels delegated under ``suffix`` in zone-file ``lines``.

    A master-format line counts when its owner is exactly one label below the
    suffix and its type is NS (``example.ch. 3600 IN NS ns1.example.net.``).
    A line with a single field is taken as a name (``example`` or
    ``example.ch``). Comments, the zone's own records and deeper names (glue)
    are skipped. Labels are lowercase; IDNs stay in their ``xn--`` form.
    """
    tail = "." + suffix.lower()
    for line in lines:
        fields = line.split(";", 1)[0].split()
        if not fields:
            continue
        owner = fields[0].lower().rstrip(".")
        if len(fields) > 1:
            types = [f for f in fields[1:5] if f.upper() in _RECORD_TYPES]
            if not types or types[0].upper() != "NS":
                continue
            if not owner.endswith(tail):
                continue
            label = owner[: -len(tail)]
        else:
            label = owner[: -len(tail)] if owner.endswith(tail) else owner
        if _LABEL.fullmatch(label):
            yield label


@dataclass(frozen=True)
class ZoneInfo:
    """A stored snapshot: its TLD, the day it was imported and how many names it has."""

    suffix: str
    imported: date
    names: int
    path: Path

    def age_days(self, today: date) -> int:
        return (today - self.imported).days

    def is_fresh(self, today: date) -> bool:
        return 0 <= self.age_days(today) <= ZONE_MAX_AGE_DAYS


class ZoneSnapshot:
    """Membership test over a snapshot file. Close it (or use ``with``) when done."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            # Kept open for the mapping; close() releases both.
            self._file = open(path, "rb")  # noqa: SIM115
            head = self._file.readline()
            match = _HEADER.fullmatch(head)
            if match is None:
                raise ZoneError(f"{path}: not a domainhack zone snapshot")
            self.info = ZoneInfo(
                match.group(1).decode("ascii"),
                date.fromisoformat(match.group(2).decode("ascii")),
                int(match.group(3)),
                path,
            )
            self._start = len(head)
            size = os.fstat(self._file.fileno()).st_size
            self._map = (
                mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
                if size > self._start
                else None
            )
        except OSError as exc:
            raise ZoneError(f"cannot read {path}: {exc.strerror or exc}") from exc
        except ZoneError:
            self._file.close()
            raise
        except ValueError:  # a header whose date is not a date
            self._file.close()
            raise ZoneError(f"{path}: not a domainhack zone snapshot") from None

    def __contains__(self, label: object) -> bool:
        data = self._map
        if data is None or not isinstance(label, str):
            return False
        try:
            wanted = label.lower().encode("ascii")
        except UnicodeEncodeError:
            return False
        low, high = self._start, len(data)
        while low < high:
            middle = (low + high) // 2
            start = data.rfind(b"\n", low, middle) + 1 if middle > low else low
            start = max(start, low)
            end = data.find(b"\n", start)
            line = data[start:end]
            if line == wanted:
                return True
            if line < wanted:
                low = end + 1
            else:
                high = start
        return False

    def close(self) -> None:
        if self._map is not None:
            self._map.close()
            self._map = None
        self._file.close()

    def __enter__(self) -> ZoneSnapshot:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class ZoneStore:
    """The directory of snapshots, one ``<tld>.zone`` file per TLD."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory if directory is not None else default_zone_dir()

    def path_for(self, suffix: str) -> Path:
        return self.directory / f"{suffix.lower()}.zone"

    def import_zone(self, suffix: str, source: Path, today: date) -> ZoneInfo:
        """Read ``source`` and store the snapshot for ``suffix``, replacing an older one."""
        try:
            tld = TLD(suffix.lower().lstrip("."))
        except ValueError:
            raise ZoneError(f"invalid TLD: {suffix!r}") from None
        try:
            with _open_text(source) as lines:
                labels = sorted(
                    {label.encode("ascii") for label in delegated_labels(lines, tld.suffix)}
                )
        except OSError as exc:
            raise ZoneError(f"cannot read {source}: {exc.strerror or exc}") from exc
        except EOFError as exc:
            raise ZoneError(f"cannot read {source}: {exc}") from exc
        if not labels:
            example = f"example.{tld.suffix}. 3600 IN NS ns1.example.net."
            raise ZoneError(
                f"{source}: no name delegated under .{tld.suffix} found "
                f"(expected NS records such as '{example}')"
            )
        target = self.path_for(tld.suffix)
        header = (
            f"# domainhack-zone tld={tld.suffix} imported={today.isoformat()} names={len(labels)}\n"
        )
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self.directory, prefix=".zone-", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(header.encode("ascii"))
                    out.write(b"\n".join(labels))
                    out.write(b"\n")
                os.replace(temp, target)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise ZoneError(f"cannot write {target}: {exc.strerror or exc}") from exc
        return ZoneInfo(tld.suffix, today, len(labels), target)

    def snapshots(self) -> list[ZoneInfo]:
        """Every readable snapshot, by TLD. Files that are not snapshots are skipped."""
        infos: list[ZoneInfo] = []
        if not self.directory.is_dir():
            return infos
        for path in sorted(self.directory.glob("*.zone")):
            try:
                with ZoneSnapshot(path) as snapshot:
                    infos.append(snapshot.info)
            except ZoneError:
                continue
        return infos

    def remove(self, suffix: str) -> bool:
        """Delete the snapshot for ``suffix``; False when there was none."""
        path = self.path_for(suffix.lower().lstrip("."))
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ZoneError(f"cannot remove {path}: {exc.strerror or exc}") from exc
        return True

    def open_fresh(self, today: date) -> tuple[dict[str, ZoneSnapshot], list[ZoneInfo]]:
        """Open the snapshots young enough to use; also return the stale ones (not opened)."""
        fresh: dict[str, ZoneSnapshot] = {}
        stale: list[ZoneInfo] = []
        for info in self.snapshots():
            if info.is_fresh(today):
                fresh[info.suffix] = ZoneSnapshot(info.path)
            else:
                stale.append(info)
        return fresh, stale
