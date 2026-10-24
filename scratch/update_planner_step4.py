from pathlib import Path

p = Path("app/test_planning/planner.py")
content = p.read_text(encoding="utf-8")

old_code = '''                for step in st.steps:
                    resolved_selector = self._resolve_selector(step.target_selector_hints, controls)
                    act_str = str(step.action_type or "CLICK").upper()
                    act_type = ActionType[act_str] if act_str in ActionType.__members__ else ActionType.CLICK'''

new_code = '''                for step in st.steps:
                    resolved_selector = self._resolve_selector(step.target_selector_hints, controls)
                    act_str = str(step.action_type or "CLICK").upper()
                    if act_str in ("SELECT", "SELECT_OPTION"):
                        act_type = ActionType.SELECT
                    elif act_str in ("FILL", "INPUT"):
                        act_type = ActionType.FILL
                    elif act_str in ("NAVIGATE", "GOTO"):
                        act_type = ActionType.NAVIGATE
                    elif act_str in ActionType.__members__:
                        act_type = ActionType[act_str]
                    else:
                        act_type = ActionType.CLICK'''

if old_code in content:
    content = content.replace(old_code, new_code, 1)
    print("Replaced act_type resolution in planner.py")

old_nav_insert = '''                # Ensure initial entry navigation for authentication patterns
                if action_steps and not any(s.action == ActionType.NAVIGATE for s in action_steps):'''

new_nav_insert = '''                # Ensure initial entry navigation for unauthenticated patterns (e.g. login boundaries)
                if not needs_auth and action_steps and not any(s.action == ActionType.NAVIGATE for s in action_steps):'''

if old_nav_insert in content:
    content = content.replace(old_nav_insert, new_nav_insert, 1)
    print("Fixed initial navigation insertion for patterns")

p.write_text(content, encoding="utf-8")
print("Done step 4.")
