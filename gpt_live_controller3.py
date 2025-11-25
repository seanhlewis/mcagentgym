# gpt_live_controller4.py
#
# Live natural-language controller for Alice using the built-in VillagerAgent pipeline.
# - Uses VillagerBench + DataManager + TaskManager + GlobalController + BaseAgent.
# - Patches OpenAI 1.x client for old LangChain 'encoding' kwarg.
# - Patches requests.Response.json to avoid crashing on non-JSON bridge responses.
# - Registers Alice with the full basic toolset from the repo (mine, build, talk, etc).
# - Single-task mode by default: after Alice completes the main task (success OR failure),
#   GlobalController stops instead of generating its own follow-up tasks.
#   You can toggle this with ALLOW_FOLLOWUP below.

import json
import time
import sys

# ---------------------------------------------------------------------
# 0. Toggle: allow or disable automatic follow-up/meta tasks
# ---------------------------------------------------------------------
ALLOW_FOLLOWUP = False  # set to True if you *want* the extra "improve environment" tasks


# ---------------------------------------------------------------------
# 1. Patch OpenAI ChatCompletions to ignore 'encoding' kwarg
#    (fixes "Completions.create() got an unexpected argument 'encoding'")
# ---------------------------------------------------------------------
try:
    from openai.resources.chat.completions import Combinations as _MaybeWrong
except Exception:
    pass  # just to avoid NameError if this import path doesn't exist

try:
    # OpenAI 1.x chat completions class used by client.chat.completions
    from openai import OpenAI
    from openai.resources.chat.completions import Completions as _ChatCompletions

    _orig_chat_create = _ChatCompletions.create

    def _patched_chat_create(self, *args, **kwargs):
        # Old langchain 0.0.350 passes an 'encoding' kwarg that no longer exists.
        if "encoding" in kwargs:
            kwargs.pop("encoding", None)
        return _orig_chat_create(self, *args, **kwargs)

    _ChatCompart = _ChatCompleations = None  # ignore if missing
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
            if "encoding" in kwargs:
                kwargs.pop("encoding", None)
            return _orig_legacy_create(*args, **kwargs)

        _openai_mod.ChatCompletion = type(
            "ChatCompletionPatched",
            (_openai_mod.ChatCompletion.__class__,),
            dict(_openai_mod.ChatCompletion.__dict__, create=staticmethod(_patched_legacy_create)),
        )
        print("[INFO] Patched openai.ChatCompletion.create to ignore 'encoding' kwarg.")
except Exception as e:
    print("[WARN] Could not patch openai.ChatCompletion.create:", e)


# ---------------------------------------------------------------------
# 2. Patch requests.Response.json to never crash on bad JSON
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
        # Return a minimal stub that fits what env/minecraft_client expects
        return {"message": text, "status": False, "new_events": []}

_Resp.json = _safe_response_json
print("[INFO] Patched requests.Response.json to return stub on JSON decode error.")


# ---------------------------------------------------------------------
# 3. Imports from this repo
# ---------------------------------------------------------------------
from env.env import VillagerBench, env_type, Agent
from pipeline.data_manager import DataManager
from pipeline.task_manager import TaskManager
from pipeline.controller import GlobalController


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
    # 4. Configure LLM for the built-in VillagerAgent pipeline
    # -----------------------------------------------------------------
    api_key_list = load_api_keys()

    # Use official OpenAI endpoint & model; you can change this to "gpt-4o" etc.
    base_url = "https://api.openai.com/v1"
    model_name = "gpt-4o-mini"  # or "gpt-4o" if you have access

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
    # 5. Create environment bound to YOUR Minecraft server
    # -----------------------------------------------------------------
    # Your Minecraft server is at localhost:25565 (with online-mode=false)
    env = VillagerBench(
        env_type.none,
        task_id=0,
        _virtual_debug=False,
        dig_needed=False,
        host="127.0.0.1",
        port=25565,
    )

    # -----------------------------------------------------------------
    # 6. Register Alice with the "basic_tools" from auto_gen_gpt_task/pipeline.agent
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
    # 7. Run the environment and a natural-language REPL over GlobalController
    # -----------------------------------------------------------------
    # env.run() will spawn env/minecraft_server.py (your Mineflayer bot bridge)
    with env.run():
        print("==============================================")
        print(" Alice Live Controller (VillagerAgent pipeline, single-task mode)")
        print("==============================================")
        print("Prereqs:")
        print("  1) Minecraft 1.19.2 server at 127.0.0.1:25565 with online-mode=false.")
        print("  2) /op Alice in-game so she can use commands on your server.")
        print("  3) API_KEY_LIST present with an OpenAI key.")
        print("----------------------------------------")
        print("Type natural-language tasks for Alice.")
        print("Examples:")
        print('  Cut down a tree and craft some planks.')
        print('  Gather seeds and plant a small wheat patch near me.')
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

            # **** KEY: limit follow-up tasks if desired ****
            if hasattr(ctrl, "set_stop_condition"):
                # Stop after 1 success (the main task), and 0 extra failures.
                if not ALLOW_FOLLOWUP:
                    ctrl.set_stop_condition(
                        max_execution_time=600,  # seconds
                        stop_after_fail_times=0,
                        stop_after_success_times=3,
                    )

            # High-level task description; second arg is optional doc (we pass empty dict).
            tm.init_task(user_task, {})

            start_t = time.time()
            ctrl.run()  # Runs the full planning/execution loop for this task
            dt = time.time() - start_t

            print(f"[INFO] Controller finished in {dt:.2f} seconds.")
            print("[INFO] You can now enter another task, or type 'exit' to quit.")
