from adaptive_learning.api.private_export import private_export_router
from adaptive_learning.api.private_forwarding import private_forwarding_router
from adaptive_learning.api.public_material_routes import public_material_router
from native_harness import api
from test_native_api import native as native


def test_capabilities_follow_included_routers(native):
    owner, _, _ = native
    for router in (public_material_router, private_forwarding_router, private_export_router):
        owner.app.include_router(router(api))
    result = owner.get("/api/native/local-first/capabilities")
    assert result.status_code == 200
    assert all(result.json()[key] for key in ("local_first", "public_library", "private_forwarding", "private_export"))
