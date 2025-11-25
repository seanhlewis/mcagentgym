#!/usr/bin/env python
# multi_agent_controller_gui.py
#
# Multi-agent controller for VillagerAgent / VillagerBench.
# - Uses the original, working env.run + agent_register pattern (bots join server correctly).
# - Each agent now runs its own infinite task loop in a dedicated worker thread
#   (no round-robin waiting on other agents).
# - GUI (customtkinter) lets you:
#     * Add random players (before launch) via "Add New Player" + count dropdown.
#     * Remove pending players via a red "X" before launch.
#     * Click "Launch Agents" to register all and start the controller.
# - GUI shows:
#     * Agent name
#     * Status pill (color-coded)
#     * Current task summary
#     * Last result summary
#     * Live "Task Time" counter (seconds on current task or duration of last task).

import argparse
import json
import sys
import threading
import time
import random
from dataclasses import dataclass
from typing import Dict, List, Set, Optional

# ---------------------------------------------------------------------
# 0. Patch OpenAI ChatCompletions to ignore 'encoding' kwarg
# ---------------------------------------------------------------------
try:
    from openai.resources.chat.completions import Completions as _ChatCompletions

    _orig_chat_create = _ChatCompletions.create

    def _patched_chat_create(self, *args, **kwargs):
        kwargs.pop("encoding", None)
        return _orig_chat_create(self, *args, **kwargs)

    _ChatCompletions.create = _patched_chat_create
    print(
        "[INFO] Patched openai.resources.chat.completions.Completions.create "
        "to ignore 'encoding' kwarg."
    )
except Exception as e:
    print("[WARN] Could not patch Completions.create:", e)

# Also patch legacy ChatCompletion.create for safety
try:
    import openai as _openai_mod

    if hasattr(_openai_mod, "ChatCompletion"):
        _orig_legacy_create = _openai_mod.ChatCompletion.create

        def _patched_legacy_create(*args, **kwargs):
            kwargs.pop("encoding", None)
            return _orig_legacy_create(*args, **kwargs)

        _openai_mod.ChatCompletion.create = _patched_legacy_create
        print("[INFO] Patched openai.ChatCompletion.create to ignore 'encoding' kwarg.")
except Exception as e:
    print("[WARN] Could not patch openai.ChatCompletion.create:", e)

# ---------------------------------------------------------------------
# 1. Patch requests.Response.json to never crash on bad JSON
# ---------------------------------------------------------------------
import requests
from requests.models import Response as _Resp

_orig_response_json = _Resp.json


def _safe_response_json(self, *args, **kwargs):
    try:
        return _orig_response_json(self, *args, **kwargs)
    except Exception as e:
        text = self.text or ""
        print(
            f"[WARN] Response.json failed ({e}); returning stub. "
            f"Raw (first 200 chars): {text[:200]!r}"
        )
        return {"message": text, "status": False, "new_events": []}


_Resp.json = _safe_response_json
print("[INFO] Patched requests.Response.json to return stub on JSON decode error.")

# ---------------------------------------------------------------------
# 2. Imports from VillagerAgent repo
# ---------------------------------------------------------------------
from env.env import VillagerBench, env_type, Agent
from pipeline.data_manager import DataManager

# ---------------------------------------------------------------------
# 3. Pretty GUI (customtkinter)
# ---------------------------------------------------------------------
try:
    import customtkinter as ctk
except ImportError:
    print(
        "[ERROR] This script requires the 'customtkinter' package for the GUI.\n"
        "Install it with:\n\n"
        "    pip install customtkinter\n"
    )
    sys.exit(1)


def load_api_keys() -> List[str]:
    """
    Load an API key from API_KEY_LIST in the current working directory.
    Tries OPENAI first, then AGENT_KEY (for backwards compatibility).
    """
    try:
        with open("API_KEY_LIST", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("[ERROR] API_KEY_LIST not found in current directory.")
        sys.exit(1)

    keys = data.get("OPENAI") or data.get("AGENT_KEY") or []
    if not keys:
        print(
            "[ERROR] No keys found in API_KEY_LIST under 'OPENAI' or 'AGENT_KEY'."
        )
        sys.exit(1)
    return keys


# ---------------------------------------------------------------------
# 4. Username generator and task library
# ---------------------------------------------------------------------

# Base pool of name stems; actual usernames will be these plus numeric suffixes
# to guarantee uniqueness (e.g. AliceBot_1273).
BASE_USERNAME_STEMS: List[str] = [
    "AliceBot",
    "BobBot",
    "CharlieBot",
    "DaisyBot",
    "EveBot",
    "FrankBot",
    "GraceBot",
    "NovaBot",
    "PixelBot",
    "MinerBot",
]


class UsernameGenerator:
    """
    Generates random, never-reused usernames based on a base name list.
    """

    def __init__(self, base_names: List[str]):
        self.base_names = list(base_names)
        self._used: Set[str] = set()

    def mark_used(self, names: List[str]) -> None:
        for n in names:
            self._used.add(n)

    def generate_one(self) -> str:
        for _ in range(100000):
            base = random.choice(self.base_names)
            suffix = random.randint(0, 99999)
            name = f"{base}_{suffix}"
            if name not in self._used:
                self._used.add(name)
                return name
        raise RuntimeError("Unable to generate a new unique username.")

    def generate_many(self, count: int) -> List[str]:
        return [self.generate_one() for _ in range(count)]


# Survival-style task curriculum (same as before).
TASK_LIBRARY: List[Dict[str, str]] = [
    {
        "id": "gather_wood_and_store",
        "description": (
            "Gather basic wood resources near your starting area.\n"
            "- Move to a nearby forest or tree.\n"
            "- Chop down enough trees to collect at least ~32 wood logs.\n"
            "- Craft some logs into planks and sticks.\n"
            "- If there is no chest near the main base / spawn, craft a chest and place it.\n"
            "- Store surplus logs and planks into that chest so that human players can reuse them.\n"
            "Avoid destroying obviously player-built structures."
        ),
    },
    {
        "id": "mine_stone_and_coal",
        "description": (
            "Set up early-game mining infrastructure.\n"
            "- Find exposed stone or dig a simple staircase mine.\n"
            "- Collect at least ~64 cobblestone and some coal.\n"
            "- Craft a furnace if there is not already one at the base.\n"
            "- Upgrade to at least stone tools if possible.\n"
            "- Place the furnace in a reasonable base area near the main chest."
        ),
    },
    {
        "id": "smelt_iron_and_craft_tools",
        "description": (
            "Upgrade the tech tree by getting iron tools.\n"
            "- Explore caves or dig deeper to find iron ore.\n"
            "- Mine a reasonable amount of iron ore (e.g., enough for tools and armor pieces).\n"
            "- Smelt the iron ore into ingots using a furnace and fuel.\n"
            "- Craft iron tools (pickaxe and sword at minimum, more if resources allow).\n"
            "- Store at least one spare iron pickaxe and sword in a chest for humans."
        ),
    },
    {
        "id": "set_up_wheat_farm",
        "description": (
            "Establish a small, sustainable wheat farm near the base.\n"
            "- Acquire seeds by breaking grass if needed.\n"
            "- Find or create farmland near a water source.\n"
            "- Till the soil with a hoe and plant wheat seeds in neat rows.\n"
            "- Place fences and torches around the farm to protect it.\n"
            "- If crops are already grown, harvest and replant to keep the farm sustainable.\n"
            "- Store extra seeds and wheat in a chest."
        ),
    },
    {
        "id": "farm_to_table_cooking",
        "description": (
            "Create a 'farm-to-table' food pipeline.\n"
            "- Humanely hunt nearby animals (cows, pigs, chickens, sheep) for food and materials.\n"
            "- Collect raw meat and useful drops (leather, feathers, etc.).\n"
            "- Use a furnace or smoker to cook the meat.\n"
            "- Store cooked food in a base chest labeled or obviously used for food.\n"
            "- Try not to completely wipe out local animal populations; leave some for breeding."
        ),
    },
    {
        "id": "build_starter_house",
        "description": (
            "Build a small, safe starter house near the main base.\n"
            "- Pick a flat area close to spawn / the main chest.\n"
            "- Build a compact wooden house (e.g., 5x5 or slightly larger) with walls and a roof.\n"
            "- Add a door, some windows (or equivalent openings), and basic interior lighting.\n"
            "- Place at least a bed, chest, and crafting table inside.\n"
            "- Ensure the inside is safe from mobs (no dark spots, no big holes in the walls)."
        ),
    },
    {
        "id": "build_watchtower_with_ladder",
        "description": (
            "Create a simple watchtower that players can climb.\n"
            "- Near the base, erect a vertical pillar or small tower (e.g., 10–15 blocks tall).\n"
            "- Attach a ladder or staircase so it is easy to climb.\n"
            "- Build a small platform at the top with a railing (fences) and torches for safety.\n"
            "- Ensure the base of the tower is clearly accessible to humans."
        ),
    },
    {
        "id": "organize_storage",
        "description": (
            "Improve storage organization around the base.\n"
            "- Open nearby chests and inspect their contents.\n"
            "- Create or repurpose a few chests to group items by type: blocks, ores/ingots, food, tools, etc.\n"
            "- Move items to form neat stacks and category-based storage.\n"
            "- If signs or item frames are available, label chests logically.\n"
            "- Avoid throwing items away unless the inventory is clearly junk (e.g., random single blocks)."
        ),
    },
    {
        "id": "explore_and_light_caves",
        "description": (
            "Safely explore nearby caves and light them up.\n"
            "- Craft or obtain a good number of torches first.\n"
            "- Locate nearby caves or dark areas that could spawn hostile mobs.\n"
            "- Carefully explore, placing torches to light up the path and main branches.\n"
            "- Prioritize safety: avoid lava, long falls, or staying near mobs without equipment.\n"
            "- If you find useful resources (coal, iron, copper, etc.), mine some along the way."
        ),
    },
    {
        "id": "fishing_and_feeding",
        "description": (
            "Set up fishing as an alternate food source.\n"
            "- If you do not have a fishing rod, craft one from sticks and string.\n"
            "- Find a suitable body of water and start fishing.\n"
            "- Catch several fish; cook them if possible using a furnace.\n"
            "- Store the cooked fish in a food chest or, if players are nearby, share some with them.\n"
            "- Keep the fishing area reasonably lit and safe."
        ),
    },
]


# ---------------------------------------------------------------------
# 5. Agent state & AgentController with per-agent worker threads
# ---------------------------------------------------------------------


@dataclass
class AgentState:
    name: str
    current_task_id: str = ""
    current_task_description: str = ""
    status: str = "IDLE"  # IDLE, RUNNING, SUCCESS, FAILED, DONE, ERROR
    last_result_short: str = ""
    tasks_completed: int = 0
    next_task_index: int = 0
    last_started_at: float = 0.0
    last_task_duration: float = 0.0  # seconds for most recently completed task


class AgentController:
    """
    Drives all agents in a single VillagerBench environment.

    Changes from original:
      - Still uses env.run(...) once, with env.agent_register(...) called once
        BEFORE the controller starts (handled by GUI).
      - Instead of a single round-robin loop over agents, it now creates
        ONE worker thread per agent. Each worker thread runs:
            while not stop:
                _run_single_task(agent_name)
        so agents do not wait on each other at the controller level.
    """

    def __init__(
        self,
        env: VillagerBench,
        agent_names: List[str],
        task_library: List[Dict[str, str]],
    ):
        self.env = env
        self.task_library = task_library
        self.states: Dict[str, AgentState] = {
            name: AgentState(name=name) for name in agent_names
        }
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._workers: List[threading.Thread] = []

    # ---- Public API -------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        print("[INFO] Stop requested; controller loop will exit after current step.")
        self._stop_event.set()

    def get_states_snapshot(self) -> List[AgentState]:
        """
        Return a shallow copy of agent states for safe use by the GUI thread.
        """
        with self._lock:
            return [AgentState(**vars(s)) for s in self.states.values()]

    # ---- Internal loop ----------------------------------------------

    def _run_loop(self) -> None:
        print("[INFO] AgentController loop starting.")

        dm = DataManager(silent=False)
        try:
            init_state = self.env.get_init_state()
        except Exception as e:
            print(f"[WARN] env.get_init_state() failed: {e}")
            init_state = []
        dm.update_database_init(init_state)

        # Keep env.run context around all worker threads
        with self.env.run(fast_api=False):
            # Spawn one worker per agent
            for name in list(self.states.keys()):
                worker = threading.Thread(
                    target=self._worker_loop, args=(name,), daemon=True
                )
                self._workers.append(worker)
                worker.start()

            # Just wait for stop signal; workers do the actual work
            while not self._stop_event.is_set():
                time.sleep(0.5)

            # Try to join workers politely
            for worker in self._workers:
                worker.join(timeout=2.0)

        print("[INFO] AgentController loop exited.")

    def _worker_loop(self, agent_name: str) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_single_task(agent_name)
            except Exception as e:
                # Should be rare since _run_single_task already catches env.step errors.
                print(f"[ERROR] Unhandled exception in worker for {agent_name}: {e}")
                with self._lock:
                    state = self.states.get(agent_name)
                    if state:
                        state.status = "ERROR"
                        state.last_result_short = f"Worker error: {e}"
                break

    def _run_single_task(self, agent_name: str) -> None:
        # Pick next task and mark state as RUNNING
        with self._lock:
            state = self.states[agent_name]
            task = self._pick_next_task_locked(state)
            state.current_task_id = task["id"]
            state.current_task_description = task["description"]
            state.status = "RUNNING"
            now = time.time()
            state.last_started_at = now
            start_time = now

        instruction = self._build_task_prompt(agent_name, task["description"])

        try:
            feedback, detail = self.env.step(agent_name, instruction)
        except Exception as e:
            with self._lock:
                state = self.states[agent_name]
                state.status = "ERROR"
                state.last_result_short = f"env.step error: {e}"
                state.last_task_duration = max(0.0, time.time() - start_time)
            return

        status, summary = self._parse_feedback(feedback)
        finish_time = time.time()
        duration = max(0.0, finish_time - start_time)

        with self._lock:
            state = self.states[agent_name]
            state.status = status
            state.last_result_short = summary
            state.last_task_duration = duration
            if status == "SUCCESS":
                state.tasks_completed += 1

    def _pick_next_task_locked(self, state: AgentState) -> Dict[str, str]:
        """
        Simple curriculum: cycle through TASK_LIBRARY in order per agent.
        """
        if not self.task_library:
            raise RuntimeError("Task library is empty.")
        idx = state.next_task_index % len(self.task_library)
        state.next_task_index += 1
        return self.task_library[idx]

    @staticmethod
    def _build_task_prompt(agent_name: str, task_description: str) -> str:
        """
        Wrap the high-level task in an instruction that the BaseAgent pipeline
        can follow, including a termination protocol we can parse.
        """
        return f"""
You are {agent_name}, a Minecraft agent in a live Minecraft world.
You can only act by calling tools (Actions) such as scanNearbyEntities, navigateTo,
MineBlock, placeBlock, craftBlock, withdrawItem, storeItem, SmeltingCooking,
attackTarget, startFishing, mountEntity, and other tools available in your API.

# High-level task
{task_description}

# Execution guidelines
- Stay focused on this task; do not wander aimlessly or start unrelated projects.
- Use your tools to gather resources, move, interact, craft, fight, build and farm.
- Try to keep yourself reasonably safe (avoid lava, big falls, suffocation, drowning).
- If resources or world conditions make the task impossible or partially doable,
  clearly explain what is missing and what you managed to do.
- Use at MOST about 10 Actions (tool calls) before finishing, unless a few more
  are absolutely necessary.

# When you are done
- Stop taking new actions and output a single 'Final Answer'.
- In the Final Answer, briefly summarize:
  * What you did.
  * What the world / base looks like now relative to the task.
- At the end of the Final Answer include exactly one line:
    TASK_STATUS: success
  or
    TASK_STATUS: failed

Once you output the Final Answer with TASK_STATUS, STOP. Do not continue acting.
"""

    @staticmethod
    def _parse_feedback(feedback) -> (str, str):
        """
        Extract a coarse status flag from the feedback and a short summary.

        We expect the LLM to include a line like:
          TASK_STATUS: success
        or
          TASK_STATUS: failed
        """
        if not isinstance(feedback, str):
            feedback_str = str(feedback)
        else:
            feedback_str = feedback

        normalized = feedback_str.lower()
        if "task_status: success" in normalized:
            status = "SUCCESS"
        elif "task_status: failed" in normalized:
            status = "FAILED"
        else:
            status = "DONE"

        summary = feedback_str.strip().replace("\n", " ")
        if len(summary) > 160:
            summary = summary[:157] + "..."

        return status, summary


# ---------------------------------------------------------------------
# 6. customtkinter GUI wrapper (with Task Time column)
# ---------------------------------------------------------------------


class AgentGUI(ctk.CTk):
    """
    customtkinter-based GUI that keeps the env / registration behavior intact,
    but lets you configure agents before launch and then monitor them.

    Flow:
      1. Use "Add New Player" + count dropdown to queue random usernames.
      2. (Optional) Remove any queued player with the red "X".
      3. Click "Launch Agents" to:
         - Call env.agent_register(...) ONCE with the full list.
         - Start AgentController (which does env.run(...) and per-agent threads).
         - Disable Add / X / Launch buttons.
      4. After launch, rows show status, current task, last result, and live
         "Task Time" for each agent.
    """

    def __init__(
        self,
        env: VillagerBench,
        agent_tool: List,
        task_library: List[Dict[str, str]],
        username_gen: UsernameGenerator,
        refresh_interval_ms: int = 1000,
    ):
        super().__init__()

        self.env = env
        self.agent_tool = agent_tool
        self.task_library = task_library
        self.username_gen = username_gen
        self.refresh_interval_ms = refresh_interval_ms

        self.agent_names: List[str] = []  # queued names
        self.controller: Optional[AgentController] = None

        # --- Window / theme ------------------------------------------
        self.title("VillagerAgent Multi-Agent Control Panel")
        self.geometry("1100x660")

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # --- Header / controls ---------------------------------------
        header_frame = ctk.CTkFrame(self)
        header_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        header_frame.grid_columnconfigure(0, weight=1)
        header_frame.grid_columnconfigure(1, weight=0)
        header_frame.grid_columnconfigure(2, weight=0)
        header_frame.grid_columnconfigure(3, weight=0)
        header_frame.grid_columnconfigure(4, weight=0)

        title_label = ctk.CTkLabel(
            header_frame,
            text="VillagerAgent Multi-Agent Control Panel",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title_label.grid(row=0, column=0, sticky="w")

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="Queue players, then launch them into your server. Each agent runs independently.",
            font=ctk.CTkFont(size=12),
        )
        subtitle_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.add_count_var = ctk.StringVar(value="1")
        add_count_label = ctk.CTkLabel(
            header_frame,
            text="Players to add:",
            font=ctk.CTkFont(size=12),
        )
        add_count_label.grid(row=0, column=1, padx=(20, 4), sticky="e")

        self.add_count_menu = ctk.CTkOptionMenu(
            header_frame,
            variable=self.add_count_var,
            values=[str(i) for i in range(1, 11)],
            width=70,
        )
        self.add_count_menu.grid(row=0, column=2, padx=(0, 4), sticky="e")

        self.add_button = ctk.CTkButton(
            header_frame,
            text="Add New Player",
            command=self._on_add_players_clicked,
            width=130,
        )
        self.add_button.grid(row=0, column=3, padx=(4, 4), sticky="e")

        self.launch_button = ctk.CTkButton(
            header_frame,
            text="Launch Agents",
            command=self._on_launch_clicked,
            width=130,
            fg_color="#2e7d32",
            hover_color="#388e3c",
        )
        self.launch_button.grid(row=0, column=4, padx=(4, 0), sticky="e")

        info_label = ctk.CTkLabel(
            header_frame,
            text="Tip: You can only add/remove players before launching. Close the window to stop everything.",
            font=ctk.CTkFont(size=11, slant="italic"),
        )
        info_label.grid(row=1, column=1, columnspan=4, sticky="e")

        # --- Agent list ------------------------------------------------
        list_frame = ctk.CTkFrame(self)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        list_frame.grid_rowconfigure(1, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        header_bar = ctk.CTkFrame(list_frame)
        header_bar.grid(row=0, column=0, sticky="ew", pady=(4, 4))
        for col in range(6):
            header_bar.grid_columnconfigure(col, weight=1 if col in (0, 2, 3) else 0)

        h_name = ctk.CTkLabel(
            header_bar,
            text="Agent",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        h_name.grid(row=0, column=0, sticky="w", padx=(8, 4))

        h_status = ctk.CTkLabel(
            header_bar,
            text="Status",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        h_status.grid(row=0, column=1, sticky="w", padx=(4, 4))

        h_task = ctk.CTkLabel(
            header_bar,
            text="Current Task",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        h_task.grid(row=0, column=2, sticky="w", padx=(4, 4))

        h_time = ctk.CTkLabel(
            header_bar,
            text="Task Time",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        h_time.grid(row=0, column=3, sticky="w", padx=(4, 4))

        h_result = ctk.CTkLabel(
            header_bar,
            text="Last Result",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        h_result.grid(row=0, column=4, sticky="w", padx=(4, 4))

        h_actions = ctk.CTkLabel(
            header_bar,
            text="",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        h_actions.grid(row=0, column=5, sticky="e", padx=(4, 8))

        self.agent_list_frame = ctk.CTkScrollableFrame(list_frame)
        self.agent_list_frame.grid(row=1, column=0, sticky="nsew")
        self.agent_list_frame.grid_columnconfigure(0, weight=1)

        # Map: agent_name -> dict of widgets
        self.rows: Dict[str, Dict[str, object]] = {}

        # Wire close event
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Start periodic refresh
        self.after(self.refresh_interval_ms, self._refresh)

    # --- GUI interactions --------------------------------------------

    def _on_close(self):
        if self.controller is not None:
            self.controller.stop()
        self.destroy()

    def _on_add_players_clicked(self):
        try:
            count = int(self.add_count_var.get())
        except ValueError:
            count = 1
        if count < 1:
            count = 1

        new_names = self.username_gen.generate_many(count)
        print(f"[GUI] Queuing {count} player(s): {', '.join(new_names)}")
        for name in new_names:
            self.agent_names.append(name)
            self._ensure_row(name)
            row = self.rows[name]
            status_label: ctk.CTkLabel = row["status_label"]  # type: ignore[assignment]
            status_label.configure(text="PENDING", fg_color="#f9a825")

    def _on_launch_clicked(self):
        # Only launch once
        if self.controller is not None:
            return
        if not self.agent_names:
            print("[GUI] No agents to launch.")
            return

        print(
            f"[GUI] Launching {len(self.agent_names)} agent(s): "
            f"{', '.join(self.agent_names)}"
        )

        # EXACTLY like original script: register all agents once, then start controller
        self.env.agent_register(
            agent_tool=self.agent_tool,
            agent_number=len(self.agent_names),
            name_list=self.agent_names,
        )

        self.controller = AgentController(
            self.env, list(self.agent_names), self.task_library
        )
        self.controller.start()

        # Disable adding/removing after launch
        self.add_button.configure(state="disabled")
        self.launch_button.configure(state="disabled")
        self.add_count_menu.configure(state="disabled")
        for row in self.rows.values():
            remove_button: ctk.CTkButton = row["remove_button"]  # type: ignore[assignment]
            remove_button.configure(state="disabled")

    def _on_remove_clicked(self, agent_name: str):
        # Only allowed before launch
        if self.controller is not None:
            return
        print(f"[GUI] Removing queued player: {agent_name}")
        if agent_name in self.agent_names:
            self.agent_names.remove(agent_name)
        if agent_name in self.rows:
            row = self.rows.pop(agent_name)
            frame = row["frame"]
            frame.grid_forget()
            frame.destroy()

    # --- Row helpers -------------------------------------------------

    def _ensure_row(self, agent_name: str):
        if agent_name in self.rows:
            return

        frame = ctk.CTkFrame(self.agent_list_frame)
        frame.grid_columnconfigure(0, weight=0)  # name
        frame.grid_columnconfigure(1, weight=0)  # status
        frame.grid_columnconfigure(2, weight=2)  # task
        frame.grid_columnconfigure(3, weight=0)  # time
        frame.grid_columnconfigure(4, weight=3)  # result
        frame.grid_columnconfigure(5, weight=0)  # actions

        name_label = ctk.CTkLabel(
            frame, text=agent_name, font=ctk.CTkFont(size=14, weight="bold")
        )
        name_label.grid(row=0, column=0, sticky="w", padx=(10, 6), pady=4)

        status_label = ctk.CTkLabel(
            frame,
            text="PENDING",
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=999,
            fg_color="#f9a825",
            padx=12,
            pady=4,
        )
        status_label.grid(row=0, column=1, sticky="w", padx=(4, 4), pady=4)

        task_label = ctk.CTkLabel(
            frame,
            text="",
            font=ctk.CTkFont(size=12),
            anchor="w",
        )
        task_label.grid(row=0, column=2, sticky="w", padx=(4, 4), pady=4)

        time_label = ctk.CTkLabel(
            frame,
            text="0.0s",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        time_label.grid(row=0, column=3, sticky="w", padx=(4, 4), pady=4)

        result_label = ctk.CTkLabel(
            frame,
            text="",
            font=ctk.CTkFont(size=12),
            anchor="w",
            wraplength=420,
            justify="left",
        )
        result_label.grid(row=0, column=4, sticky="w", padx=(4, 4), pady=4)

        remove_button = ctk.CTkButton(
            frame,
            text="✕",
            width=32,
            height=32,
            fg_color="#b3261e",
            hover_color="#d32f2f",
            font=ctk.CTkFont(size=16, weight="bold"),
            command=lambda n=agent_name: self._on_remove_clicked(n),
        )
        remove_button.grid(row=0, column=5, sticky="e", padx=(4, 10), pady=4)

        self.rows[agent_name] = {
            "frame": frame,
            "name_label": name_label,
            "status_label": status_label,
            "task_label": task_label,
            "time_label": time_label,
            "result_label": result_label,
            "remove_button": remove_button,
        }

    def _status_color(self, status: str) -> str:
        status = status.upper()
        if status in ("SUCCESS", "DONE"):
            return "#2e7d32"  # green
        if status in ("FAILED", "ERROR"):
            return "#c62828"  # red
        if status == "RUNNING":
            return "#1565c0"  # blue
        if status == "IDLE":
            return "#3b3b3b"
        if status == "PENDING":
            return "#f9a825"
        return "#3b3b3b"

    def _refresh(self):
        # Lay out rows in order (queued list first)
        for idx, name in enumerate(self.agent_names):
            if name in self.rows:
                frame = self.rows[name]["frame"]
                frame.grid(row=idx, column=0, sticky="ew", padx=4, pady=4)

        # Once controller is running, pull live states
        if self.controller is not None:
            states = self.controller.get_states_snapshot()
            states_by_name = {s.name: s for s in states}
            now = time.time()

            for name, row in self.rows.items():
                status_label: ctk.CTkLabel = row["status_label"]  # type: ignore[assignment]
                task_label: ctk.CTkLabel = row["task_label"]  # type: ignore[assignment]
                time_label: ctk.CTkLabel = row["time_label"]  # type: ignore[assignment]
                result_label: ctk.CTkLabel = row["result_label"]  # type: ignore[assignment]

                state = states_by_name.get(name)
                if state is None:
                    continue

                # Update status pill
                status_text = state.status
                status_label.configure(
                    text=status_text, fg_color=self._status_color(status_text)
                )

                # Task display
                task_display = state.current_task_id or ""
                if state.current_task_description:
                    desc = state.current_task_description.replace("\n", " ")
                    if len(desc) > 60:
                        desc = desc[:57] + "..."
                    if task_display:
                        task_display = f"{task_display}: {desc}"
                    else:
                        task_display = desc
                task_label.configure(text=task_display)

                # Task time
                if state.status == "RUNNING" and state.last_started_at > 0:
                    elapsed = max(0.0, now - state.last_started_at)
                    time_label.configure(text=f"{elapsed:5.1f}s")
                else:
                    if state.last_task_duration > 0:
                        time_label.configure(text=f"{state.last_task_duration:5.1f}s")
                    else:
                        time_label.configure(text="—")

                # Result display
                result_display = state.last_result_short or ""
                if len(result_display) > 120:
                    result_display = result_display[:117] + "..."
                result_label.configure(text=result_display)

        self.after(self.refresh_interval_ms, self._refresh)


# ---------------------------------------------------------------------
# 7. Main entrypoint
# ---------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="VillagerAgent multi-agent controller with GUI."
    )
    parser.add_argument(
        "--host",
        "-H",
        type=str,
        default="127.0.0.1",
        help="Minecraft server host used by VillagerBench.",
    )
    parser.add_argument(
        "--port",
        "-P",
        type=int,
        default=25565,
        help="Minecraft server port used by VillagerBench.",
    )
    parser.add_argument(
        "--env-type",
        type=str,
        default="none",
        choices=["none", "construction"],
        help="VillagerBench env_type to use (default: none).",
    )
    parser.add_argument(
        "--task-id",
        type=int,
        default=0,
        help="VillagerBench task_id (kept for compatibility; usually 0).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o-mini",
        help="OpenAI model name to use for Agent.model.",
    )

    args = parser.parse_args()

    # Configure LLM for BaseAgents
    api_key_list = load_api_keys()
    base_url = "https://api.openai.com/v1"
    model_name = args.model

    Agent.model = model_name
    Agent.base_url = base_url
    Agent.api_key_list = api_key_list

    if args.env_type == "none":
        env_t = env_type.none
    elif args.env_type == "construction":
        env_t = env_type.construction
    else:
        env_t = env_type.none

    # Bind VillagerBench to your Minecraft server; host/port should match minecraft_server.py / server
    env = VillagerBench(
        env_t,
        task_id=args.task_id,
        _virtual_debug=False,
        dig_needed=False,
        host=args.host,
        port=args.port,
    )

    # Tools: same as your original controller script
    agent_tool = [
        Agent.scanNearbyEntities,
        Agent.navigateTo,
        Agent.attackTarget,
        Agent.useItemOnEntity,
        Agent.MineBlock,
        Agent.placeBlock,
        Agent.equipItem,
        Agent.handoverBlock,
        Agent.SmeltingCooking,
        Agent.talkTo,
        Agent.waitForFeedback,
        Agent.withdrawItem,
        Agent.storeItem,
        Agent.craftBlock,
        Agent.ToggleAction,
        Agent.sleep,
        Agent.wake,
        Agent.tossItem,
        Agent.read,
        Agent.get_entity_info,
        Agent.get_environment_info,
        Agent.performMovement,
        Agent.lookAt,
        Agent.startFishing,
        Agent.stopFishing,
        Agent.mountEntity,
        Agent.dismountEntity,
    ]

    print("==============================================")
    print(" VillagerAgent Multi-Agent Controller + GUI")
    print("==============================================")
    print("Prereqs:")
    print(
        f"  1) Minecraft 1.19.2 server at {args.host}:{args.port} with online-mode=false."
    )
    print("  2) Node / Mineflayer bridge (minecraft_server.py) is running.")
    print("  3) Bot usernames will be auto-generated and should be /op'ed or configured")
    print("     so they can act freely on the server.")
    print("  4) API_KEY_LIST present with a valid OpenAI key.")
    print("----------------------------------------------")
    print(
        "Use 'Add New Player' to queue names,\n"
        "then 'Launch Agents' to register them and start fully parallel task loops."
    )
    print("Close the GUI window to stop all agents.")
    print("==============================================")

    username_gen = UsernameGenerator(BASE_USERNAME_STEMS)
    app = AgentGUI(env, agent_tool, TASK_LIBRARY, username_gen)
    app.mainloop()


if __name__ == "__main__":
    main()
