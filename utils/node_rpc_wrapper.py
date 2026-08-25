from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from utils.http_wrapper import HttpError, HttpWrapper


class NodeRpcError(RuntimeError):
    pass


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class NodeRpcWrapper:
    def __init__(
        self,
        node_url: str,
        *,
        timeout: float = 15,
        page_size: int = 250,
        retries: int = 2,
        rate_limit_max_wait_seconds: float = 60,
    ):
        self.node_url = node_url.rstrip("/")
        self.timeout = timeout
        self.page_size = max(1, min(int(page_size), 1000))
        self.retries = max(0, min(int(retries), 5))
        self.rate_limit_max_wait_seconds = max(
            1.0,
            float(rate_limit_max_wait_seconds),
        )

    def _rpc(self, method: str, params: list[Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = HttpWrapper.post(
                    self.node_url,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": params,
                    },
                    timeout=self.timeout,
                )
                if response.status_code == 429:
                    last_error = NodeRpcError(
                        f"{method} returned HTTP 429 (rate limited)"
                    )
                    if attempt < self.retries:
                        delay = HttpWrapper.retry_after_seconds(
                            response,
                            fallback=min(2 ** attempt, 5),
                            maximum=self.rate_limit_max_wait_seconds,
                        )
                        time.sleep(delay)
                        continue
                    break
                if response.status_code != 200:
                    raise NodeRpcError(
                        f"{method} returned HTTP {response.status_code}"
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise NodeRpcError(
                        f"{method} returned invalid JSON"
                    ) from exc
                if not isinstance(payload, dict):
                    raise NodeRpcError(f"{method} returned a non-object JSON value")
                if payload.get("error"):
                    raise NodeRpcError(
                        f"{method} returned RPC error: {payload['error']}"
                    )
                if "result" not in payload:
                    raise NodeRpcError(f"{method} response has no result")
                return payload["result"]
            except (HttpError, NodeRpcError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 5))
        raise NodeRpcError(
            f"{method} failed after {self.retries + 1} attempts: {last_error}"
        ) from last_error

    def get_latest_momentum(self) -> dict[str, Any]:
        result = self._rpc("ledger.getFrontierMomentum", [])
        if not isinstance(result, dict):
            raise NodeRpcError("ledger.getFrontierMomentum result is not an object")
        height = _as_int(result.get("height"))
        if height is None:
            raise NodeRpcError("ledger.getFrontierMomentum has no valid height")
        return {
            "height": height,
            "hash": result.get("hash"),
            "timestamp": _as_int(result.get("timestamp")),
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def get_sync_info(self) -> dict[str, Any]:
        result = self._rpc("stats.syncInfo", [])
        if not isinstance(result, dict):
            raise NodeRpcError("stats.syncInfo result is not an object")
        return result

    def get_all_pillars(self) -> dict[str, Any]:
        pillars: dict[str, dict[str, Any]] = {}
        page = 0
        total: int | None = None

        while True:
            result = self._rpc(
                "embedded.pillar.getAll",
                [page, self.page_size],
            )
            if not isinstance(result, dict):
                raise NodeRpcError("embedded.pillar.getAll result is not an object")
            items = result.get("list")
            if not isinstance(items, list):
                raise NodeRpcError("embedded.pillar.getAll has no list")
            if total is None:
                total = _as_int(result.get("count"))

            for pillar in items:
                if not isinstance(pillar, dict):
                    raise NodeRpcError("embedded.pillar.getAll contains invalid pillar data")
                owner_address = pillar.get("ownerAddress")
                if not owner_address:
                    raise NodeRpcError("Pillar has no ownerAddress")
                current_stats = pillar.get("currentStats")
                if not isinstance(current_stats, dict):
                    raise NodeRpcError(f"Pillar {owner_address} has no currentStats")
                normalized = {
                    "name": str(pillar.get("name") or owner_address),
                    "ownerAddress": owner_address,
                    "currentStats": {
                        "producedMomentums": _as_int(
                            current_stats.get("producedMomentums"), 0
                        ),
                        "expectedMomentums": _as_int(
                            current_stats.get("expectedMomentums"), 0
                        ),
                    },
                    "weight": _as_int(pillar.get("weight"), 0) or 0,
                    "giveMomentumRewardPercentage": _as_int(
                        pillar.get("giveMomentumRewardPercentage"), 0
                    ) or 0,
                    "giveDelegateRewardPercentage": _as_int(
                        pillar.get("giveDelegateRewardPercentage"), 0
                    ) or 0,
                    "rank": _as_int(pillar.get("rank")),
                    "raw": pillar,
                }
                pillars[str(owner_address)] = normalized

            if not items:
                break
            if len(items) < self.page_size:
                break
            if total is not None and len(pillars) >= total:
                break
            page += 1
            if page > 10000:
                raise NodeRpcError("Pillar pagination exceeded safety limit")

        return {
            "pillars": pillars,
            "count": len(pillars),
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def get_reward_epoch(self, address: str) -> dict[str, Any]:
        if not address:
            raise NodeRpcError("reference_reward_address is empty")
        result = self._rpc(
            "embedded.pillar.getFrontierRewardByPage",
            [address, 0, 1],
        )
        if not isinstance(result, dict):
            raise NodeRpcError(
                "embedded.pillar.getFrontierRewardByPage result is not an object"
            )
        items = result.get("list")
        if not isinstance(items, list) or not items:
            raise NodeRpcError(
                "embedded.pillar.getFrontierRewardByPage returned no rewards"
            )
        reward = items[0]
        epoch = _as_int(reward.get("epoch"))
        if epoch is None:
            raise NodeRpcError("Reward entry has no valid epoch")
        return {
            "epoch": epoch,
            "znn_reward": _as_int(reward.get("znnAmount"), 0) or 0,
            "qsr_reward": _as_int(reward.get("qsrAmount"), 0) or 0,
            "source_address": address,
            "source": "node",
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw": reward,
        }

    def get_reward_history(
        self,
        address: str,
        *,
        page_size: int = 100,
    ) -> dict[str, Any]:
        if not address:
            raise NodeRpcError("reference_reward_address is empty")
        page_size = max(1, min(int(page_size), 1000))
        entries: list[dict[str, Any]] = []
        page = 0
        total: int | None = None
        while True:
            result = self._rpc(
                "embedded.pillar.getFrontierRewardByPage",
                [address, page, page_size],
            )
            if not isinstance(result, dict):
                raise NodeRpcError(
                    "embedded.pillar.getFrontierRewardByPage result is not an object"
                )
            items = result.get("list")
            if not isinstance(items, list):
                raise NodeRpcError(
                    "embedded.pillar.getFrontierRewardByPage has no list"
                )
            if total is None:
                total = _as_int(result.get("count"))
            for reward in items:
                if not isinstance(reward, dict):
                    continue
                epoch = _as_int(reward.get("epoch"))
                if epoch is None:
                    continue
                entries.append(
                    {
                        "epoch": epoch,
                        "znn_reward": _as_int(reward.get("znnAmount"), 0) or 0,
                        "qsr_reward": _as_int(reward.get("qsrAmount"), 0) or 0,
                        "source_address": address,
                        "source": "node",
                        "raw": reward,
                    }
                )
            if not items or len(items) < page_size:
                break
            if total is not None and len(entries) >= total:
                break
            page += 1
            if page > 10000:
                raise NodeRpcError("Reward pagination exceeded safety limit")
        if not entries:
            raise NodeRpcError(
                "embedded.pillar.getFrontierRewardByPage returned no rewards"
            )
        latest = max(entries, key=lambda item: item["epoch"])
        observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for entry in entries:
            entry["observed_at"] = observed_at
        return {
            "latest": latest,
            "entries": entries,
            "count": len(entries),
        }
