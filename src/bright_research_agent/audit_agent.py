import argparse
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agents import Agent, AsyncOpenAI, RunConfig, Runner, set_default_openai_client
from agents.model_settings import ModelSettings
from dotenv import load_dotenv
from openai import APITimeoutError

from bright_research_agent.brightdata import (
    brightdata_serp_search,
    brightdata_unlock_url,
    serp_search_api,
    unlock_url_api,
)
from bright_research_agent.audit_schemas import AuditReport


logger = logging.getLogger(__name__)

INSTRUCTIONS = (Path(__file__).parent / "audit_instructions.txt").read_text()


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().lstrip("www.")


def build_agent() -> Agent:
    return Agent(
        name="Google Ads Competitive Audit Agent",
        instructions=INSTRUCTIONS,
        tools=[brightdata_serp_search, brightdata_unlock_url],
        model=os.getenv("OPENAI_MODEL", "gpt-4.1"),
        model_settings=ModelSettings(
            tool_choice="auto",
            max_tokens=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "4000")),
        ),
        output_type=AuditReport,
    )


def configure_openai_client(timeout_seconds: float, max_retries: int) -> None:
    logger.info(
        "Configuring OpenAI client: timeout_seconds=%s max_retries=%s",
        timeout_seconds,
        max_retries,
    )
    client = AsyncOpenAI(timeout=timeout_seconds, max_retries=max_retries)
    set_default_openai_client(client)


async def collect_audit_evidence(
    keyword: str,
    client_url: str,
    max_competitors: int = 3,
) -> dict[str, Any]:
    logger.info(
        "Collecting audit evidence: keyword=%r client_url=%s", keyword, client_url
    )

    # SERP uses only the keyword; client page fetched directly and concurrently
    search, client_page = await asyncio.gather(
        serp_search_api(keyword, max_results=max_competitors + 3),
        unlock_url_api(client_url, max_chars=8000),
        return_exceptions=True,
    )

    if isinstance(search, Exception):
        logger.error("SERP search failed: %s", search)
        search = {"results": [], "result_count": 0}

    if isinstance(client_page, Exception):
        logger.warning("Client page fetch failed: %s", client_page)
        client_page = {"url": client_url, "error": str(client_page)}

    # Filter client domain out of SERP results before selecting competitor URLs
    client_domain = _domain(client_url)
    competitor_urls = [
        r["url"]
        for r in search.get("results", [])
        if r.get("url") and client_domain not in _domain(r["url"])
    ][:max_competitors]

    logger.info(
        "Competitor URLs selected: count=%s urls=%s", len(competitor_urls), competitor_urls
    )

    competitor_pages_raw = await asyncio.gather(
        *(unlock_url_api(url, max_chars=8000) for url in competitor_urls),
        return_exceptions=True,
    )

    competitor_pages = []
    for url, page in zip(competitor_urls, competitor_pages_raw):
        if isinstance(page, Exception):
            logger.warning("Competitor page fetch failed: url=%s error=%s", url, page)
            competitor_pages.append({"label": "COMPETITOR SITE", "url": url, "error": str(page)})
        else:
            logger.info("Competitor page fetched: url=%s chars=%s", url, len(page.get("content", "")))
            competitor_pages.append({"label": "COMPETITOR SITE", **page})

    client_page_labeled = {"label": "CLIENT SITE", **client_page}

    logger.info(
        "Evidence ready: serp_results=%s client_ok=%s competitors=%s",
        len(search.get("results", [])),
        "error" not in client_page,
        len(competitor_pages),
    )

    return {
        "keyword": keyword,
        "client_url": client_url,
        "serp_results": search.get("results", []),
        "client_site": client_page_labeled,
        "competitor_sites": competitor_pages,
    }


async def run_audit(
    keyword: str,
    business: str,
    client_url: str,
    max_turns: int,
) -> AuditReport:
    logger.info("Starting audit: keyword=%r client_url=%s", keyword, client_url)
    evidence = await collect_audit_evidence(keyword, client_url)
    agent = build_agent()
    prompt = (
        f"Keyword: {keyword}\n"
        f"Business: {business}\n"
        f"Client URL: {client_url}\n\n"
        "Produce a Google Ads competitive audit using the evidence below. "
        "The client site is labeled CLIENT SITE and competitor pages are labeled COMPETITOR SITE. "
        "Call the Bright Data tools if you need additional pages.\n\n"
        f"Evidence:\n{json.dumps(evidence, indent=2)}"
    )
    logger.info("Handing evidence to agent for audit synthesis")
    result = await Runner.run(
        agent,
        prompt,
        max_turns=max_turns,
        run_config=RunConfig(tracing_disabled=True),
    )
    report = result.final_output
    logger.info(
        "Audit report generated: recommendations=%s quick_wins=%s open_questions=%s",
        len(report.google_ads_recommendations),
        len(report.quick_wins),
        len(report.open_questions),
    )
    return report


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        raise SystemExit(
            f"Invalid log level {level_name!r}. Use DEBUG, INFO, WARNING, ERROR, or CRITICAL."
        )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Google Ads competitive audit agent.")
    parser.add_argument("--keyword", required=True, help='Target keyword (e.g. "Lake Erie fishing charters")')
    parser.add_argument("--business", required=True, help='Short business description (e.g. "Walleye charter out of Lorain, Ohio")')
    parser.add_argument("--client-url", required=True, help="Client website URL")
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging verbosity.",
    )
    parser.add_argument(
        "--openai-timeout",
        type=float,
        default=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "240")),
        help="OpenAI API request timeout in seconds.",
    )
    parser.add_argument(
        "--openai-max-retries",
        type=int,
        default=int(os.getenv("OPENAI_MAX_RETRIES", "3")),
        help="OpenAI API retry count.",
    )
    return parser.parse_args()


def render_html(report: AuditReport, keyword: str, business: str, client_url: str, json_data: str = "") -> str:
    priority_colors = {"high": "#c0392b", "medium": "#e67e22", "low": "#27ae60"}

    def badge(priority: str) -> str:
        color = priority_colors.get(priority.lower(), "#888")
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:3px;font-size:0.8em;font-weight:bold;text-transform:uppercase">{priority}</span>'

    def section(title: str, content: str) -> str:
        return f'<section><h2>{title}</h2>{content}</section>'

    def ul(items: list[str]) -> str:
        return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"

    # Competitor comparison
    comp_html = ul(report.competitor_comparison)

    # Google Ads recommendations
    recs_html = ""
    for r in report.google_ads_recommendations:
        recs_html += (
            f'<div class="rec">'
            f'<div class="rec-header">{badge(r.priority)} <span class="category">{r.category}</span></div>'
            f'<p class="rec-text">{r.recommendation}</p>'
            f'<p class="reasoning">{r.reasoning}</p>'
            f'</div>'
        )

    # GEO recommendations
    geo_html = ul(report.geo_recommendations)

    # Quick wins
    wins_html = "<ol>" + "".join(f"<li>{w}</li>" for w in report.quick_wins) + "</ol>"

    # Open questions
    oq_html = ul(report.open_questions)

    # Sources
    sources_html = "<ul>"
    for s in report.sources_consulted:
        sources_html += f'<li><a href="{s.url}" target="_blank">{s.title or s.url}</a></li>'
    sources_html += "</ul>"

    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Google Ads Audit — {keyword}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 860px; margin: 40px auto; padding: 0 24px; color: #222; line-height: 1.6; }}
  h1 {{ font-size: 1.6em; margin-bottom: 4px; }}
  .meta {{ color: #666; font-size: 0.9em; margin-bottom: 32px; }}
  .meta span {{ margin-right: 20px; }}
  h2 {{ font-size: 1.15em; text-transform: uppercase; letter-spacing: 0.05em; color: #444; border-bottom: 2px solid #eee; padding-bottom: 6px; margin-top: 40px; }}
  .summary {{ background: #f8f9fa; border-left: 4px solid #3498db; padding: 16px 20px; border-radius: 4px; margin-bottom: 8px; }}
  ul, ol {{ padding-left: 20px; }} li {{ margin-bottom: 8px; }}
  .rec {{ border: 1px solid #e0e0e0; border-radius: 6px; padding: 14px 18px; margin-bottom: 12px; }}
  .rec-header {{ margin-bottom: 8px; }}
  .category {{ font-weight: 600; font-size: 0.85em; color: #555; margin-left: 8px; text-transform: uppercase; }}
  .rec-text {{ margin: 6px 0 4px; font-weight: 500; }}
  .reasoning {{ margin: 0; color: #555; font-size: 0.92em; }}
  .quick-wins {{ background: #eafaf1; border-left: 4px solid #27ae60; padding: 16px 20px; border-radius: 4px; }}
  .quick-wins ol {{ margin: 0; }}
  a {{ color: #2980b9; }}
  footer {{ margin-top: 48px; padding-top: 16px; border-top: 1px solid #eee; color: #999; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>Google Ads Competitive Audit</h1>
<div class="meta">
  <span><strong>Keyword:</strong> {keyword}</span>
  <span><strong>Business:</strong> {business}</span>
  <span><strong>Client:</strong> <a href="{client_url}">{client_url}</a></span>
  <span><strong>Generated:</strong> {timestamp}</span>
</div>

{section("Summary", f'<div class="summary">{report.summary}</div>')}
{section("Competitor Comparison", comp_html)}
{section("Google Ads Recommendations", recs_html)}
{section("GEO / AI Search Visibility", geo_html)}
{section("Quick Wins", f'<div class="quick-wins">{wins_html}</div>')}
{section("Open Questions", oq_html)}
{section("Sources Consulted", sources_html)}

<section><h2>Raw JSON</h2>
<button onclick="navigator.clipboard.writeText(document.getElementById('json-data').textContent).then(()=>this.textContent='Copied!').catch(()=>this.textContent='Copy failed')" style="margin-bottom:10px;padding:6px 14px;cursor:pointer;border:1px solid #ccc;border-radius:4px;background:#f8f9fa;font-size:0.85em">Copy JSON</button>
<pre id="json-data" style="background:#f4f4f4;padding:16px;border-radius:4px;overflow-x:auto;font-size:0.8em;line-height:1.5">{json_data}</pre>
</section>

<footer>Generated by Google Ads Audit Agent &middot; {timestamp}</footer>
</body>
</html>"""


def _output_path(keyword: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_")[:40]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parents[2] / "output"
    output_dir.mkdir(exist_ok=True)
    return output_dir / f"audit_{slug}_{timestamp}.html"


def main() -> None:
    load_dotenv()
    args = parse_args()
    configure_logging(args.log_level)
    configure_openai_client(args.openai_timeout, args.openai_max_retries)
    try:
        report = asyncio.run(
            run_audit(
                keyword=args.keyword,
                business=args.business,
                client_url=args.client_url,
                max_turns=args.max_turns,
            )
        )
    except APITimeoutError as exc:
        raise SystemExit(
            "OpenAI request timed out. Try rerunning with "
            "`--openai-timeout 300 --max-turns 8`, or set "
            "OPENAI_TIMEOUT_SECONDS=300 in .env."
        ) from exc
    output_path = _output_path(args.keyword)
    json_data = json.dumps(report.model_dump(mode="json"), indent=2)
    output_path.write_text(render_html(report, args.keyword, args.business, args.client_url, json_data), encoding="utf-8")
    print(f"Report saved: {output_path.resolve()}")


if __name__ == "__main__":
    main()
