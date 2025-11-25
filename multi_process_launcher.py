#!/usr/bin/env python
"""
Multi-process controller for VillagerAgent.

Each worker process owns its own VillagerBench instance (and Node bridge) so we
can safely drive many agents in parallel without sharing the fragile JS barrier.
"""

from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path
from typing import List

from controller.multiprocess_core import (
    DEFAULT_SHORT_TASK_TEMPLATE,
    WorkerConfig,
    load_api_keys,
    resolve_env_type,
    spawn_workers,
)
from controller.task_library import TASK_LIBRARY
from controller.name_pool import JsonNamePool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch multiple VillagerAgent workers (one process per agent)."
    )
    parser.add_argument("--agents", nargs="*", default=[], help="Optional explicit agent names.")
    parser.add_argument("--num-agents", type=int, default=2, help="Number of agents to launch.")
    parser.add_argument("--env-type", choices=[
        "construction",
        "farming",
        "puzzle",
        "auto",
        "meta",
        "gen",
        "none",
    ], default="construction")
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--task-name", type=str, default="survival")
    parser.add_argument("--dig-needed", action="store_true", help="Construction scenario needs digging.")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--fast-api", action="store_true", help="Use minecraft_server_fast.py")
    parser.add_argument("--server-debug", action="store_true")
    parser.add_argument("--base-port-start", type=int, default=5000, help="Starting local Flask port.")
    parser.add_argument("--port-step", type=int, default=50, help="Gap between workers' port ranges.")
    parser.add_argument("--max-turns", type=int, default=3, help="Max LangChain iterations per env.step")
    parser.add_argument("--step-delay", type=float, default=2.0, help="Delay between env.step calls per worker.")
    parser.add_argument("--task-mode", choices=["cycle", "random"], default="cycle")
    parser.add_argument("--api-key-file", type=str, default="API_KEY_LIST")
    parser.add_argument("--llm-model", type=str, default=os.environ.get("VILLAGER_AGENT_MODEL", "gpt-4-1106-preview"))
    parser.add_argument("--llm-base-url", type=str, default=os.environ.get("VILLAGER_AGENT_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--clear-logs", action="store_true", help="Allow each worker to wipe logs/metrics.")
    parser.add_argument("--task-template", type=str, default=DEFAULT_SHORT_TASK_TEMPLATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_keys = load_api_keys(args.api_key_file)
    if not api_keys:
        raise RuntimeError("No API keys available.")
    if not args.agents:
        launcher_dir = Path(__file__).resolve().parent
        json_path = launcher_dir / "usernames.json"
        name_pool = JsonNamePool(str(json_path))
        agent_names = name_pool.next_many(args.num_agents)
    else:
        agent_names = args.agents
    configs: List[WorkerConfig] = []
    env_kind = resolve_env_type(args.env_type)
    for idx, name in enumerate(agent_names):
        base_port = args.base_port_start + idx * args.port_step
        configs.append(
            WorkerConfig(
                agent_name=name,
                env_kind=env_kind,
                task_id=args.task_id,
                dig_needed=args.dig_needed,
                host=args.host,
                port=args.port,
                task_name=args.task_name,
                base_port=base_port,
                max_turns=max(1, args.max_turns),
                fast_api=args.fast_api,
                server_debug=args.server_debug,
                step_delay=max(0.0, args.step_delay),
                task_mode=args.task_mode,
                llm_model=args.llm_model,
                llm_base_url=args.llm_base_url,
                api_keys=api_keys,
                clear_logs=args.clear_logs,
                task_template=args.task_template,
                task_library=TASK_LIBRARY,
            )
        )

    workers, stop_event = spawn_workers(configs)
    print(f"[MAIN] Spawned {len(workers)} worker process(es).")

    def shutdown_handler(signum, frame):
        print(f"\n[MAIN] Received signal {signum}; stopping workers...")
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        while any(p.is_alive() for p in workers):
            for p in workers:
                p.join(timeout=0.5)
    finally:
        stop_event.set()
        for p in workers:
            p.join(timeout=5.0)


if __name__ == "__main__":
    main()

