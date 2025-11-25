# alice_direct_repl.py
#
# Live natural-language controller for Alice using the built-in BaseAgent pipeline.
# This script:
#   - Patches OpenAI 1.x so LangChain's old 'encoding' kwarg doesn't crash.
#   - Patches requests.Response.json so non-JSON HTTP replies from the bridge don't blow up.
#   - Uses VillagerBench + env.step("Alice", instruction) just like agent_demo.py.
#   - Registers Alice with a rich toolset from doc/api_library.md / auto_gen_gpt_task.
#   - No TaskManager, no GlobalController, no meta-follow-up tasks: each instruction is one self-contained attempt.

import json
import sys
import time

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
    print("[INFO] Patched openai.resources.chat.completions.Completions.create to ignore 'encoding' kwarg.")
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
        print(f"[WARN] Response.json failed ({e}); returning stub. Raw (first 200 chars): {text[:200]!r}")
        return {"message": text, "status": False, "new_events": []}

_Resp.json = _safe_response_json
print("[INFO] Patched requests.Response.json to return stub on JSON decode error.")

# ---------------------------------------------------------------------
# 2. Imports from this repo
# ---------------------------------------------------------------------
from env.env import VillagerBench, env_type, Agent
from pipeline.data_manager import DataManager

def load_api_keys():
    """
    Load an API key from API_KEY_LIST in the current working directory.
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
    # 3. Configure LLM for BaseAgent pipeline
    # -----------------------------------------------------------------
    api_key_list = load_api_keys()

    # Use official OpenAI endpoint & model; change model_name if you prefer
    base_url = "https://api.openai.com/v1"
    model_name = "gpt-4o-mini"  # e.g. "gpt-4o" or "gpt-4-1106-preview"

    # These Agent class attributes are what env / BaseAgent use internally
    Agent.model = model_name
    Agent.base_url = base_url
    Agent.api_key_list = api_key_list

    # -----------------------------------------------------------------
    # 4. Create environment bound to YOUR Minecraft server
    # -----------------------------------------------------------------
    env = VillagerBench(
        env_type.none,
        task_id=0,
        _virtual_debug=False,
        dig_needed=False,
        host="127.0.0.1",
        port=25565,
    )

    # -----------------------------------------------------------------
    # 5. Register Alice with a rich toolset (from doc/api_library.md)
    # -----------------------------------------------------------------
    agent_tool = [
        Agent.scanNearbyEntities,  # find blocks/entities
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

    env.agent_register(agent_tool=agent_tool, agent_number=1, name_list=["Alice"])

    # -----------------------------------------------------------------
    # 6. Run env + DataManager + direct env.step() REPL
    # -----------------------------------------------------------------
    with env.run(fast_api=False):
        # Initialize DataManager once (so internal state history works)
        dm = DataManager(silent=False)
        try:
            init_state = env.get_init_state()
        except Exception as e:
            print(f"[WARN] env.get_init_state() failed: {e}")
            init_state = []
        dm.update_database_init(init_state)

        print("==============================================")
        print(" Alice Direct REPL (BaseAgent pipeline via env.step)")
        print("==============================================")
        print("Prereqs:")
        print("  1) Minecraft 1.19.2 server at 127.0.0.1:25565 with online-mode=false.")
        print("  2) /op Alice in-game so she can use commands on your server.")
        print("  3) API_KEY_LIST present with an OpenAI key.")
        print("----------------------------------------")
        print("Each line you type is given directly to Alice via env.step('Alice', ...).")
        print("No TaskManager, no GlobalController, no meta-follow-up.")
        print("Examples:")
        print('  Cut down a tree and craft some planks.')
        print('  Gather seeds and plant a small wheat patch near me.')
        print('  Talk to Tigerish and say hi.')
        print("Type 'quit' or 'exit' to stop.")
        print("==============================================")

        while True:
            try:
                user_task = input("\nInstruction for Alice> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[INFO] Exiting.")
                break

            if not user_task:
                continue
            if user_task.lower() in ("quit", "exit"):
                print("[INFO] Exiting.")
                break

            print(f"[INFO] Sending instruction to Alice: {user_task!r}")
            start_t = time.time()

            # This is the key: use the repo's built-in env.step, like agent_demo.py.
            try:
                feedback, detail = env.step("Alice", user_task)
            except Exception as e:
                print(f"[ERROR] env.step failed: {e}")
                continue

            dt = time.time() - start_t

            print(f"[RESULT] Feedback: {feedback}")
            print(f"[RESULT] Detail keys: {list(detail.keys())}")
            print(f"[INFO] env.step took {dt:.2f} seconds.")
            print("[INFO] You can now enter another instruction, or type 'exit' to quit.")
