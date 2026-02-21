"""
将论文列表转换为 Discord embed 格式并通过 webhook 发送到论坛频道。
"""

import math
import time
import datetime
import requests
from loguru import logger
from paper import ArxivPaper


PAPERS_PER_MESSAGE = 5


def get_stars_text(score: float) -> str:
    """将相关度分数转换为纯文本星级（与 construct_email.get_stars 逻辑一致）"""
    low, high = 6, 8
    if score <= low:
        return ""
    if score >= high:
        return "⭐" * 5
    interval = (high - low) / 10
    star_num = math.ceil((score - low) / interval)
    full = star_num // 2
    half = star_num - full * 2
    return "⭐" * full + ("½" if half else "")


def _format_authors(authors: list) -> str:
    names = [a.name for a in authors]
    if len(names) <= 5:
        return ", ".join(names)
    return ", ".join(names[:3] + ["..."] + names[-2:])


def _format_affiliations(affiliations: list[str] | None) -> str:
    if not affiliations:
        return "Unknown"
    shown = affiliations[:5]
    text = ", ".join(shown)
    if len(affiliations) > 5:
        text += ", ..."
    return text


# 相关度 → embed 侧边颜色
_SCORE_COLORS = {
    "high": 0xE74C3C,    # 红色 (score >= 7.5)
    "medium": 0xF39C12,  # 橙色 (score >= 6.5)
    "low": 0x3498DB,     # 蓝色
}


def _score_color(score: float | None) -> int:
    if score is None:
        return _SCORE_COLORS["low"]
    if score >= 7.5:
        return _SCORE_COLORS["high"]
    if score >= 6.5:
        return _SCORE_COLORS["medium"]
    return _SCORE_COLORS["low"]


def render_paper_embed(paper: ArxivPaper, index: int) -> dict:
    """将单篇论文转换为 Discord embed 字典"""
    stars = get_stars_text(paper.score) if paper.score is not None else ""

    links_parts = [f"[PDF]({paper.pdf_url})"]
    if paper.code_url:
        links_parts.append(f"[Code]({paper.code_url})")

    fields = [
        {"name": "Authors", "value": _format_authors(paper.authors), "inline": False},
        {"name": "Affiliations", "value": _format_affiliations(paper.affiliations), "inline": False},
    ]
    if stars:
        fields.append({"name": "Relevance", "value": stars, "inline": True})
    fields.append({"name": "Links", "value": " | ".join(links_parts), "inline": True})

    return {
        "title": f"{index}. {paper.title}",
        "url": f"https://arxiv.org/abs/{paper.arxiv_id}",
        "description": f"**TLDR:** {paper.tldr}",
        "color": _score_color(paper.score),
        "fields": fields,
    }


def _post_webhook(webhook_url: str, payload: dict) -> dict:
    """发送 webhook 请求，带重试"""
    url = webhook_url if "?" in webhook_url else webhook_url + "?wait=true"
    if "wait=true" not in url:
        url += "&wait=true"

    for attempt in range(3):
        resp = requests.post(url, json=payload, timeout=30,
                             headers={"User-Agent": "ZoteroArxivDaily/1.0"})
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 2)
            logger.warning(f"Rate limited, retrying after {retry_after}s...")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Discord webhook failed after 3 retries")


def create_forum_post(webhook_url: str, papers: list[ArxivPaper]) -> str:
    """
    在论坛频道创建新帖子并发送所有论文。返回 thread_id。

    - 空列表：发送"今日无新论文"，不发触发标记
    - 非空：每 PAPERS_PER_MESSAGE 篇一条消息，最后发触发标记
    """
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    if not papers:
        payload = {
            "thread_name": f"📄 Daily arXiv {today}",
            "content": "今日无新论文。Yesterday might be a holiday 🏖️",
        }
        data = _post_webhook(webhook_url, payload)
        thread_id = data["channel_id"]
        logger.info(f"Created empty forum post, thread_id: {thread_id}")
        return thread_id

    # 第一条消息（主楼）：仅创建帖子并报告概要信息
    score_high = sum(1 for p in papers if p.score is not None and p.score >= 7.5)
    score_mid = sum(1 for p in papers if p.score is not None and 6.5 <= p.score < 7.5)
    score_low = len(papers) - score_high - score_mid

    summary_lines = [
        f"**Daily arXiv {today}** — 共 {len(papers)} 篇论文",
        "",
        f"🔴 高相关度 (≥7.5): {score_high} 篇",
        f"🟠 中相关度 (≥6.5): {score_mid} 篇",
        f"🔵 低相关度 (<6.5): {score_low} 篇",
    ]
    first_payload = {
        "thread_name": f"📄 Daily arXiv {today}",
        "content": "\n".join(summary_lines),
    }
    data = _post_webhook(webhook_url, first_payload)
    thread_id = data["channel_id"]
    logger.info(f"Created forum post, thread_id: {thread_id}")

    # 分批构建 embeds
    batches = []
    for i in range(0, len(papers), PAPERS_PER_MESSAGE):
        batch_embeds = []
        for j, paper in enumerate(papers[i : i + PAPERS_PER_MESSAGE]):
            batch_embeds.append(render_paper_embed(paper, i + j + 1))
        batches.append(batch_embeds)

    # 后续消息：追加论文到帖子
    thread_url = f"{webhook_url}?thread_id={thread_id}&wait=true"
    for idx, batch in enumerate(batches, start=1):
        time.sleep(1)  # 避免速率限制
        payload = {"embeds": batch}
        _post_webhook(thread_url, payload)
        logger.debug(f"Sent batch {idx}/{len(batches)}")

    # 触发标记
    time.sleep(1)
    trigger = f"📊 ARXIV_DAILY_COMPLETE | {today} | 共 {len(papers)} 篇论文"
    _post_webhook(thread_url, {"content": trigger})
    logger.info("Sent ARXIV_DAILY_COMPLETE trigger")

    return thread_id
