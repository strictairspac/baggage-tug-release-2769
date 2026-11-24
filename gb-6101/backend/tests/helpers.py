"""release_sql.release_decisions 的 Python 等价口径（测试双）。

与 backend/src/domain/release_sql.py 的 SQL 口径保持一致：
按检测分钟、证据序号降序取每项在复核分钟时点的最新一条；
通过且失效分钟严格大于复核分钟才有效；无必检配置则不放行。
"""

from src.domain import scenario


def python_release_decisions(payload, minute):
    vehicles = {item["asset"]: item["category"] for item in payload["vehicles"]}
    requirements = {}
    for item in payload["requirements"]:
        requirements.setdefault(item["category"], set()).add(item["check"])
    latest = {}
    for evidence in sorted(payload["evidence"], key=lambda item: (item["checked"], item["seq"]), reverse=True):
        if evidence["checked"] > minute:
            continue
        slot = (evidence["asset"], evidence["check"])
        if slot in latest:
            continue
        latest[slot] = evidence
    rows = []
    for asset, category in sorted(vehicles.items()):
        missing = []
        for check in sorted(requirements.get(category, ())):
            evidence = latest.get((asset, check))
            if evidence is None:
                reason = "无当时回执"
            elif evidence["status"] == "fail":
                reason = "末次检测未通过"
            elif evidence["expires"] <= minute:
                reason = "证据已失效"
            else:
                continue
            missing.append((check, reason))
        has_rules = bool(requirements.get(category))
        released = has_rules and not missing
        rows.append({
            "asset": asset,
            "category": category,
            "released": released,
            "missing_checks": " / ".join(item[0] for item in missing) or None,
            "diagnostic": " / ".join(f"{check}（{reason}）" for check, reason in missing) or None,
        })
    return rows


def patched_scenario(monkeypatch):
    monkeypatch.setattr(
        scenario, "release_decisions",
        lambda db, payload, minute: python_release_decisions(payload, minute),
    )


REGULAR_PAYLOAD = {
    "vehicles": [
        {"asset": "YP-T12", "category": "电动"},
        {"asset": "YP-T13", "category": "电动"},
        {"asset": "YP-D22", "category": "柴油"},
    ],
    "requirements": [
        {"category": "电动", "check": "制动"},
        {"category": "电动", "check": "牵引销"},
        {"category": "柴油", "check": "制动"},
    ],
    "evidence": [
        {"seq": 1, "asset": "YP-T12", "check": "制动", "checked": 450, "expires": 570, "status": "pass"},
        {"seq": 2, "asset": "YP-T12", "check": "牵引销", "checked": 460, "expires": 550, "status": "pass"},
        {"seq": 3, "asset": "YP-T13", "check": "制动", "checked": 450, "expires": 550, "status": "pass"},
        {"seq": 5, "asset": "YP-T13", "check": "牵引销", "checked": 481, "expires": 570, "status": "pass"},
        {"seq": 6, "asset": "YP-D22", "check": "制动", "checked": 410, "expires": 480, "status": "pass"},
    ],
}


def make_case(client, token, payload=None):
    response = client.post("/cases", headers=token, json={
        "title": "补登流程测试资料",
        "description": "integration",
        "payload": payload or REGULAR_PAYLOAD,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"], response.json()["payload"]


def run_to_done(client, token, case_id, parameter=480, monkeypatch=None):
    if monkeypatch is not None:
        patched_scenario(monkeypatch)
    response = client.post(f"/cases/{case_id}/runs", headers=token, json={"parameter": parameter})
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    from src.services.execution import service_one

    assert service_one() is True
    detail = client.get(f"/runs/{run_id}", headers=token).json()
    assert detail["status"] == "done", detail
    return detail
