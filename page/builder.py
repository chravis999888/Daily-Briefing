import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

AEST = timezone(timedelta(hours=10))

ACCENTS = {
    "breaking": "#c0392b",
    "australia": "#2e7bbf",
    "archaeology": "#b07d2a",
    "football": "#2a7a52",
    "world": "#7b68c8",
    "developing": "#2a7a6e"
}


def build_html(all_data, yesterday_data, world_topics, developing_situations, health=None):
    date_str = datetime.now(AEST).strftime("%A %d %B %Y").upper()
    updated_str = datetime.now(AEST).strftime("%I:%M %p AEST").lstrip("0")
    build_ts = int(datetime.now(timezone.utc).timestamp())

    last_run = health["runs"][-1] if health and health.get("runs") else None
    if last_run:
        dot_color = "#e67e22" if last_run.get("errors") else "#2ecc71"
        if last_run.get("errors"):
            tooltip_text = "Issues: " + "; ".join(last_run["errors"][:3])
        else:
            tooltip_text = "All sources OK"
        health_dot = (
            f'<span class="health-dot-wrap" style="position:relative;display:inline-block;vertical-align:middle;margin-right:6px;">'
            f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{dot_color};cursor:default;"></span>'
            f'<span class="health-tooltip" style="display:none;position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);'
            f'background:#1c1c1a;border:1px solid rgba(255,255,255,0.1);border-radius:6px;padding:6px 10px;white-space:nowrap;'
            f'font-size:11px;font-family:Inter,sans-serif;color:#c8c4bc;pointer-events:none;z-index:100;opacity:0;transition:opacity 0.2s;">'
            f'{tooltip_text}</span></span>'
        )
    else:
        health_dot = ""

    col_categories = [
        {
            "id": "australia",
            "label": "Australia",
            "ac": ACCENTS["australia"],
            "data": all_data.get("australia", []),
            "yesterday": yesterday_data.get("australia", []),
        },
        {
            "id": "archaeology",
            "label": "Archaeology & Palaeoanthropology",
            "ac": ACCENTS["archaeology"],
            "data": all_data.get("archaeology", []),
            "yesterday": yesterday_data.get("archaeology", []),
        },
        {
            "id": "football",
            "label": "Football",
            "ac": ACCENTS["football"],
            "data": all_data.get("football", []),
            "yesterday": yesterday_data.get("football", []),
        },
    ]

    env = Environment(loader=FileSystemLoader(Path(__file__).parent), autoescape=False)
    env.filters["urlencode_component"] = lambda s: urllib.parse.quote(str(s), safe="")

    template = env.get_template("template.html")
    return template.render(
        date_str=date_str,
        updated_str=updated_str,
        build_ts=build_ts,
        health_dot=health_dot,
        breaking=all_data.get("breaking", []),
        yesterday_breaking=yesterday_data.get("breaking", []),
        col_categories=col_categories,
        world_topics=world_topics,
        developing_situations=developing_situations,
        accents=ACCENTS,
    )
