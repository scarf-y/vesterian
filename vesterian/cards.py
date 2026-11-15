"""Compact overviews; full source details remain accessible in their own view."""

from .core import clean, display


def compact(result, kind, data):
    fields = []

    def add(name, value):
        if value is not None and value != [] and value != "":
            fields.append((name, display(value), True))

    if kind == "item":
        m = data.get("metadata", {})
        for label, key in [
            ("Required level", "requiredLevel"),
            ("Attack", "baseDamage"),
            ("Defense", "defense"),
            ("Upgrade slots", "maxUpgrades"),
            ("Sell value", "sellValue"),
        ]:
            add(label, m.get(key))
        add("Class", data.get("requiredClasses"))
        add("Binding", "Soulbound" if data.get("soulbound") else None)
        lines = [display(data["description"])] if data.get("description") else []
        if data.get("behaviorSummary"):
            lines.append(display(data["behaviorSummary"]))
        lines.append("Use **Sources**, **Crafting**, or **Details** for more information.")
    elif kind == "monster":
        for stat in data.get("bookStats", {}).get("stats", [])[:9]:
            add(stat.get("label", "Stat"), stat.get("value"))
        lines = [display(data["description"])] if data.get("description") else []
        lines.append("Explore **Drops**, **Locations**, or **Details** below.")
    elif kind == "ability":
        for stat in (data.get("stats") or {}).get("stats", [])[:9]:
            add(stat.get("label", "Stat"), stat.get("value"))
        lines = [display(data[k]) for k in ("description", "requirementText", "behaviorNotes") if data.get(k)]
        lines.append("Choose **Ranks** for progression or **Details** for interactions and differences.")
    else:
        for label, key in [
            ("Monsters", "monsters"),
            ("NPCs", "characters"),
            ("Shops", "shops"),
            ("Exits", "portals"),
        ]:
            add(label, data.get("counts", {}).get(key))
        lines = [" · ".join(clean(t) for t in data.get("tags", []))] if data.get("tags") else []
        if data.get("calibrated") is False:
            lines.append("Map positions are not calibrated; use the lists below.")
        lines.append("Explore **Monsters**, **NPCs**, and **Exits** below.")
    result.lines, result.fields = lines, fields
    return result
