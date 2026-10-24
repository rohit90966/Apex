from pathlib import Path

p = Path("app/knowledge/retriever_v2.py")
content = p.read_text(encoding="utf-8")

old_code = '''        for pat in all_patterns:
            score = 0.0
            reasons: List[str] = []

            # 1. Archetype Match
            if primary_arch in pat.archetypes:'''

new_code = '''        for pat in all_patterns:
            score = 0.0
            reasons: List[str] = []

            # Filter out completely incompatible archetypes (e.g. Booking or Banking patterns on E-Commerce)
            is_compat = (
                primary_arch in pat.archetypes
                or any(sa in pat.archetypes for sa in secondary_archs)
                or WebsiteArchetype.GENERIC_WEB in pat.archetypes
                or WebsiteArchetype.AUTHENTICATION in pat.archetypes
            )
            if not is_compat:
                continue

            # 1. Archetype Match
            if primary_arch in pat.archetypes:'''

if old_code in content:
    content = content.replace(old_code, new_code, 1)
    p.write_text(content, encoding="utf-8")
    print("Updated retriever_v2.py successfully!")
else:
    print("Old code not found in retriever_v2.py")
