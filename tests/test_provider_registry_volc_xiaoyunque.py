"""volc-xiaoyunque provider registry 注册测试"""

from lib.config.registry import PROVIDER_REGISTRY
from lib.providers import PROVIDER_VOLC_XIAOYUNQUE


def test_xiaoyunque_provider_registered():
    assert PROVIDER_VOLC_XIAOYUNQUE in PROVIDER_REGISTRY
    meta = PROVIDER_REGISTRY[PROVIDER_VOLC_XIAOYUNQUE]
    assert "access_key" in meta.required_keys
    assert "secret_key" in meta.required_keys
    assert "tos_bucket" in meta.required_keys
    assert "tos_endpoint" in meta.required_keys
    assert "tos_region" in meta.required_keys
    # secret_keys 应至少包含 access_key + secret_key
    assert "access_key" in meta.secret_keys
    assert "secret_key" in meta.secret_keys
    # 模型必须存在 + media_type=video
    assert "xiaoyunque-agent-2.0" in meta.models
    assert meta.models["xiaoyunque-agent-2.0"].media_type == "video"
    assert meta.models["xiaoyunque-agent-2.0"].default is True


def test_xiaoyunque_backend_registered():
    from lib.video_backends import get_registered_backends

    assert PROVIDER_VOLC_XIAOYUNQUE in get_registered_backends()


def test_default_pool_includes_xiaoyunque_video_lane():
    """_build_default_pools 应该自动给 video media_type 的 provider 配 video lane。"""
    from lib.generation_worker import _build_default_pools

    pools = _build_default_pools()
    assert PROVIDER_VOLC_XIAOYUNQUE in pools
    assert pools[PROVIDER_VOLC_XIAOYUNQUE].video_max >= 1
