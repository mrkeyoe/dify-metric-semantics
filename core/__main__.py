"""Offline CLI: exercise the whole decision path without Dify.

    python -m core seed
    python -m core list
    python -m core resolve "上个月活跃用户数"
    python -m core arbitrate "上个月活跃用户数" --stub
    python -m core record active_user.7d_login --outcome failure
    python -m core health
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from .config import Config
from .factory import build_service
from .llm import StaticLLMClient
from .seeds import example_definitions

STUB_RESPONSE = json.dumps(
    {
        "equivalent": False,
        "chosen_id": "active_user.7d_login",
        "rationale": (
            "两个口径的统计对象不同：一个按登录行为统计 7 天窗口，另一个按支付成功订单统计 30 天窗口。"
            "「上个月活跃用户数」未说明活跃的判定方式，按默认的登录口径选择，但需要业务方确认。"
        ),
        "confidence": 0.62,
        "requires_human": True,
        "conflicts": [
            {
                "ids": ["active_user.7d_login", "active_user.30d_order"],
                "type": "semantic",
                "detail": "时间窗口与统计实体均不同",
            }
        ],
    },
    ensure_ascii=False,
)


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _service(args: argparse.Namespace, *, llm=None):
    overrides = {}
    if getattr(args, "store", None):
        overrides["store_path"] = args.store
    config = Config.from_env().with_overrides(**overrides)
    return build_service(config=config, llm=llm)


def cmd_seed(args: argparse.Namespace) -> int:
    service = _service(args)
    created: List[str] = []
    skipped: List[str] = []
    for definition in example_definitions():
        if service.store.has(definition.id) and not args.force:
            skipped.append(definition.id)
            continue
        service.register_definition(definition, replace=True)
        created.append(definition.id)
    _print({"ok": True, "registered": created, "already_present": skipped, "store": service.health()["store"]})
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    service = _service(args)
    _print(
        {
            "ok": True,
            "metrics": [
                {
                    "id": item.id,
                    "name": item.name,
                    "owner": item.owner,
                    "confidence": item.confidence,
                    "aliases": item.aliases,
                    "source_tables": item.source_tables,
                }
                for item in service.list_metrics()
            ],
        }
    )
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    service = _service(args)
    _print(service.resolve_to_dict(args.query, metric_name=args.metric_hint, top_k=args.top_k))
    return 0


def cmd_arbitrate(args: argparse.Namespace) -> int:
    llm = StaticLLMClient(STUB_RESPONSE) if args.stub else None
    service = _service(args, llm=llm)
    resolved = service.resolve(args.query)
    if not resolved.candidates:
        _print({"ok": False, "message": "no candidates matched", "resolve": resolved.to_dict()})
        return 2
    result = service.arbitrate(
        resolved.candidates,
        query=args.query,
        precheck=resolved.conflict_report,
        confidence_threshold=args.threshold,
    )
    payload = result.to_dict()
    payload["stub"] = bool(args.stub)
    payload["llm"] = "static-demo-stub" if args.stub else payload["llm"]
    _print(payload)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    service = _service(args)
    success: Optional[bool] = None
    if args.outcome == "success":
        success = True
    elif args.outcome == "failure":
        success = False
    result = service.record_outcome(
        args.metric_id,
        success=success,
        signal=args.signal,
        alpha=args.alpha,
        note=args.note,
    )
    _print({"ok": True, "feedback": result.to_dict()})
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    service = _service(args)
    _print({"ok": True, "health": service.health()})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m core", description="dify-metric-semantics offline CLI")
    parser.add_argument("--store", help="override the JSON store path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed = subparsers.add_parser("seed", help="register the example definitions")
    seed.add_argument("--force", action="store_true", help="overwrite existing definitions")
    seed.set_defaults(func=cmd_seed)

    listing = subparsers.add_parser("list", help="list registered metrics")
    listing.set_defaults(func=cmd_list)

    resolve = subparsers.add_parser("resolve", help="resolve a query into candidate definitions")
    resolve.add_argument("query")
    resolve.add_argument("--metric-hint", default=None)
    resolve.add_argument("--top-k", type=int, default=None)
    resolve.set_defaults(func=cmd_resolve)

    arbitrate = subparsers.add_parser("arbitrate", help="arbitrate candidates for a query")
    arbitrate.add_argument("query")
    arbitrate.add_argument("--threshold", type=float, default=None)
    arbitrate.add_argument("--stub", action="store_true", help="use the offline demo stub instead of a real model")
    arbitrate.set_defaults(func=cmd_arbitrate)

    record = subparsers.add_parser("record", help="write back an execution outcome")
    record.add_argument("metric_id")
    record.add_argument("--outcome", choices=["success", "failure"], default=None)
    record.add_argument("--signal", type=float, default=None)
    record.add_argument("--alpha", type=float, default=None)
    record.add_argument("--note", default="")
    record.set_defaults(func=cmd_record)

    health = subparsers.add_parser("health", help="show store and model configuration")
    health.set_defaults(func=cmd_health)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # CLI must print a readable error, not a traceback
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())