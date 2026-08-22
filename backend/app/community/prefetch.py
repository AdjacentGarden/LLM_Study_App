from __future__ import annotations

from app.community.catalog import CATALOG, ensure_community_source_cached


def main() -> None:
    for entry in CATALOG:
        path = ensure_community_source_cached(entry)
        print(f"cached {entry.id}: {path.name} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
