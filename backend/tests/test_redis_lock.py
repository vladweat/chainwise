from uuid import uuid4

from app.utils.redis_lock import (
    acquire_indexing_lock,
    is_indexing_locked,
    release_indexing_lock,
)


def test_indexing_lock_is_exclusive_case_insensitive_and_scoped_by_user():
    user_id = uuid4().int % (2**31)
    other_user_id = user_id + 1
    address = f"0x{uuid4().hex}{uuid4().hex[:8]}"
    chain_id = 1

    try:
        assert not is_indexing_locked(user_id, chain_id, address.lower())
        assert acquire_indexing_lock(user_id, chain_id, address.upper(), ttl=60)
        assert is_indexing_locked(user_id, chain_id, address.lower())
        assert not acquire_indexing_lock(user_id, chain_id, address.lower(), ttl=60)

        assert not is_indexing_locked(other_user_id, chain_id, address)
        assert acquire_indexing_lock(other_user_id, chain_id, address.lower(), ttl=60)

        release_indexing_lock(user_id, chain_id, address)
        assert not is_indexing_locked(user_id, chain_id, address)
        assert is_indexing_locked(other_user_id, chain_id, address)
    finally:
        release_indexing_lock(user_id, chain_id, address)
        release_indexing_lock(other_user_id, chain_id, address)
