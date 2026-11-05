import json
import logging
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.utils.address import is_supported_chain, is_valid_address, normalize_address

logger = logging.getLogger(__name__)

EXPLORER_API_URLS: dict[int, str] = {
    1: "https://api.etherscan.io/api",
    56: "https://api.bscscan.com/api",
    11124: "https://api.abscan.org/api",
}


class EtherscanError(Exception):
    pass


class EtherscanNotConfiguredError(EtherscanError):
    pass


class EtherscanContractNotFoundError(EtherscanError):
    pass


class EtherscanNetworkError(EtherscanError):
    pass


@dataclass
class ContractSource:
    address: str
    chain_id: int
    source_code: str
    name: str | None
    compiler_version: str | None
    is_verified: bool


def _normalize_compiler_version(raw: str | None) -> str | None:
    if not raw:
        return None
    version = raw.strip()
    if version.startswith("v"):
        version = version[1:]
    if "+" in version:
        version = version.split("+", 1)[0]
    return version or None


def _unwrap_source_code(raw: str) -> str:
    source = raw.strip()
    if not source:
        return source

    if source.startswith("{{") and source.endswith("}}"):
        source = source[2:-2]

    if source.startswith("{") and source.endswith("}"):
        try:
            metadata = json.loads(source)
        except json.JSONDecodeError:
            return raw

        if isinstance(metadata, dict) and "sources" in metadata:
            parts: list[str] = []
            sources = metadata["sources"]
            if isinstance(sources, dict):
                for file_path, file_data in sorted(sources.items()):
                    if isinstance(file_data, dict):
                        content = file_data.get("content", "")
                    else:
                        content = str(file_data)
                    parts.append(f"// File: {file_path}\n{content}")
            return "\n\n".join(parts) if parts else raw

    return raw


def fetch_contract_source(address: str, chain_id: int) -> ContractSource:
    if not is_valid_address(address):
        raise EtherscanError("Invalid contract address")

    if not is_supported_chain(chain_id):
        raise EtherscanError(f"Unsupported chain_id: {chain_id}")

    settings = get_settings()
    if not settings.etherscan_api_key:
        raise EtherscanNotConfiguredError("Etherscan API key is not configured")

    api_url = EXPLORER_API_URLS[chain_id]
    normalized = normalize_address(address)

    try:
        response = httpx.get(
            api_url,
            params={
                "module": "contract",
                "action": "getsourcecode",
                "address": normalized,
                "apikey": settings.etherscan_api_key,
            },
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("Block explorer request failed for chain_id=%s", chain_id)
        raise EtherscanNetworkError(
            "Cannot reach block explorer API. Check Docker network/DNS or try file upload.",
        ) from exc

    payload = response.json()

    if payload.get("status") != "1" or not payload.get("result"):
        message = payload.get("message", "Contract not found")
        raise EtherscanContractNotFoundError(message)

    result = payload["result"][0]
    source_code = _unwrap_source_code(result.get("SourceCode", ""))
    if not source_code.strip():
        raise EtherscanContractNotFoundError("Contract source code is empty")

    return ContractSource(
        address=normalized,
        chain_id=chain_id,
        source_code=source_code,
        name=result.get("ContractName") or None,
        compiler_version=_normalize_compiler_version(result.get("CompilerVersion")),
        is_verified=str(result.get("ABI", "")).strip() not in ("", "Contract source code not verified"),
    )
