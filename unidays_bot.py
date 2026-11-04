"""Relaie les nouvelles offres UNiDAYS (France) sur un salon Discord via webhook.

Usage :
    python unidays_bot.py --dry-run   # affiche les nouvelles offres sans rien poster
    python unidays_bot.py             # poste les nouvelles offres sur le webhook

Variable d'environnement requise (hors --dry-run) : DISCORD_WEBHOOK_URL
(un ou plusieurs liens de webhook, un par ligne)
Au tout premier lancement, les offres existantes sont seulement mémorisées
(pas postées) pour éviter de spammer le salon. Utilise --post-existing pour forcer.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://www.myunidays.com"
PAGE_URL = f"{BASE}/FR/fr-FR"
STATE_FILE = Path(__file__).with_name("seen_offers.json")
USER_AGENT = "UnidaysAnnounceBot/1.0 (bot Discord etudiant)"
EMBED_COLOR = 0x1ED8A9
MAX_POSTS_PER_RUN = 10

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def fetch_page_data() -> dict:
    # La page est servie par CloudFront avec un cache très permissif : chaque serveur
    # relais peut renvoyer une copie périmée. Un paramètre aléatoire force une version fraîche.
    resp = requests.get(
        PAGE_URL,
        params={"nocache": int(time.time() * 1000)},
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
        timeout=30,
    )
    resp.raise_for_status()
    match = NEXT_DATA_RE.search(resp.text)
    if not match:
        raise RuntimeError("__NEXT_DATA__ introuvable : la structure du site a peut-être changé.")
    return json.loads(match.group(1))


def iter_offers(node, section=None):
    """Parcourt récursivement le JSON et renvoie (section, benefit, partner)."""
    if isinstance(node, dict):
        if isinstance(node.get("title"), str) and "benefits" in node:
            section = node["title"]
        benefit, partner = node.get("benefit"), node.get("partner")
        if isinstance(benefit, dict) and isinstance(partner, dict) and benefit.get("id"):
            yield section, benefit, partner
        for value in node.values():
            yield from iter_offers(value, section)
    elif isinstance(node, list):
        for item in node:
            yield from iter_offers(item, section)


def extract_offers(data: dict) -> dict:
    offers = {}
    for section, benefit, partner in iter_offers(data):
        oid = benefit["id"]
        if oid in offers:
            continue
        content = benefit.get("content") or {}
        url = benefit.get("url") or ""
        if url.startswith("/"):
            url = BASE + url
        offers[oid] = {
            "id": oid,
            "brand": partner.get("displayName") or content.get("slug") or "?",
            "title": benefit.get("displayName") or "Offre étudiante",
            "url": url or PAGE_URL,
            "logo": partner.get("logo"),
            "image": benefit.get("defaultImageUrl"),
            "start": benefit.get("startDate"),
            "end": benefit.get("endDate"),
            "channel": benefit.get("channel"),
            "limited": bool(content.get("isLimitedTimeOnly")),
            "exclusive": bool(content.get("isExclusive")),
            "section": section,
        }
    return offers


def fmt_date(iso):
    if not iso or iso.startswith("9999"):
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"<t:{int(dt.timestamp())}:D>"  # date localisée par Discord
    except ValueError:
        return None


def build_embed(offer: dict) -> dict:
    tags = []
    if offer["limited"]:
        tags.append("⏳ Durée limitée")
    if offer["exclusive"]:
        tags.append("⭐ Exclusif")
    if offer["channel"]:
        tags.append({"online": "🌐 En ligne", "instore": "🏬 En magasin"}.get(offer["channel"], offer["channel"]))

    fields = []
    if end := fmt_date(offer["end"]):
        fields.append({"name": "Jusqu'au", "value": end, "inline": True})
    if offer["section"]:
        fields.append({"name": "Rubrique", "value": offer["section"], "inline": True})

    embed = {
        "title": f"{offer['brand']} — {offer['title']}",
        "url": offer["url"],
        "description": " · ".join(tags) or None,
        "color": EMBED_COLOR,
        "fields": fields,
        "footer": {"text": "Génère ton code sur UNiDAYS (compte étudiant requis)"},
    }
    if offer["logo"]:
        embed["thumbnail"] = {"url": offer["logo"]}
    if offer["image"]:
        embed["image"] = {"url": offer["image"]}
    return {k: v for k, v in embed.items() if v}


def post_to_discord(webhook: str, offer: dict):
    payload = {"username": "UNiDAYS Bot", "embeds": [build_embed(offer)]}
    for _ in range(3):
        resp = requests.post(webhook, json=payload, timeout=30)
        if resp.status_code == 429:  # rate limit Discord
            time.sleep(float(resp.json().get("retry_after", 2)))
            continue
        resp.raise_for_status()
        return
    raise RuntimeError("Discord rate limit persistant")


def load_state():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return None


def save_state(ids):
    STATE_FILE.write_text(json.dumps(sorted(ids), indent=1), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="n'affiche que, ne poste rien, ne sauvegarde rien")
    parser.add_argument("--post-existing", action="store_true", help="au 1er lancement, poste aussi les offres déjà présentes")
    args = parser.parse_args()

    offers = extract_offers(fetch_page_data())
    if not offers:
        sys.exit("Aucune offre trouvée : la structure du site a probablement changé.")

    seen = load_state()
    first_run = seen is None
    seen = seen or set()
    new = [o for oid, o in offers.items() if oid not in seen]
    new.sort(key=lambda o: o["start"] or "")  # plus anciennes d'abord

    print(f"{len(offers)} offres trouvées, {len(new)} nouvelles.")

    if args.dry_run:
        for o in new:
            print(f"- [{o['section']}] {o['brand']} : {o['title']} -> {o['url']}")
        return

    if first_run and not args.post_existing:
        save_state(seen | offers.keys())
        print("Premier lancement : offres mémorisées sans être postées.")
        return

    # Un ou plusieurs webhooks, séparés par des retours à la ligne, virgules ou espaces.
    webhooks = re.split(r"[\s,]+", os.environ.get("DISCORD_WEBHOOK_URL", "").strip())
    webhooks = [w for w in webhooks if w]
    if not webhooks:
        sys.exit("DISCORD_WEBHOOK_URL n'est pas défini.")

    for o in new[:MAX_POSTS_PER_RUN]:
        for n, webhook in enumerate(webhooks, 1):
            try:
                post_to_discord(webhook, o)
            except Exception as e:  # un webhook cassé ne doit pas bloquer les autres serveurs
                status = getattr(getattr(e, "response", None), "status_code", type(e).__name__)
                print(f"::warning::Webhook n°{n} en erreur ({status}) pour {o['brand']}")
        seen.add(o["id"])
        print(f"Posté : {o['brand']} - {o['title']}")
        time.sleep(1)
    save_state(seen)
    if len(new) > MAX_POSTS_PER_RUN:
        print(f"{len(new) - MAX_POSTS_PER_RUN} offres restantes seront postées au prochain passage.")


if __name__ == "__main__":
    main()
