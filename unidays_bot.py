"""Relaie les offres UNiDAYS (France) sur un salon Discord via webhook.

Deux sources :
- les nouveautés mises en avant sur la page d'accueil, postées dès qu'elles apparaissent ;
- le catalogue complet (offres permanentes, jamais mises en avant), posté au compte-gouttes
  pour faire (re)découvrir les autres offres sans spammer le salon.

Usage :
    python unidays_bot.py --dry-run   # affiche ce qui serait posté, sans rien poster
    python unidays_bot.py             # poste sur le(s) webhook(s)

Variables d'environnement :
    DISCORD_WEBHOOK_URL    requise (hors --dry-run) : un ou plusieurs webhooks, un par ligne
    CATALOGUE_WEBHOOK_URL  optionnelle : webhook(s) dédié(s) aux offres du catalogue
                           (par défaut, les mêmes que DISCORD_WEBHOOK_URL)
    CATALOGUE_PER_DAY      optionnelle : nombre d'offres du catalogue postées par jour
                           (défaut 3, soit une toutes les 8 h ; 0 désactive)
                           Une offre qui expire dans moins de 2 jours est sautée au profit d'une autre.

Au tout premier lancement, les offres existantes sont seulement mémorisées
(pas postées) pour éviter de spammer le salon. Utilise --post-existing pour forcer.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from itertools import chain, zip_longest
from pathlib import Path

import requests

BASE = "https://www.myunidays.com"
PAGE_URL = f"{BASE}/FR/fr-FR"
FEED_URL = "https://feeds.prod.unidays.io/FR/feeds/partners-best-benefit-by-cat"
CONTENT_URL = "https://content.myunidays.com/FR"
STATE_FILE = Path(__file__).with_name("seen_offers.json")
CATALOGUE_STATE_FILE = Path(__file__).with_name("catalogue_state.json")
USER_AGENT = "UnidaysAnnounceBot/1.0 (bot Discord etudiant)"
HEADERS = {"User-Agent": USER_AGENT}
EMBED_COLOR = 0x1ED8A9
CATALOGUE_COLOR = 0x7B61FF
MAX_POSTS_PER_RUN = 10
DEFAULT_CATALOGUE_PER_DAY = 3
# Une offre du catalogue n'est postée que s'il lui reste au moins ce délai avant expiration.
CATALOGUE_MIN_REMAINING = timedelta(days=2)
# Les passages GitHub Actions sont décalés de quelques minutes : on tolère un peu d'avance.
SCHEDULE_TOLERANCE = timedelta(minutes=10)

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def fetch_page_data() -> dict:
    # La page est servie par CloudFront avec un cache très permissif : chaque serveur
    # relais peut renvoyer une copie périmée. Un paramètre aléatoire force une version fraîche.
    resp = requests.get(
        PAGE_URL,
        params={"nocache": int(time.time() * 1000)},
        headers={**HEADERS, "Cache-Control": "no-cache"},
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


def make_offer(benefit: dict, partner: dict, section) -> dict:
    content = benefit.get("content") or {}
    url = benefit.get("url") or ""
    if url.startswith("/"):
        url = BASE + url
    return {
        "id": benefit["id"],
        "brand": partner.get("displayName") or content.get("slug") or "?",
        "title": (benefit.get("displayName") or "Offre étudiante").strip(),
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


def extract_offers(data: dict) -> dict:
    offers = {}
    for section, benefit, partner in iter_offers(data):
        if benefit["id"] not in offers:
            offers[benefit["id"]] = make_offer(benefit, partner, section)
    return offers


# --- Catalogue complet (offres non mises en avant) -----------------------------------

def get_categories(data: dict) -> list:
    """(nom affiché, slug) des grandes catégories, lus dans le menu du site."""
    links = (data.get("props", {}).get("pageProps", {}).get("navigation") or {}).get("links") or []
    cats = []
    for link in links:
        if len(link) >= 2 and isinstance(link[1], str):
            m = re.fullmatch(r"/categories/([\w-]+)", link[1])
            if m and "_" not in m.group(1):  # les sous-catégories contiennent un "_"
                cats.append((link[0], m.group(1)))
    return cats


def fetch_feed_ids(filters=None) -> list:
    """Identifiants des offres d'un flux UNiDAYS (meilleure offre de chaque marque)."""
    ids, url = [], FEED_URL
    params = {"memberTargets": "student", "pageSize": 500, "pageNumber": 1}
    if filters:
        params["filters"] = filters
    while url:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        feed = resp.json()
        feed = feed[0] if isinstance(feed, list) else feed
        ids += [item.rstrip("/").rsplit("/", 1)[-1] for item in feed.get("items", [])]
        url, params = (feed.get("pagination") or {}).get("next"), None
    return ids


def fetch_catalogue(data: dict) -> list:
    """Liste ordonnée de (id, rubrique), en alternant les catégories pour varier les posts."""
    per_category = []
    for name, slug in get_categories(data):
        per_category.append([(oid, name) for oid in fetch_feed_ids(f"cat-{slug}")])
    if not per_category:  # menu introuvable : flux global, sans rubrique
        per_category = [[(oid, None) for oid in fetch_feed_ids()]]

    catalogue, done = [], set()
    for oid, name in chain.from_iterable(zip_longest(*per_category, fillvalue=(None, None))):  # round-robin
        if oid and oid not in done:
            done.add(oid)
            catalogue.append((oid, name))
    return catalogue


def fetch_content(kind: str, oid: str):
    resp = requests.get(f"{CONTENT_URL}/{kind}/{oid}", headers=HEADERS, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def expires_too_soon(benefit: dict, now: datetime) -> bool:
    end = parse_date(benefit.get("endDate"))
    return end is not None and end - now < CATALOGUE_MIN_REMAINING


def fetch_catalogue_offers(candidates, count: int) -> list:
    """Récupère le détail des `count` premières offres encore valables au moins 2 jours."""
    offers, now = [], datetime.now(timezone.utc)
    for oid, section in candidates:
        if len(offers) >= count:
            break
        benefit = fetch_content("benefits", oid)
        if not benefit or not benefit.get("id"):
            continue  # offre retirée entre-temps
        if expires_too_soon(benefit, now):
            continue  # expire trop vite : on passe à la suivante
        partner = fetch_content("partners", benefit.get("partnerId", "")) or {}
        offers.append(make_offer(benefit, partner, section))
    return offers


def catalogue_per_day() -> int:
    raw = os.environ.get("CATALOGUE_PER_DAY", "").strip()
    try:
        return max(0, int(raw)) if raw else DEFAULT_CATALOGUE_PER_DAY
    except ValueError:
        print(f"::warning::CATALOGUE_PER_DAY invalide ({raw!r}), valeur par défaut utilisée.")
        return DEFAULT_CATALOGUE_PER_DAY


def load_last_catalogue_post():
    if CATALOGUE_STATE_FILE.exists():
        last = json.loads(CATALOGUE_STATE_FILE.read_text(encoding="utf-8")).get("last_post")
        return datetime.fromisoformat(last) if last else None
    return None


def save_last_catalogue_post(when: datetime):
    CATALOGUE_STATE_FILE.write_text(json.dumps({"last_post": when.isoformat()}, indent=1), encoding="utf-8")


# --- Discord ---------------------------------------------------------------------------

def parse_date(iso):
    """Date ISO -> datetime ; None si absente ou « sans fin » (année 9999)."""
    if not iso or iso.startswith("9999"):
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_date(iso):
    dt = parse_date(iso)
    return f"<t:{int(dt.timestamp())}:D>" if dt else None  # date localisée par Discord


def build_embed(offer: dict, catalogue=False) -> dict:
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
        "color": CATALOGUE_COLOR if catalogue else EMBED_COLOR,
        "fields": fields,
        "footer": {"text": "Génère ton code sur UNiDAYS (compte étudiant requis)"},
    }
    if catalogue:
        embed["author"] = {"name": "💡 Offre à (re)découvrir"}
    if offer["logo"]:
        embed["thumbnail"] = {"url": offer["logo"]}
    if offer["image"]:
        embed["image"] = {"url": offer["image"]}
    return {k: v for k, v in embed.items() if v}


def post_to_discord(webhook: str, offer: dict, catalogue=False):
    payload = {"username": "UNiDAYS Bot", "embeds": [build_embed(offer, catalogue)]}
    for _ in range(3):
        resp = requests.post(webhook, json=payload, timeout=30)
        if resp.status_code == 429:  # rate limit Discord
            time.sleep(float(resp.json().get("retry_after", 2)))
            continue
        resp.raise_for_status()
        return
    raise RuntimeError("Discord rate limit persistant")


def post_everywhere(webhooks, offer: dict, catalogue=False):
    for n, webhook in enumerate(webhooks, 1):
        try:
            post_to_discord(webhook, offer, catalogue)
        except Exception as e:  # un webhook cassé ne doit pas bloquer les autres serveurs
            status = getattr(getattr(e, "response", None), "status_code", type(e).__name__)
            print(f"::warning::Webhook n°{n} en erreur ({status}) pour {offer['brand']}")


def parse_webhooks(var: str) -> list:
    # Un ou plusieurs webhooks, séparés par des retours à la ligne, virgules ou espaces.
    return [w for w in re.split(r"[\s,]+", os.environ.get(var, "").strip()) if w]


# --- État ------------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return None


def save_state(ids):
    STATE_FILE.write_text(json.dumps(sorted(ids), indent=1), encoding="utf-8")


# --- Programme principal ---------------------------------------------------------------

def run_catalogue(data, offers, seen, webhooks, dry_run):
    per_day = catalogue_per_day()
    if per_day == 0:
        return
    now = datetime.now(timezone.utc)
    last = load_last_catalogue_post()
    next_due = last + timedelta(days=1) / per_day - SCHEDULE_TOLERANCE if last else now

    # On exclut les offres déjà postées et celles de l'accueil (gérées comme nouveautés).
    candidates = [(oid, s) for oid, s in fetch_catalogue(data) if oid not in seen and oid not in offers]
    print(f"Catalogue : {len(candidates)} offres jamais postées.")

    if dry_run:
        when = "dès le prochain passage" if next_due <= now else f"à partir de {next_due:%d/%m %H:%M} UTC"
        print(f"Prochaine offre du catalogue {when}. Les 5 prochaines :")
        for o in fetch_catalogue_offers(candidates, 5):
            print(f"- [{o['section']}] {o['brand']} : {o['title']} -> {o['url']}")
        return
    if next_due > now or not candidates:
        return

    for o in fetch_catalogue_offers(candidates, 1):
        post_everywhere(webhooks, o, catalogue=True)
        seen.add(o["id"])
        print(f"Posté (catalogue) : {o['brand']} - {o['title']}")
    save_state(seen)
    save_last_catalogue_post(now)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="n'affiche que, ne poste rien, ne sauvegarde rien")
    parser.add_argument("--post-existing", action="store_true", help="au 1er lancement, poste aussi les offres déjà présentes")
    args = parser.parse_args()

    data = fetch_page_data()
    offers = extract_offers(data)
    if not offers:
        sys.exit("Aucune offre trouvée : la structure du site a probablement changé.")

    seen = load_state()
    first_run = seen is None
    seen = seen or set()
    new = [o for oid, o in offers.items() if oid not in seen]
    new.sort(key=lambda o: o["start"] or "")  # plus anciennes d'abord

    print(f"{len(offers)} offres en avant sur l'accueil, {len(new)} nouvelles.")

    if args.dry_run:
        for o in new:
            print(f"- [{o['section']}] {o['brand']} : {o['title']} -> {o['url']}")
        run_catalogue(data, offers, seen, [], dry_run=True)
        return

    if first_run and not args.post_existing:
        save_state(seen | offers.keys())
        print("Premier lancement : offres mémorisées sans être postées.")
        return

    webhooks = parse_webhooks("DISCORD_WEBHOOK_URL")
    if not webhooks:
        sys.exit("DISCORD_WEBHOOK_URL n'est pas défini.")

    for o in new[:MAX_POSTS_PER_RUN]:
        post_everywhere(webhooks, o)
        seen.add(o["id"])
        print(f"Posté : {o['brand']} - {o['title']}")
        time.sleep(1)
    save_state(seen)
    if len(new) > MAX_POSTS_PER_RUN:
        print(f"{len(new) - MAX_POSTS_PER_RUN} offres restantes seront postées au prochain passage.")

    # Le catalogue ne doit jamais faire échouer le passage : les nouveautés sont déjà postées.
    try:
        run_catalogue(data, offers, seen, parse_webhooks("CATALOGUE_WEBHOOK_URL") or webhooks, dry_run=False)
    except Exception as e:
        print(f"::warning::Catalogue indisponible ({type(e).__name__}: {e})")


if __name__ == "__main__":
    main()
