from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any, Iterable

from utils.node_rpc_wrapper import NodeRpcError, NodeRpcWrapper


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class NodeNotReadyError(NodeRpcError):
    """Raised when a reachable node is not suitable for collection."""


class NodePoolError(NodeRpcError):
    """Raised when no configured node can provide a valid observation."""


@dataclass
class NodePollResult:
    status: str
    node_url: str
    latest_momentum: dict[str, Any]
    pillars: dict[str, dict[str, Any]] | None = None
    epoch_data: dict[str, Any] | None = None
    epoch_history: list[dict[str, Any]] | None = None


class NodeRpcPool:
    """Select a healthy, synchronized node and fail over between endpoints."""

    def __init__(
        self,
        nodes: Iterable[NodeRpcWrapper],
        *,
        require_sync_info: bool = False,
        max_frontier_age_seconds: float = 300,
        failure_cooldown_seconds: float = 120,
    ):
        self.nodes = list(nodes)
        if not self.nodes:
            raise ValueError("At least one node RPC endpoint is required")
        self.require_sync_info = bool(require_sync_info)
        self.max_frontier_age_seconds = max(0.0, float(max_frontier_age_seconds))
        self.failure_cooldown_seconds = max(
            0.0,
            float(failure_cooldown_seconds),
        )
        self.active_index = 0
        self._unavailable_until = [0.0] * len(self.nodes)

    @property
    def active_node_url(self) -> str:
        return self._node_url(self.nodes[self.active_index])

    @staticmethod
    def _node_url(node: NodeRpcWrapper) -> str:
        return str(getattr(node, "node_url", "<custom node>"))

    def _candidate_indices(self) -> list[int]:
        now = time.monotonic()
        available = [
            index
            for index, unavailable_until in enumerate(self._unavailable_until)
            if unavailable_until <= now
        ]
        if not available:
            available = list(range(len(self.nodes)))

        # Prefer the active endpoint, but periodically probe the primary so a
        # recovered primary becomes preferred again.
        if self.active_index != 0 and 0 in available:
            preferred = [0, self.active_index]
        else:
            preferred = [self.active_index]
        return preferred + [index for index in available if index not in preferred]

    def _mark_failure(self, index: int) -> None:
        self._unavailable_until[index] = (
            time.monotonic() + self.failure_cooldown_seconds
        )

    def _mark_success(self, index: int) -> None:
        previous_index = self.active_index
        self.active_index = index
        self._unavailable_until[index] = 0.0
        if previous_index != index:
            print(
                "Node RPC failover: switched from "
                f"{self._node_url(self.nodes[previous_index])} to "
                f"{self._node_url(self.nodes[index])}"
            )

    def _validate_node(
        self,
        node: NodeRpcWrapper,
        latest_momentum: dict[str, Any],
    ) -> None:
        timestamp = _as_int(latest_momentum.get("timestamp"))
        if (
            self.max_frontier_age_seconds > 0
            and timestamp is not None
            and timestamp >= 1_000_000_000
        ):
            age = max(0.0, datetime.now(timezone.utc).timestamp() - timestamp)
            if age > self.max_frontier_age_seconds:
                raise NodeNotReadyError(
                    f"frontier is {age:.0f}s old; maximum is "
                    f"{self.max_frontier_age_seconds:.0f}s"
                )

        get_sync_info = getattr(node, "get_sync_info", None)
        if not callable(get_sync_info):
            if self.require_sync_info:
                raise NodeNotReadyError("stats.syncInfo is not available")
            return

        try:
            sync_info = get_sync_info()
        except NodeRpcError as exc:
            if self.require_sync_info:
                raise NodeNotReadyError(
                    f"stats.syncInfo check failed: {exc}"
                ) from exc
            return

        if not isinstance(sync_info, dict):
            raise NodeNotReadyError("stats.syncInfo did not return an object")
        state = _as_int(sync_info.get("state"))
        current_height = _as_int(sync_info.get("currentHeight"))
        target_height = _as_int(sync_info.get("targetHeight"))
        if state is not None and state != 2:
            raise NodeNotReadyError(
                f"node sync state is {state}, expected 2 (synced)"
            )
        if (
            current_height is not None
            and target_height is not None
            and current_height < target_height
        ):
            raise NodeNotReadyError(
                f"node is at height {current_height}, target is {target_height}"
            )
        if self.require_sync_info and state is None:
            raise NodeNotReadyError("stats.syncInfo did not return a state")

    def collect_snapshot(
        self,
        *,
        reference_reward_address: str,
        previous_height: int | None,
        previous_hash: str | None,
        previous_epoch: int | None,
        reward_page_size: int,
        allow_empty_pillars: bool,
    ) -> NodePollResult:
        errors: list[str] = []
        stale_candidates: list[tuple[int, dict[str, Any]]] = []
        reorg_candidates: list[tuple[int, dict[str, Any]]] = []

        for index in self._candidate_indices():
            node = self.nodes[index]
            try:
                latest_momentum = node.get_latest_momentum()
                current_height = _as_int(latest_momentum.get("height"))
                if current_height is None:
                    raise NodeNotReadyError("frontier momentum has no height")
                self._validate_node(node, latest_momentum)

                if previous_height is not None and current_height < previous_height:
                    reorg_candidates.append((index, latest_momentum))
                    continue

                if previous_height is not None and current_height == previous_height:
                    if (
                        previous_hash
                        and latest_momentum.get("hash")
                        and previous_hash != latest_momentum.get("hash")
                    ):
                        reorg_candidates.append((index, latest_momentum))
                    else:
                        stale_candidates.append((index, latest_momentum))
                    continue

                pillar_data = node.get_all_pillars()
                pillars = pillar_data.get("pillars") or {}
                if not pillars and not allow_empty_pillars:
                    raise NodeNotReadyError(
                        "node returned an empty pillar list"
                    )

                epoch_data = node.get_reward_epoch(reference_reward_address)
                epoch_history = None
                get_reward_history = getattr(node, "get_reward_history", None)
                if (
                    callable(get_reward_history)
                    and (
                        previous_epoch is None
                        or int(epoch_data["epoch"]) > int(previous_epoch)
                    )
                ):
                    history = get_reward_history(
                        reference_reward_address,
                        page_size=reward_page_size,
                    )
                    epoch_data = history["latest"]
                    epoch_history = history["entries"]

                self._mark_success(index)
                return NodePollResult(
                    status="success",
                    node_url=self._node_url(node),
                    latest_momentum=latest_momentum,
                    pillars=pillars,
                    epoch_data=epoch_data,
                    epoch_history=epoch_history,
                )
            except (NodeRpcError, KeyError, TypeError, ValueError) as exc:
                self._mark_failure(index)
                node_url = self._node_url(node)
                errors.append(f"{node_url}: {exc}")
                print(f"Node RPC candidate failed ({node_url}): {exc}")

        if reorg_candidates:
            index, latest_momentum = reorg_candidates[0]
            self._mark_success(index)
            return NodePollResult(
                status="reorg",
                node_url=self._node_url(self.nodes[index]),
                latest_momentum=latest_momentum,
            )

        if stale_candidates:
            index, latest_momentum = max(
                stale_candidates,
                key=lambda item: _as_int(item[1].get("height"), -1) or -1,
            )
            self._mark_success(index)
            return NodePollResult(
                status="stale",
                node_url=self._node_url(self.nodes[index]),
                latest_momentum=latest_momentum,
            )

        joined_errors = "; ".join(errors) or "no usable node response"
        raise NodePoolError(f"All node RPC endpoints failed: {joined_errors}")
