import re

ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")

SUPPORTED_CHAIN_IDS = frozenset({1, 56, 11124})


def normalize_address(address: str) -> str:
    return address.lower()


def is_valid_address(address: str) -> bool:
    return bool(ADDRESS_PATTERN.match(address))


def is_supported_chain(chain_id: int) -> bool:
    return chain_id in SUPPORTED_CHAIN_IDS
