from __future__ import annotations

import json
import multiprocessing as mp
import os
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from controller.task_library import TASK_LIBRARY
from env.env import VillagerBench, env_type
from env.minecraft_client import Agent
from ml_models.logger import log_step


DEFAULT_SHORT_TASK_TEMPLATE = """You are {agent}, a Minecraft worker.

High-level task:

{task_description}

Last step summary:

{last_step_summary}

You must now choose and execute up to {max_turns} tool calls to move this task forward.
Treat the high-level task as a multi-step project that you work on over many steps. Do NOT restart from scratch every time.

Available tools (names MUST match exactly, do NOT invent new tool names):
- scanNearbyEntities(player_name, item_name, radius, item_num, emotion, murmur)
- navigateTo(player_name, x, y, z, emotion, murmur)
- MineBlock(player_name, x, y, z, emotion, murmur)
- placeBlock(player_name, item_name, x, y, z, facing, emotion, murmur)
- equipItem(player_name, slot, item_name, emotion, murmur)
- craftBlock(player_name, item_name, count, emotion, murmur)
- attackTarget(player_name, target_name, emotion, murmur)

Important conventions and error handling:

- NEVER use coordinates (0, 0, 0) unless they came directly from a tool output.

- If a tool returns "cannot reach crafting_table, or there is no crafting_table":
  - Stop calling craftBlock in place.
  - Either scan for a crafting_table, navigate to it, or craft and place a crafting_table if you have wood.

- If a tool returns "move failed" or "blocked":
  - Do not keep calling navigateTo with the same coordinates.
  - Choose a closer or more accessible location instead.

- If a tool returns "I can't find anything named X":
  - Treat that as a hard constraint: X is not available nearby.
  - Do not immediately repeat the same scan with the same X.
  - Try a more specific or alternate resource (e.g., "cow" or "pig" instead of "animal").

- item_name must be realistic:
  - Use specific blocks/mobs like "cow", "pig", "oak_log", "stone", "grass".
  - Do NOT use generic labels like "animal" or "fishing rod" for blocks.
  - For inventory-related checks, rely on existing tools, not invented ones.

Planning:
- Use up to {max_turns} tool calls per step. For example:
  - Step 1: scanNearbyEntities for oak_log.
  - Step 2: navigateTo the oak_log.
  - Step 3: MineBlock the oak_log.

- After you finish your sequence of tool calls for this step, STOP and return the final answer.
  The environment will call you again with your last step summary so you can continue.

Return ONLY the JSON action/tool calls and your final answer in the expected format.
"""


def load_api_keys(path: Optional[str]) -> List[str]:
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            keys = data.get("OPENAI") or data.get("AGENT_KEY") or []
            if keys:
                return keys
        except FileNotFoundError:
            print(f"[WARN] API key file '{path}' not found; falling back to env var.")
    env_value = os.environ.get("VILLAGER_AGENT_API_KEY", "")
    keys = [k.strip() for k in env_value.split(",") if k.strip()]
    if not keys:
        raise RuntimeError(
            "No API keys provided. Supply --api-key-file or set VILLAGER_AGENT_API_KEY."
        )
    return keys


def resolve_env_type(name: str) -> int:
    mapping = {
        "construction": env_type.construction,
        "farming": env_type.farming,
        "puzzle": env_type.puzzle,
        "auto": env_type.auto,
        "meta": env_type.meta,
        "gen": env_type.gen,
        "none": env_type.none,
    }
    return mapping[name]


@dataclass
class WorkerConfig:
    agent_name: str
    env_kind: int
    task_id: int
    dig_needed: bool
    host: str
    port: int
    task_name: str
    base_port: int
    max_turns: int
    fast_api: bool
    server_debug: bool
    step_delay: float
    task_mode: str
    llm_model: str
    llm_base_url: str
    api_keys: List[str]
    clear_logs: bool
    task_template: str
    task_library: Sequence[Dict[str, str]]


class TaskFeeder:
    def __init__(self, tasks: Sequence[Dict[str, str]], mode: str, template: str, max_turns: int):
        if not tasks:
            raise ValueError("Task library is empty; cannot drive agents.")
        self.tasks = list(tasks)
        self.mode = mode
        self.template = template
        self.max_turns = max_turns
        self._cursor = 0

    def next_instruction(self, agent_name: str, last_feedback: Optional[str] = None) -> str:
        if self.mode == "random":
            spec = random.choice(self.tasks)
        else:
            spec = self.tasks[self._cursor % len(self.tasks)]
            self._cursor += 1
        description = spec["description"].strip()
        last_summary = (last_feedback or "").strip()
        if last_summary and len(last_summary) > 400:
            last_summary = last_summary[:400] + "..."
        if not last_summary:
            last_summary = "No previous step; this is your first action."
        return self.template.format(
            agent=agent_name,
            task_description=description,
            last_step_summary=last_summary,
            max_turns=self.max_turns,
        )


def worker_entry(cfg: WorkerConfig, stop_event: mp.Event, status_queue: Optional[mp.Queue] = None) -> None:
    def send_status(event: str, **payload):
        if status_queue is not None:
            status_queue.put({"agent": cfg.agent_name, "event": event, **payload})

    # NOTE: This worker process owns a single agent + VillagerBench instance.
    random.seed(os.getpid() + int(time.time()))
    Agent.configure_llm(
        model=cfg.llm_model,
        base_url=cfg.llm_base_url,
        api_keys=cfg.api_keys,
        max_iterations=cfg.max_turns,
    )
    bench = VillagerBench(
        cfg.env_kind,
        cfg.task_id,
        cfg.dig_needed,
        host=cfg.host,
        port=cfg.port,
        task_name=cfg.task_name,
        base_port_start=cfg.base_port,
        max_turn_per_step=cfg.max_turns,
        clear_logs_on_init=cfg.clear_logs,
    )
    bench.langchain_model = cfg.llm_model
    bench.agent_register(agent_number=1, name_list=[cfg.agent_name])
    feeder = TaskFeeder(cfg.task_library, cfg.task_mode, cfg.task_template, cfg.max_turns)
    send_status("starting", base_port=cfg.base_port)
    last_feedback_str: Optional[str] = None
    try:
        with bench.run(server_debug=cfg.server_debug, fast_api=cfg.fast_api):
            send_status("running")
            while not stop_event.is_set():
                instruction = feeder.next_instruction(cfg.agent_name, last_feedback_str)
                start = time.time()
                try:
                    feedback, detail = bench.step(cfg.agent_name, instruction)
                    duration = time.time() - start
                    # normalize feedback for continuity
                    if isinstance(feedback, str):
                        last_feedback_str = feedback.strip().replace("\n", " ")
                    else:
                        last_feedback_str = str(feedback)
                    if last_feedback_str and len(last_feedback_str) > 400:
                        last_feedback_str = last_feedback_str[:400] + "..."

                    print(f"[{cfg.agent_name}] step done in {duration:.2f}s -> {(feedback or 'UNKNOWN')}")
                    send_status(
                        "step",
                        duration=duration,
                        feedback=feedback or "UNKNOWN",
                        detail=detail,
                    )
                    # ML log
                    log_step({
                        "agent": cfg.agent_name,
                        "task_name": cfg.task_name,
                        "max_turns": cfg.max_turns,
                        "duration": duration,
                        "feedback": feedback,
                        "timestamp": time.time(),
                    })
                except Exception as exc:
                    send_status("error", message=str(exc))
                    time.sleep(max(cfg.step_delay, 1.0))
                    continue
                time.sleep(cfg.step_delay)
    except KeyboardInterrupt:
        pass
    finally:
        bench.stop()
        send_status("stopped")


def spawn_workers(
    configs: Sequence[WorkerConfig],
    status_queue: Optional[mp.Queue] = None,
) -> tuple[List[mp.Process], mp.Event]:
    if not configs:
        raise ValueError("No worker configs provided.")
    ctx = mp.get_context("spawn")
    stop_event = ctx.Event()
    processes: List[mp.Process] = []
    for cfg in configs:
        proc = ctx.Process(target=worker_entry, args=(cfg, stop_event, status_queue), daemon=False)
        proc.start()
        processes.append(proc)
    return processes, stop_event

