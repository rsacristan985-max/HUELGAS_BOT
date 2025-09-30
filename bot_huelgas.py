# bot_huelgas.py
import asyncio
import os
import json
from datetime import datetime
import logging

import aiohttp
import feedparser
from bs4 import BeautifulSoup
import discord
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 300))

SOURCES = [
    {"name": "Comunidad de Madrid - Huelgas", "type": "html", "url": "https://www.comunidad.madrid/servicios/empleo/huelgas"},
    {"name": "BOCM - Últimos boletines", "type": "bocm_search", "url": "https://www.bocm.es"},
]

STATE_FILE = "seen_items.json"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("huelga-bot")
intents = discord.Intents.default()
client = discord.Client(intents=intents)

EDU_KEYWORDS = [
    "educación", "educativo", "enseñanza", "estudiantes", "alumnos", "alumnado",
    "colegio", "instituto", "universidad", "universidades", "campus",
    "profesorado", "profesores", "maestros", "docentes",
    "escuela", "upm", "ucm", "uam", "transporte escolar"
]

def is_relevant(text: str) -> bool:
    t = text.lower()
    if "huelga" not in t: return False
    return any(kw in t for kw in EDU_KEYWORDS)

async def fetch_text(session, url):
    async with session.get(url, timeout=30) as resp: return await resp.text()

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {"seen": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f: json.dump(state, f, ensure_ascii=False, indent=2)

async def check_html_for_huelga(session, src):
    html = await fetch_text(session, src["url"])
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        a_text = (a.get_text() or "").strip()
        if is_relevant(a_text + " " + href):
            url = href if href.startswith("http") else (src["url"].rstrip("/") + "/" + href.lstrip("/"))
            results.append({"id": src["name"] + "::" + url, "title": a_text or "Aviso de huelga educativa", "url": url, "summary": a_text})
    if is_relevant(soup.get_text()) and not results:
        results.append({"id": src["name"] + "::" + src["url"], "title": "Posible referencia a huelga educativa", "url": src["url"], "summary": soup.get_text()[:400]})
    return results

async def check_bocm(session, src):
    html = await fetch_text(session, src["url"])
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        a_text = (a.get_text() or "").strip()
        if is_relevant(a_text + " " + href):
            url = href if href.startswith("http") else (src["url"].rstrip("/") + "/" + href.lstrip("/"))
            results.append({"id": src["name"] + "::" + url, "title": a_text or "BOCM: aviso de huelga educativa", "url": url, "summary": a_text})
    if is_relevant(soup.get_text()) and not results:
        results.append({"id": src["name"] + "::" + src["url"], "title": "BOCM: posible mención a huelga educativa", "url": src["url"], "summary": soup.get_text()[:400]})
    return results

async def check_rss(session, src):
    feed = feedparser.parse(src["url"])
    results = []
    for entry in feed.entries:
        combined = (entry.get("title","") + " " + entry.get("summary","") + " " + entry.get("description",""))
        if is_relevant(combined):
            url = entry.get("link", src["url"])
            results.append({"id": src["name"] + "::" + url, "title": entry.get("title","Aviso de huelga educativa"), "url": url, "summary": entry.get("summary","")})
    return results

CHECKERS = {"html": check_html_for_huelga, "bocm_search": check_bocm, "rss": check_rss}

async def gather_new_items(state):
    new_items = []
    async with aiohttp.ClientSession() as session:
        tasks = [CHECKERS[src["type"]](session, src) for src in SOURCES if src["type"] in CHECKERS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception): logger.exception("Error fetch:", exc_info=res); continue
            for item in res:
                if item["id"] not in state["seen"]: new_items.append(item)
    return new_items

async def notify_channel(channel, items):
    for it in items:
        embed = discord.Embed(title=it["title"] or "Aviso de huelga educativa", description=it.get("summary",""), url=it["url"], timestamp=datetime.utcnow())
        embed.set_footer(text="Fuente detectada automáticamente")
        await channel.send(embed=embed)

@client.event
async def on_ready():
    logger.info(f"Conectado como {client.user} — iniciando loop de comprobación.")
    state = load_state()
    channel = client.get_channel(CHANNEL_ID)
    if channel is None: logger.error("Canal no encontrado: revisa CHANNEL_ID")
    async def loop():
        nonlocal state
        while True:
            try:
                new_items = await gather_new_items(state)
                if new_items:
                    logger.info("Nuevos items detectados: %d", len(new_items))
                    if channel: await notify_channel(channel, new_items)
                    for it in new_items: state["seen"].append(it["id"])
                    save_state(state)
                else:
                    logger.info("Sin novedades.")
            except Exception as e:
                logger.exception("Error en loop principal: %s", e)
            await asyncio.sleep(POLL_INTERVAL)
    client.loop.create_task(loop())

if __name__ == "__main__":
    if not DISCORD_TOKEN or CHANNEL_ID == 0:
        print("Necesitas configurar DISCORD_TOKEN y CHANNEL_ID en variables de entorno o en .env")
        raise SystemExit(1)
    client.run(DISCORD_TOKEN)
