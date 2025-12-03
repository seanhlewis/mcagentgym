#!/usr/bin/env python
"""
Multi-agent GUI controller for VillagerAgent.

This GUI is a front-end that launches multiple worker processes via the
multi-process core. Each worker owns its own VillagerBench + Node bridge.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import queue
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from controller.multiprocess_core import (
    DEFAULT_SHORT_TASK_TEMPLATE,
    WorkerConfig,
    load_api_keys,
    resolve_env_type,
    spawn_workers,
)
from controller.task_library import TASK_LIBRARY
from controller.name_pool import JsonNamePool

try:
    import customtkinter as ctk
except ImportError:
    print(
        "[ERROR] This script requires the 'customtkinter' package for the GUI.\n"
        "Install it with:\n\n"
        "    pip install customtkinter\n"
    )
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VillagerAgent Multi-Agent GUI")
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
    parser.add_argument("--dig-needed", action="store_true")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--fast-api", action="store_true")
    parser.add_argument("--server-debug", action="store_true")
    parser.add_argument("--base-port-start", type=int, default=5000)
    parser.add_argument("--port-step", type=int, default=50)
    parser.add_argument("--max-turns", type=int, default=3)
    parser.add_argument("--step-delay", type=float, default=2.0)
    parser.add_argument("--task-mode", choices=["cycle", "random"], default="cycle")
    parser.add_argument("--api-key-file", type=str, default="API_KEY_LIST")
    parser.add_argument("--llm-model", type=str, default=os.environ.get("VILLAGER_AGENT_MODEL", "gpt-4-1106-preview"))
    parser.add_argument("--llm-base-url", type=str, default=os.environ.get("VILLAGER_AGENT_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--clear-logs", action="store_true", help="Allow each worker to wipe logs/metrics.")
    parser.add_argument("--task-template", type=str, default=DEFAULT_SHORT_TASK_TEMPLATE)
    return parser.parse_args()


class AgentGUI(ctk.CTk):
    def __init__(self, launch_args: argparse.Namespace, api_keys: List[str]):
        super().__init__()

        self.args = launch_args
        self.api_keys = api_keys

        self.agent_names: List[str] = []
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.status_info: Dict[str, Dict[str, Any]] = {}
        self.worker_processes: List[mp.Process] = []
        self.stop_event: Optional[mp.Event] = None
        self.status_queue: Optional[mp.Queue] = None

        launcher_dir = Path(__file__).resolve().parent
        json_path = launcher_dir / "usernames.json"
        self.name_pool = JsonNamePool(str(json_path))

        self.title("VillagerAgent Multi-Agent Control Panel")
        self.geometry("1100x660")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build_ui()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(1000, self._refresh_status)

    def _build_ui(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header_frame = ctk.CTkFrame(self)
        header_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        header_frame.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            header_frame,
            text="VillagerAgent Multi-Agent Control Panel",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title_label.grid(row=0, column=0, sticky="w")

        self.add_count_var = ctk.StringVar(value="1")
        add_count_label = ctk.CTkLabel(header_frame, text="Players to add:")
        add_count_label.grid(row=0, column=1, padx=(20, 4))
        self.add_count_menu = ctk.CTkOptionMenu(
            header_frame,
            variable=self.add_count_var,
            values=[str(i) for i in range(1, 11)],
            width=70,
        )
        self.add_count_menu.grid(row=0, column=2, padx=(0, 4))

        self.add_button = ctk.CTkButton(
            header_frame,
            text="Add New Player",
            command=self._on_add_players_clicked,
            width=130,
        )
        self.add_button.grid(row=0, column=3, padx=(4, 4))

        self.launch_button = ctk.CTkButton(
            header_frame,
            text="Launch Agents",
            command=self._on_launch_clicked,
            width=130,
            fg_color="#2e7d32",
            hover_color="#388e3c",
        )
        self.launch_button.grid(row=0, column=4, padx=(4, 4))

        self.stop_button = ctk.CTkButton(
            header_frame,
            text="Stop Agents",
            command=self._stop_workers,
            width=130,
            fg_color="#c62828",
            hover_color="#e53935",
            state="disabled",
        )
        self.stop_button.grid(row=0, column=5, padx=(4, 0))

        # Tempo controls
        self.step_delay_var = ctk.StringVar(value=str(self.args.step_delay))
        self.max_turns_var = ctk.StringVar(value=str(self.args.max_turns))

        tempo_label = ctk.CTkLabel(header_frame, text="Step delay (s):")
        tempo_label.grid(row=1, column=1, padx=(20, 4), pady=(6, 0), sticky="e")
        self.step_delay_entry = ctk.CTkEntry(header_frame, textvariable=self.step_delay_var, width=70)
        self.step_delay_entry.grid(row=1, column=2, padx=(0, 4), pady=(6, 0))

        max_turns_label = ctk.CTkLabel(header_frame, text="Max turns:")
        max_turns_label.grid(row=1, column=3, padx=(4, 4), pady=(6, 0), sticky="e")
        self.max_turns_entry = ctk.CTkEntry(header_frame, textvariable=self.max_turns_var, width=70)
        self.max_turns_entry.grid(row=1, column=4, padx=(0, 4), pady=(6, 0))

        info_label = ctk.CTkLabel(
            header_frame,
            text=f"Server: {self.args.host}:{self.args.port} | Model: {self.args.llm_model}",
            font=ctk.CTkFont(size=11, slant="italic"),
        )
        info_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

        list_frame = ctk.CTkFrame(self)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        list_frame.grid_rowconfigure(1, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        header_bar = ctk.CTkFrame(list_frame)
        header_bar.grid(row=0, column=0, sticky="ew", pady=(4, 4))
        header_bar.grid_columnconfigure(0, weight=1)
        labels = ["Agent", "Status", "Last Result"]
        for idx, label in enumerate(labels):
            widget = ctk.CTkLabel(header_bar, text=label, font=ctk.CTkFont(size=13, weight="bold"))
            widget.grid(row=0, column=idx, sticky="w", padx=(8 if idx == 0 else 4, 4))

        self.agent_list_frame = ctk.CTkScrollableFrame(list_frame)
        self.agent_list_frame.grid(row=1, column=0, sticky="nsew")
        self.agent_list_frame.grid_columnconfigure(0, weight=1)

    # --- Agent list management ---

    def _queue_agent_name(self, name: str) -> None:
        if name in self.agent_names:
            return
        self.agent_names.append(name)
        row_frame = ctk.CTkFrame(self.agent_list_frame)
        row_frame.grid(row=len(self.agent_names) - 1, column=0, sticky="ew", pady=2, padx=4)
        row_frame.grid_columnconfigure(0, weight=1)

        name_label = ctk.CTkLabel(row_frame, text=name)
        name_label.grid(row=0, column=0, sticky="w", padx=(4, 4))

        status_label = ctk.CTkLabel(row_frame, text="pending", text_color="gray")
        status_label.grid(row=0, column=1, sticky="w", padx=(4, 4))

        result_label = ctk.CTkLabel(row_frame, text="(no steps yet)", anchor="w")
        result_label.grid(row=0, column=2, sticky="w", padx=(4, 4))

        self.rows[name] = {
            "frame": row_frame,
            "status_label": status_label,
            "result_label": result_label,
        }

    def _on_add_players_clicked(self):
        try:
            count = int(self.add_count_var.get())
        except ValueError:
            count = 1
        if count < 1:
            count = 1
        new_names = self.name_pool.next_many(count)
        for n in new_names:
            self._queue_agent_name(n)

    # --- Worker management ---

    def _sync_tempo_from_ui(self):
        try:
            self.args.step_delay = max(0.0, float(self.step_delay_var.get()))
        except ValueError:
            pass
        try:
            self.args.max_turns = max(1, int(self.max_turns_var.get()))
        except ValueError:
            pass

    def _build_worker_configs(self) -> List[WorkerConfig]:
        configs: List[WorkerConfig] = []
        env_kind = resolve_env_type(self.args.env_type)
        for idx, name in enumerate(self.agent_names):
            base_port = self.args.base_port_start + idx * self.args.port_step
            configs.append(
                WorkerConfig(
                    agent_name=name,
                    env_kind=env_kind,
                    task_id=self.args.task_id,
                    dig_needed=self.args.dig_needed,
                    host=self.args.host,
                    port=self.args.port,
                    task_name=self.args.task_name,
                    base_port=base_port,
                    max_turns=max(1, self.args.max_turns),
                    fast_api=self.args.fast_api,
                    server_debug=self.args.server_debug,
                    step_delay=max(0.0, self.args.step_delay),
                    task_mode=self.args.task_mode,
                    llm_model=self.args.llm_model,
                    llm_base_url=self.args.llm_base_url,
                    api_keys=self.api_keys,
                    clear_logs=self.args.clear_logs,
                    task_template=self.args.task_template,
                    task_library=TASK_LIBRARY,
                )
            )
        return configs

    def _on_launch_clicked(self):
        if self.worker_processes:
            return
        if not self.agent_names:
            print("[GUI] No agents queued; add some first.")
            return
        self._sync_tempo_from_ui()
        configs = self._build_worker_configs()
        self.status_queue = mp.Queue()
        self.worker_processes, self.stop_event = spawn_workers(configs, status_queue=self.status_queue)
        self.launch_button.configure(state="disabled")
        self.add_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

    def _stop_workers(self):
        if not self.worker_processes or self.stop_event is None:
            return
        self.stop_event.set()
        for p in self.worker_processes:
            p.join(timeout=5.0)
        self.worker_processes = []
        self.launch_button.configure(state="normal")
        self.add_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _refresh_status(self):
        if self.status_queue is not None:
            while True:
                try:
                    msg = self.status_queue.get_nowait()
                except queue.Empty:
                    break
                agent = msg.get("agent")
                event = msg.get("event")
                row = self.rows.get(agent)
                if not row:
                    continue
                status_label: ctk.CTkLabel = row["status_label"]
                result_label: ctk.CTkLabel = row["result_label"]
                if event == "starting":
                    status_label.configure(text="starting", text_color="orange")
                elif event == "running":
                    status_label.configure(text="running", text_color="green")
                elif event == "step":
                    status_label.configure(text="active", text_color="green")
                    feedback = msg.get("feedback") or ""
                    result_label.configure(text=feedback[:120])
                elif event == "error":
                    status_label.configure(text="error", text_color="red")
                    result_label.configure(text=msg.get("message", "error"))
                elif event == "stopped":
                    status_label.configure(text="stopped", text_color="gray")
        self.after(1000, self._refresh_status)

    def _on_close(self):
        self._stop_workers()
        self.destroy()


def main():
    args = parse_args()
    api_keys = load_api_keys(args.api_key_file)
    if not api_keys:
        print("[ERROR] No API keys available.")
        sys.exit(1)
    app = AgentGUI(args, api_keys)
    app.mainloop()


if __name__ == "__main__":
    mp.freeze_support()
    main()
