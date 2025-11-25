# gpt_live_controller3.py
#
# Live natural-language controller for Alice using the built-in VillagerAgent pipeline.
# This script:
#   - Patches OpenAI's chat completions so LangChain's old "encoding" kwarg doesn't crash.
#   - Patches requests.Response.json so non-JSON HTTP replies from the bridge don't blow up.
#   - Uses VillagerBench + DataManager + TaskManager + GlobalController + BaseAgent.
#   - Registers Alice with the full basic toolset.
#   - Lets you type tasks; Alice decomposes and executes them using the repo's logic.

import json
import time
import sys

# ---------------------------------------------------------------------
# 0. Patch OpenAI ChatCompletions to ignore 'encoding' kwarg
#    (fixes "Completions.create() got an unexpected keyword 'encoding'")
# ---------------------------------------------------------------------
try:
    # OpenAI 1.x chat completions class used by client.chat.completions
    from openai.resources.chat.completions import Completions as _ChatCompletions

    _orig_chat_create = _ChatCompletions.create

    def _patched_chat_create(self, *args, **kwargs):
        # Old langchain 0.0.350 passes an 'encoding' kwarg that no longer exists.
        kwargs.pop("encoding", None)
        return _orig_chat_create(self, *args, **kwargs)

    _ChatCompletions.create = _patched_chat_create
    print("[INFO] Patched openai.resources.chat.completions.Completions.create to ignore 'encoding' kwarg.")
except Exception as e:
    print("[WARN] Could not patch Completions.create:", e)

# Also patch legacy ChatCompletion.create for extra safety
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
# 1. Patch requests.Response.json to never raise JSONDecodeError
# ---------------------------------------------------------------------
import requests
from requests.models import Response as _Resp

_orig_response_json = _Resp.json


def _safe_response_json(self, *args, **kwargs):
    try:
        return _orig_response_json(self, *args, **kwargs)
    except Exception as e:
        text = self.text or ""
        print(f"[WARN] Response.json failed ({e}); returning stub. Raw (first 300 chars): {text[:300]!r}")
        # Generic stub: callers expect at least a dict with 'message' and 'status'.
        return {"message": text, "status": False, "new_events": []}


_Resp.json = _safe_response_json
print("[INFO] Patched requests.Response.json to return stub on JSON decode error.")

# ---------------------------------------------------------------------
# 2. Imports from this repo
# ---------------------------------------------------------------------
from env.env import VillagerBench, env_type, Agent
from pipeline.data_manager import DataManager
from pipeline.task_manager import TaskManager
from pipeline.controller import GlobalController


def load_api_keys():
    """
    Load an API key list from API_KEY_LIST in the repo root.
    Tries OPENAI first, then AGENT_KEY.
    """
    try:
        with open("API_KEY_LIST", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("[ERROR] API_KEY_LIST not found in current directory.")
        sys.exit(1)

    keys = data.get("OPENAI") or data.get("AGENT_KEY") or []
    if not keys:
        print("[ERROR] No keys found in API_KEY_LIST under 'OPENAI' or 'AGENT_KEY'.")
        sys.exit(1)
    return keys


if __name__ == "__main__":
    # -----------------------------------------------------------------
    # 3. Configure LLM for the built-in VillagerAgent pipeline
    # -----------------------------------------------------------------
    api_key_list = load_api_keys()

    # Use official OpenAI endpoint & model; change model_name if you prefer
    base_url = "https://api.openai.com/v1"
    model_name = "gpt-4o-mini"  # e.g. "gpt-4o" or "gpt-4-1106-preview"

    # This llm_config is what GlobalController -> init_language_model uses
    llm_config = {
        "api_key": api_key_list[0],
        "api_model": model_name,
        "api_base": base_url,
        "api_key_list": api_key_list,
    }

    # These Agent class attributes are what the env / BaseAgent code uses internally
    Agent.model = model_name
    Agent.base_url = base_url
    Agent.api_key_list = api_key_list

    # -----------------------------------------------------------------
    # 4. Create environment bound to YOUR Minecraft server
    # -----------------------------------------------------------------
    # Your Minecraft 1.19.2 server is at localhost:25565 (with online-mode=false)
    env = VillagerBench(
        env_type.none,
        task_id=0,
        _virtual_debug=False,
        dig_needed=False,
        host="127.0.0.1",
        port=25565,
    )

    # -----------------------------------------------------------------
    # 5. Register Alice with the same "basic_tools" set used in auto_gen_gpt_task.py
    #    (these are the tools the LLM can freely choose from)
    # -----------------------------------------------------------------
    # From pipeline/agent.py and auto_gen_gpt_task.py basic_tools
    agent_tool = [
        Agent.scanNearbyEntities, Agent.navigateTo, Agent.attackTarget,
        Agent.useItemOnEntity,
        Agent.MineBlock, Agent.placeBlock, Agent.equipItem,
        Agent.handoverBlock, Agent.SmeltingCooking, Agent.talkTo, Agent.waitForFeedback,
        Agent.withdrawItem, Agent.storeItem, Agent.craftBlock, Agent.ToggleAction,
        Agent.sleep, Agent.wake, Agent.tossItem, Agent.read,
        Agent.get_entity_info, Agent.get_environment_info, Agent.performMovement,
        Agent.lookAt, Agent.startFishing, Agent.stopFishing, Agent.mountEntity, Agent.dismountEntity
    ]

    env.agent_register(agent_tool=agent_tool, agent_number=1, name_list=["Alice"])

    # -----------------------------------------------------------------
    # 6. Run the environment and a natural-language REPL over GlobalController
    # -----------------------------------------------------------------
    # env.run() will spawn env/minecraft_server.py (your Mineflayer bot bridge)
    with env.run():
        print("==============================================")
        print(" Alice Live Controller (VillagerAgent pipeline, robust HTTP/JSON)")
        print("==============================================")
        print("Prereqs:")
        print("  1) Minecraft 1.19.2 server at 127.0.0.1:25565 with online-mode=false.")
        print("  2) /op Alice in-game so she can use commands if needed.")
        print("  3) API_KEY_LIST present with an OpenAI key.")
        print("----------------------------------------------")
        print("Type natural-language tasks for Alice.")
        print("Examples:")
        print('  Cut down a tree and craft some planks.')
        print('  Gather seeds and plant a small wheat farm near me.')
        print('  Build a small dirt tower next to me.')
        print('  Talk to Tigerish and say hi.')
        print("Type 'quit' or 'exit' to stop.")
        print("==============================================")

        while True:
            try:
                user_task = input("\nTask for Alice> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[INFO] Exiting.")
                break

            if not user_task:
                continue
            if user_task.lower() in ("quit", "exit"):
                print("[INFO] Exiting.")
                break

            print(f"[INFO] New task: {user_task!r}")

            # For each task, reset DM/TM so tasks are independent.
            dm = DataManager(silent=False)
            try:
                init_state = env.get_init_state()
            except Exception as e:
                print(f"[WARN] env.get_init_state() failed: {e}")
                init_state = []
            dm.update_database_init(init_state)

            tm = TaskManager(silent=False)

            # GlobalController is the main brain: decomposes tasks, assigns subtasks to agents,
            # and uses BaseAgent + the tools we registered.
            ctrl = GlobalController(llm_config, tm, dm, env)

            # High-level task description; second arg is optional task doc (we pass empty dict).
            tm.init_task(user_task, {})

            start_t = time.time()
            ctrl.run()  # Runs the full planning/execution loop for this task
            dt = time.time() - start_t

            print(f"[INFO] Controller finished in {dt:.2f} seconds for this task.")
            # If you want, you can inspect env.get_score() or dm logs here.
