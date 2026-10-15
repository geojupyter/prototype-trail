from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

# the units a byte count is worded in, each 1024 of the one before it
SIZE_UNITS: Final[tuple[str, ...]] = ('KiB', 'MiB', 'GiB', 'TiB')
# what a stat reports for a resource that has gone since it was tracked
MISSING: Final = '<missing>'


class Repr(Protocol):
    def _repr_items(self) -> Iterator[tuple[str, object]]: ...


def st_size(size: int) -> str:
    """
    A byte count with its unit. Under a kibibyte it is exact. Above that it is rounded to one
    decimal place.
    """
    if size < 1024:
        return f'{size} B'
    scaled = float(size)
    for unit in SIZE_UNITS[:-1]:
        scaled /= 1024
        # promoted on the rounded value, so a size just under a threshold reads '1.0 MiB'
        # rather than '1024.0 KiB'
        if round(scaled, 1) < 1024:
            return f'{scaled:.1f} {unit}'
    return f'{scaled / 1024:.1f} {SIZE_UNITS[-1]}'


def clock(moment: datetime) -> str:
    """
    A moment as a clock time to the millisecond. The date is added in front only as far as it
    differs from today, so a moment from today is a time alone.
    """
    now = datetime.now(moment.tzinfo)
    date_prefix = ''
    if moment.year != now.year:
        date_prefix = moment.strftime('%Y-%m-%d ')
    elif moment.month != now.month:
        date_prefix = moment.strftime('%m-%d ')
    elif moment.day != now.day:
        date_prefix = moment.strftime('%d ')
    return f'{date_prefix}{moment:%H:%M:%S}.{moment.microsecond // 1000:03d}'


def mtime(seconds: float) -> str:
    """
    An mtime as a local date and time, to the second. The stored value is seconds since the epoch.
    """
    moment = datetime.fromtimestamp(seconds, UTC).astimezone()
    return f'{moment:%Y-%m-%d %H:%M:%S}'


def bare(value: object) -> str:
    """
    A field as text. A string is shown as itself and anything else as its repr. A string is quoted
    only if it is empty, has whitespace at either end, or has a character that is not printable.
    """
    if not isinstance(value, str):
        return repr(value)
    if not value or value != value.strip() or not value.isprintable():
        return repr(value)
    return value


def plural(
        count: int,
        noun: str,
) -> str:
    """
    A count with its noun, in the plural unless the count is one.

    >>> plural(1, 'asset')
    '1 asset'
    """
    if count == 1:
        return f'{count} {noun}'
    return f'{count} {noun}s'


def bracketed(
        name: str,
        shown: Iterable[object],
        total: int,
        indent: str = '    ',
) -> list[str]:
    """Bracketed lines for a list truncated to `shown`, summarizing the remainder of `total`."""
    if not total:
        return [f'{indent}{name}: []']
    rows = [f'{indent}{name}: [']
    displayed = 0
    for value in shown:
        rows.append(f'{indent * 2}{bare(value)},')
        displayed += 1
    hidden = total - displayed
    if hidden > 0:
        rows.append(f'{indent * 2}<{hidden} others>')
    rows.append(f'{indent}]')
    return rows


def lines(
        name: str,
        values: Iterable[str],
) -> str:
    """
    A header with the count, then each line indented under it.

    >>> lines('Markers', ['./', '*.csv', 'data/*.png'])
    Markers (3)
        ./
        *.csv
        data/*.png
    """
    values = list(values)
    out = [f'{name} ({len(values)})']
    out.extend(
        f'    {value}'
        for value in values
    )
    return '\n'.join(out)


def asset(
        name: str,
        path: Path | None,
        attributes: Iterable[tuple[str, object]] | None = None,
) -> str:
    rows = [name]
    if attributes is None:
        if path is None:
            displayed_path = None
        else:
            displayed_path = str(path)
        attributes = [('path', displayed_path)]
    rows.extend(
        f'    {key}: {bare(value)}'
        for key, value in attributes
    )
    if path is None:
        return '\n'.join(rows)
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            offset = max(0, stream.tell() - 8192)
            stream.seek(offset)
            content = stream.read(8192)
    except FileNotFoundError:
        rows.append('    tail: <missing>')
        return '\n'.join(rows)
    except OSError as error:
        rows.append(f'    tail: <unreadable: {error.strerror}>')
        return '\n'.join(rows)

    if offset:
        newline = content.find(b'\n')
        if newline != -1 and newline < len(content) - 1:
            content = content[newline + 1:]
    tail = content.decode('utf-8', errors='replace').splitlines()[-4:]
    if not tail:
        rows.append('    tail: <empty>')
        return '\n'.join(rows)
    rows.append('    tail:')
    if offset:
        rows.append('        ...')
    for line in tail:
        if len(line) > 240:
            line = line[:237] + '...'
        rows.append(f'        {line}')
    return '\n'.join(rows)


def items(
        name: str,
        records: Iterable[Repr],
) -> str:
    """Multi-line repr for a collection, expanding each item's fields onto its own line."""
    records = list(records)
    rows = [f'{name} ({len(records)})']
    for position, record in enumerate(records):
        rows.append(f'    {position}. {type(record).__name__}')
        rows.extend(
            f'        {key}: {bare(value)}'
            for key, value in record._repr_items()
        )
    return '\n'.join(rows)
