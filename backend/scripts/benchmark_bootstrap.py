"""Disposable SQLite fixture benchmark. No model/network/user data is used."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

if os.getenv("DATABASE_URL") or os.getenv("DATABASE_HOST"):
    raise SystemExit("Unset database configuration; this benchmark only uses disposable SQLite fixtures")

with tempfile.TemporaryDirectory(prefix="bootstrap-benchmark-") as folder:
    root = Path(folder)
    os.environ["APP_DATA_DIR"] = str(root)
    sys.path.insert(0, str(Path(__file__).parents[1] / "tests"))
    import pytest
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from native_harness import api, configure

    from adaptive_learning import native_catalog
    from adaptive_learning.database import Connection
    from adaptive_learning.native import NativeRepository
    patch = pytest.MonkeyPatch()
    app = configure(root, patch)
    patch.setenv("NATIVE_LOCAL_LOGIN_ENABLED", "1")
    patch.setenv("APP_ENV", "development")
    token = api.account_repository.local_session(None)
    owner = api.account_repository.session_owner(token)
    queries = []
    original = Connection.execute
    def counted(self, query, params=()):
        if query.lstrip().upper().startswith("SELECT"):
            queries.append(query)
        return original(self, query, params)
    patch.setattr(Connection, "execute", counted)
    records = []
    optimized_workspaces = NativeRepository.workspaces
    def old_workspaces(self, actor):
        with self.connect() as db:
            keys = [row[0] for row in db.execute("SELECT id FROM native_workspaces WHERE owner=? ORDER BY created,id", (actor,))]
        return [self.workspace(actor, key) for key in keys]
    with TestClient(app) as client:
        client.cookies.set("zhiwo_account", token)
        previous = 0
        for count in (1, 5, 20):
            for index in range(previous, count):
                response = client.post("/api/native/catalog/community_genetics/import", json={"id": f"workspace_bench_{index}", "name": f"课程{index}"})
                response.raise_for_status()
            previous = count
            measured = {"workspaces": count}
            for mode in ("legacy", "full", "metadata"):
                queries.clear()
                began = time.perf_counter()
                if mode == "legacy":
                    # Restore the exact former workspace list N+1 in addition to
                    # the retained pre-P2 resource assembler for the before sample.
                    patch.setattr(NativeRepository, "workspaces", old_workspaces)
                    workspaces = client.get("/api/workspaces")
                    workspaces.raise_for_status()
                    library = []
                    for entry in api.account_repository.repo.library(owner):
                        structure = api.job_repository.get_structure(entry["book_id"])
                        library.append({**entry, "structure": structure.model_dump(mode="json") if structure else None})
                    selection = api.native_repository.selection(owner)
                    preferences = api.native_repository.learner_preferences(owner)
                    payload = JSONResponse({"workspaces": workspaces.json(), "selection": selection,
                                            "contract_version": 1, "library": library,
                                            "learner_preferences": preferences,
                                            "catalog": native_catalog.catalog()}).body
                    patch.setattr(NativeRepository, "workspaces", optimized_workspaces)
                else:
                    response = client.get("/api/native/bootstrap?mode=" + ("metadata" if mode == "metadata" else "full"))
                    response.raise_for_status()
                    payload = response.content
                measured[mode] = {"bytes": len(payload), "selects": len(queries), "milliseconds": round((time.perf_counter()-began)*1000, 2)}
            records.append(measured)
    patch.undo()
    print(json.dumps(records, indent=2))
