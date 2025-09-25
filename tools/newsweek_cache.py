# file: tools/newsweek_cache.py
import json, os, time, email.utils
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from xml.etree import ElementTree as ET

FEED_URL = "https://www.newsweek.pl/.feed"
STORE_PATH = "data/newsweek_store.json"
OUTPUT_PATH = "docs/newsweek.xml"  # <-- zapis do /docs
RETENTION_DAYS = 7

os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

def now_utc():
    return datetime.now(timezone.utc)

def load_store():
    if not os.path.exists(STORE_PATH):
        return {}
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_store(store):
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)

def fetch_feed_xml(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (RSS cache)"})
    with urlopen(req, timeout=30) as resp:
        return resp.read()

def parse_rss_items(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    channel = root.find("channel")
    if channel is not None:
        for it in channel.findall("item"):
            items.append(it)
    else:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for it in root.findall("atom:entry", ns):
            items.append(it)
    return items

def text(el, tag):
    t = el.find(tag)
    return t.text.strip() if t is not None and t.text else ""

def guid_clean(raw: str) -> str:
    if not raw:
        return ""
    lower = raw.lower()
    if lower.startswith("urn:uuid:"):
        return raw.split(":", 2)[-1]
    if ":" in raw:
        return raw.split(":")[-1]
    return raw

def parse_pubdate(pd: str):
    try:
        dt = email.utils.parsedate_to_datetime(pd)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def extract_item_data(it):
    title = text(it, "title")
    link = text(it, "link")
    desc_el = it.find("description")
    description = desc_el.text if (desc_el is not None and desc_el.text) else ""
    enc_el = it.find("enclosure")
    enclosure = None
    if enc_el is not None:
        enclosure = {
            "url": enc_el.attrib.get("url", ""),
            "length": enc_el.attrib.get("length", ""),
            "type": enc_el.attrib.get("type", ""),
        }
    raw_guid = text(it, "guid")
    guid = guid_clean(raw_guid)
    pub_date_raw = text(it, "pubDate")
    pub_date = parse_pubdate(pub_date_raw)
    return {
        "guid": guid,
        "title": title,
        "link": link,
        "description": description,
        "enclosure": enclosure,
        "pubDate_raw": pub_date_raw,
        "pubDate": pub_date.isoformat() if pub_date else None,
    }

def nowiso():
    return now_utc().isoformat()

def prune_store(store):
    cutoff = now_utc() - timedelta(days=RETENTION_DAYS)
    to_delete = []
    for g, rec in list(store.items()):
        fetched_at = datetime.fromisoformat(rec["fetched_at"])
        if fetched_at < cutoff:
            to_delete.append(g)
    for g in to_delete:
        del store[g]

def upsert_items(store, items_data):
    fetched = nowiso()
    for d in items_data:
        g = d["guid"]
        if not g:
            continue
        if g not in store:
            store[g] = {**d, "fetched_at": fetched}
        else:
            store[g].update({k: d[k] for k in d if k not in ("guid", "fetched_at")})

def build_rss(store):
    rss = ET.Element("rss", attrib={"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Newsweek – cache (5h, 7 dni)"
    ET.SubElement(channel, "link").text = "https://www.newsweek.pl/"
    ET.SubElement(channel, "description").text = "Lustrzany cache jednego feedu, odświeżany co 5 godzin"
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(now_utc())

    def sort_key(rec):
        pd = rec.get("pubDate")
        if pd:
            try:
                return datetime.fromisoformat(pd)
            except Exception:
                pass
        return datetime.fromisoformat(rec["fetched_at"])

    for _, rec in sorted(store.items(), key=lambda kv: sort_key(kv[1]), reverse=True):
        it = ET.SubElement(channel, "item")
        if rec.get("title"):
            ET.SubElement(it, "title").text = rec["title"]
        if rec.get("link"):
            ET.SubElement(it, "link").text = rec["link"]
        desc_text = rec.get("description") or ""
        if desc_text:
            d = ET.SubElement(it, "description")
            d.text = f"__CDATA_PLACEHOLDER_START__{desc_text}__CDATA_PLACEHOLDER_END__"
        enc = rec.get("enclosure")
        if enc and enc.get("url"):
            enc_el = ET.SubElement(it, "enclosure")
            for k in ("url", "length", "type"):
                v = enc.get(k)
                if v:
                    enc_el.set(k, str(v))
        if rec.get("pubDate_raw"):
            ET.SubElement(it, "pubDate").text = rec["pubDate_raw"]
        if rec.get("guid"):
            ET.SubElement(it, "guid").text = rec["guid"]

    tree = ET.ElementTree(rss)
    xml_bytes = ET.tostring(tree.getroot(), encoding="utf-8")
    xml_str = xml_bytes.decode("utf-8")
    xml_str = xml_str.replace("__CDATA_PLACEHOLDER_START__", "<![CDATA[")
    xml_str = xml_str.replace("__CDATA_PLACEHOLDER_END__", "]]]>")
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(xml_str)

def main():
    store = load_store()
    prune_store(store)
    xml = fetch_feed_xml(FEED_URL)
    items = parse_rss_items(xml)
    parsed = [extract_item_data(it) for it in items]
    upsert_items(store, parsed)
    save_store(store)
    build_rss(store)

if __name__ == "__main__":
    main()
