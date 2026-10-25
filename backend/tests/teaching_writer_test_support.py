"""Adapt legacy writer doubles to the advertised component-patch wire shape.

This only projects their authored response. It does not merge production drafts,
approve semantics, fill missing components or repair malformed identities.
"""

from copy import deepcopy


def advertised_writer_response(result, payload):
    contract = payload.get("repair_contract")
    if not contract or not isinstance(result, dict) or not isinstance(result.get("points"), list):
        return result
    result = deepcopy(result)
    fields = {"point_id", "title", "explanation", "source_ids", "flashcard", "practice"}
    rows = []
    for point in result["points"]:
        names = contract["point_components"].get(point.get("point_id"))
        if names is None or isinstance(point.get("explanation"), dict):
            rows.append(point)
            continue
        row = {key: value for key, value in point.items() if key not in fields}
        row["point_id"] = point.get("point_id")
        for name in names:
            if name == "explanation":
                row[name] = {key: point[key] for key in ("title", "explanation", "source_ids")
                             if key in point}
            elif name in point:
                row[name] = point[name]
        rows.append(row)
    result["points"] = rows
    lesson = result.get("lesson")
    if isinstance(lesson, dict) and "worked_examples" in lesson:
        patch = {}
        for name in contract["lesson_components"]:
            if name in {"opening", "summary", "reading_source_ids"}:
                if name in lesson:
                    patch[name] = lesson[name]
            else:
                prefix, slot = name.rsplit("_", 1)
                field = "worked_examples" if prefix == "example" else "checkpoints"
                if int(slot) <= len(lesson.get(field, [])):
                    patch[name] = lesson[field][int(slot) - 1]
        result["lesson"] = patch
    return result
