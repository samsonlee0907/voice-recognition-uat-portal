"""Build a portable UAT page using synthetic cases only."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def safe_json(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build():
    bank = json.loads((ROOT / "config" / "telecom_uat_cases.json").read_text(encoding="utf-8"))
    if any(item["refs"] for item in bank["cases"]):
        raise ValueError("The standalone question bank must not reference private source logs")
    markup = (ROOT / "static" / "uat-template.html").read_text(encoding="utf-8")
    substitutions = {
        "__WORKBENCH__": (ROOT / "static" / "uat-workbench.html").read_text(encoding="utf-8"),
        "__BANK__": safe_json(bank),
        "__REVIEW__": safe_json({"schema": 1, "bankVersion": bank["version"], "savedAt": "", "runs": []}),
        "__APP__": (ROOT / "static" / "uat-app.js").read_text(encoding="utf-8"),
    }
    for token, value in substitutions.items():
        if markup.count(token) != 1:
            raise ValueError(f"Expected exactly one {token} placeholder")
        markup = markup.replace(token, value)
    (ROOT / "static" / "telecom-uat-report.html").write_text(markup, encoding="utf-8")
    print(f"Built portable synthetic UAT page: {len(bank['cases'])} cases")


if __name__ == "__main__":
    build()
