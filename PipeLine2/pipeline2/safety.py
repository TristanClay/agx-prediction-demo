"""All application writers pass through this Pipeline1 protection boundary."""

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

PROTECTED_ROOT = Path(__file__).resolve().parents[2] / "PipeLine1"
_SOURCE_ROOTS: ContextVar[tuple[Path, ...]] = ContextVar("pipeline1_sources", default=())


@contextmanager
def protect_sources(roots: tuple[Path, ...]):
    token = _SOURCE_ROOTS.set((*_SOURCE_ROOTS.get(), *roots))
    try:
        yield
    finally:
        _SOURCE_ROOTS.reset(token)


def writable_path(path: str | Path, protected_roots: tuple[Path, ...] = ()) -> Path:
    resolved = Path(path).expanduser().resolve()
    roots = (
        PROTECTED_ROOT.resolve(),
        *(p.resolve() for p in (*protected_roots, *_SOURCE_ROOTS.get())),
    )
    if any(resolved == root or root in resolved.parents for root in roots):
        raise PermissionError(f"Pipeline 1 is read-only: {resolved}")
    if any(part.casefold() == "pipeline1" for part in resolved.parts):
        raise PermissionError(f"Pipeline 1 is read-only: {resolved}")
    return resolved


def new_directory(path: str | Path) -> Path:
    target = writable_path(path)
    target.mkdir(parents=True, exist_ok=False)
    return target


def write_new(path: str | Path, content: str) -> Path:
    target = writable_path(path)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(content)
    return target
