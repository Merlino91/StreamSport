import asyncio
import logging
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from app.config import ENABLE_REPLAYS
from app.services.catalog_service import CatalogService
from app.services.daddylive_api import daddylive_api
from app.services.dailymotion_service import dailymotion_service
from app.services.db_service import db_service
from app.services.genre_classifier import genre_classifier
from app.services.sportvideo_service import sportvideo_service
from app.services.streamed_api import streamed_api
from app.services.youtube_service import youtube_service

logger = logging.getLogger("streamsport.stream")

# Language flags & Italian translations for Streamed sources
STREAMED_LANG_MAP = {
    "english": ("🇬🇧", "Inglese"),
    "en": ("🇬🇧", "Inglese"),
    "italian": ("🇮🇹", "Italiano"),
    "it": ("🇮🇹", "Italiano"),
    "spanish": ("🇪🇸", "Spagnolo"),
    "es": ("🇪🇸", "Spagnolo"),
    "french": ("🇫🇷", "Francese"),
    "fr": ("🇫🇷", "Francese"),
    "german": ("🇩🇪", "Tedesco"),
    "de": ("🇩🇪", "Tedesco"),
    "portuguese": ("🇵🇹", "Portoghese"),
    "pt": ("🇵🇹", "Portoghese"),
    "russian": ("🇷🇺", "Russo"),
    "ru": ("🇷🇺", "Russo"),
    "dutch": ("🇳🇱", "Olandese"),
    "nl": ("🇳🇱", "Olandese"),
    "arabic": ("🇸🇦", "Arabo"),
    "ar": ("🇸🇦", "Arabo"),
    "turkish": ("🇹🇷", "Turco"),
    "tr": ("🇹🇷", "Turco"),
    "polish": ("🇵🇱", "Polacco"),
    "pl": ("🇵🇱", "Polacco"),
}

# Country flag regex patterns for channel names
CHANNEL_COUNTRY_PATTERNS: List[Tuple[re.Pattern, Optional[re.Pattern], str]] = [
    # 1. Italian: strict word boundaries on keywords including Rai and calcio, must NOT contain foreign country keywords
    (
        re.compile(r"\b(it|italy|italia|rai|calcio|raisport|supertennis|sportitalia|mediaset|canale\s*5|italia\s*1|tv8)\b", re.IGNORECASE),
        re.compile(r"\b(uk|spain|espana|germany|deutschland|austria|ca|canada|us|usa|turkey|turkiye|croatia|hrvatska|serbia|srbija|france|poland|polska|netherlands|holland|russia)\b", re.IGNORECASE),
        "🇮🇹"
    ),
    # 2. United Kingdom
    (
        re.compile(r"\b(uk|gb|england|united\s*kingdom|bbc|itv|tnt\s*sports?)\b", re.IGNORECASE),
        None,
        "🇬🇧"
    ),
    # 3. USA
    (
        re.compile(r"\b(usa?|espn|fox\s*sports?|cbs|nbc|abc|tnt\s*us|tbs|bally|msg|yes\s*network|nesn|altitude|marquee)\b", re.IGNORECASE),
        None,
        "🇺🇸"
    ),
    # 4. Spain
    (
        re.compile(r"\b(es|spain|espana|movistar|laliga\s*tv|gol\s*play)\b", re.IGNORECASE),
        None,
        "🇪🇸"
    ),
    # 5. France
    (
        re.compile(r"\b(fr|france|canal\+|rmc\s*sport|beIN\s*sports?\s*fr)\b", re.IGNORECASE),
        None,
        "🇫🇷"
    ),
    # 6. Germany / Austria
    (
        re.compile(r"\b(de|germany|deutschland|austria|zdf|ard)\b", re.IGNORECASE),
        None,
        "🇩🇪"
    ),
    # 7. Canada
    (
        re.compile(r"\b(ca|canada|tsn|sportsnet)\b", re.IGNORECASE),
        None,
        "🇨🇦"
    ),
    # 8. Russia
    (
        re.compile(r"\b(ru|russia|match!|match\s*tv|match\s*premier|match\s*football|okko)\b", re.IGNORECASE),
        None,
        "🇷🇺"
    ),
    # 9. Poland
    (
        re.compile(r"\b(pl|poland|polska|polsat|canal\+\s*sport\s*pl|tvp\s*sport)\b", re.IGNORECASE),
        None,
        "🇵🇱"
    ),
    # 10. Netherlands
    (
        re.compile(r"\b(nl|netherlands|holland|ziggo)\b", re.IGNORECASE),
        None,
        "🇳🇱"
    ),
    # 11. Portugal
    (
        re.compile(r"\b(pt|portugal|sport\s*tv\s*pt)\b", re.IGNORECASE),
        None,
        "🇵🇹"
    ),
    # 12. Brazil
    (
        re.compile(r"\b(br|brazil|brasil|globo|sportv|premiere)\b", re.IGNORECASE),
        None,
        "🇧🇷"
    ),
    # 13. Turkey
    (
        re.compile(r"\b(tr|turkey|turkiye|tivibu|s\s*sport|a\s*spor)\b", re.IGNORECASE),
        None,
        "🇹🇷"
    ),
    # 14. Croatia
    (
        re.compile(r"\b(hr|croatia|hrvatska)\b", re.IGNORECASE),
        None,
        "🇭🇷"
    ),
    # 15. Serbia
    (
        re.compile(r"\b(rs|serbia|srbija)\b", re.IGNORECASE),
        None,
        "🇷🇸"
    ),
    # 16. Greece
    (
        re.compile(r"\b(gr|greece|cosmote|novasports)\b", re.IGNORECASE),
        None,
        "🇬🇷"
    ),
    # 17. Romania
    (
        re.compile(r"\b(ro|romania|digi\s*sport|prima\s*sport)\b", re.IGNORECASE),
        None,
        "🇷🇴"
    ),
    # 18. Arabic / Saudi / UAE
    (
        re.compile(r"\b(ar|arabic|uae|saudi|ssc|alkass)\b", re.IGNORECASE),
        None,
        "🇸🇦"
    ),
    # 19. Australia
    (
        re.compile(r"\b(au|australia|kayo|stan\s*sport|optus)\b", re.IGNORECASE),
        None,
        "🇦🇺"
    ),
]

class StreamService:
    """
    Resolves live streams (DaddyLive/Italian channels + Streamed sources) and post-match replays/highlights.
    """

    def detect_host(self, embed_url: str) -> str:
        """Detects the appropriate EasyProxy extractor host from the embed URL."""
        url_lower = embed_url.lower()
        if "embed.st" in url_lower or "embedstream" in url_lower:
            return "embedst"
        elif "cdnlivetv.tv" in url_lower or "cdnlive" in url_lower:
            return "cdnlive"
        elif "vixsrc" in url_lower:
            return "vixsrc"
        elif "dlstreams" in url_lower:
            return "dlstreams"
        elif "freeshot" in url_lower:
            return "freeshot"
        elif "streamhg" in url_lower:
            return "streamhg"
        elif "sports99" in url_lower:
            return "sports99"
        elif "sportsonline" in url_lower:
            return "sportsonline"
        elif "livetv" in url_lower:
            return "livetv"
        elif "fastream" in url_lower:
            return "fastream"
        elif "vavoo" in url_lower:
            return "vavoo"
        elif "dood" in url_lower:
            return "doodstream"
        elif "filemoon" in url_lower:
            return "filemoon"
        elif "mixdrop" in url_lower:
            return "mixdrop"
        elif "supervideo" in url_lower:
            return "supervideo"
        return "generic"

    def get_channel_flag(self, ch_name: str) -> str:
        """Determines the country flag emoji for a channel name using strict regex boundaries."""
        if not ch_name:
            return ""
        for include_rx, exclude_rx, flag in CHANNEL_COUNTRY_PATTERNS:
            if exclude_rx and exclude_rx.search(ch_name):
                continue
            if include_rx.search(ch_name):
                return flag
        return ""

    def get_flag_for_language(self, lang_text: str) -> str:
        """Finds flag emoji for a given language string."""
        lang_lower = (lang_text or "").lower().strip()
        if lang_lower in STREAMED_LANG_MAP:
            return STREAMED_LANG_MAP[lang_lower][0]
        return self.get_channel_flag(lang_text)

    def build_easyproxy_url(self, ep_url: str, ep_pass: Optional[str], host: str, destination_url: str) -> str:
        """
        Builds the target EasyProxy extractor URL:
        {ep_url}/extractor/video.m3u8?host={host}&d={url_encoded}&redirect_stream=true[&api_password={ep_pass}]
        """
        base = ep_url.rstrip("/")
        encoded_d = urllib.parse.quote(destination_url, safe="")
        url = f"{base}/extractor/video.m3u8?host={host}&d={encoded_d}&redirect_stream=true"
        if ep_pass:
            url += f"&api_password={urllib.parse.quote(ep_pass)}"
        return url

    def generate_status_card(self, match: Optional[Dict[str, Any]], user_tz: Optional[str] = None) -> List[Dict[str, Any]]:
        """Generates an informative placeholder card with countdown or match status."""
        if not match:
            return [{
                "name": "Nessun flusso disponibile",
                "title": "Evento non trovato o rimosso dai provider.",
                "url": "",
                "behaviorHints": {"notWebReady": True},
            }]

        date_ms = match.get("date", 0)
        if not date_ms:
            return [{
                "name": "Nessun flusso attivo",
                "title": "Nessuna sorgente video attiva al momento. Riprova più tardi.",
                "url": "",
                "behaviorHints": {"notWebReady": True},
            }]

        now_ms = time.time() * 1000
        diff_mins = int((date_ms - now_ms) / 60000)

        from app.services.catalog_service import catalog_service
        start_time_str = catalog_service.format_event_date(date_ms, user_tz)

        if diff_mins > 1440:
            days = diff_mins // 1440
            hours = (diff_mins % 1440) // 60
            time_left = f"{days}g {hours}h" if hours else f"{days} giorni"
            return [{
                "name": f"⏳ Inizia tra {time_left}",
                "title": f"Inizio: {start_time_str} • I flussi saranno disponibili 20 minuti prima dell'inizio",
                "url": "",
                "behaviorHints": {"notWebReady": True},
            }]
        elif diff_mins > 60:
            hours = diff_mins // 60
            mins = diff_mins % 60
            time_left = f"{hours}h {mins}m" if mins else f"{hours}h"
            return [{
                "name": f"⏳ Inizia tra {time_left}",
                "title": f"Inizio: {start_time_str} • I flussi saranno disponibili 20 minuti prima dell'inizio",
                "url": "",
                "behaviorHints": {"notWebReady": True},
            }]
        elif diff_mins > 20:
            return [{
                "name": f"⏳ Inizia tra ~{diff_mins} min",
                "title": f"Inizio: {start_time_str} • I flussi saranno disponibili 20 minuti prima dell'inizio",
                "url": "",
                "behaviorHints": {"notWebReady": True},
            }]
        elif diff_mins > 0:
            return [{
                "name": "⏳ Inizio imminente (Caricamento flussi)",
                "title": f"Inizio: {start_time_str} • I flussi sono in fase di attivazione sui server, riprova a breve",
                "url": "",
                "behaviorHints": {"notWebReady": True},
            }]
            live_window = CatalogService.get_live_window_minutes(match) if match else 240
            if diff_mins >= -live_window:
                return [{
                    "name": "🔴 Partita in corso (Nessun flusso)",
                    "title": f"Iniziata alle {start_time_str} • Nessuna sorgente attiva al momento",
                    "url": "",
                    "behaviorHints": {"notWebReady": True},
                }]
            else:
                return [{
                    "name": "🏁 Evento Terminato",
                    "title": f"Questa partita si è conclusa (iniziata il {start_time_str})",
                    "url": "",
                    "behaviorHints": {"notWebReady": True},
                }]

    async def get_streams_for_event(
        self,
        item_id: str,
        ep_url: Optional[str],
        ep_pass: Optional[str] = None,
        user_tz: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Resolves streams for an event ID and formats them for Stremio.
        """
        if not ep_url:
            return [
                {
                    "name": "⚠️ EasyProxy non configurato",
                    "title": "Configura l'URL del tuo EasyProxy nel pannello dell'addon.",
                    "url": "",
                    "description": "Apri il pannello /configure per impostare l'URL del proxy.",
                    "behaviorHints": {"notWebReady": True},
                }
            ]

        # Extract slug/ID
        if ":" in item_id:
            _, slug_id = item_id.split(":", 1)
        else:
            slug_id = item_id

        # 1. Check Catalog in-memory cache first (contains merged sources from both providers!)
        match = None
        from app.services.catalog_service import catalog_service
        if catalog_service._cached_matches:
            for m in catalog_service._cached_matches:
                mid = str(m.get("id", ""))
                if mid == slug_id or slug_id in mid or mid.endswith(slug_id):
                    match = m
                    break

        # 2. Check SQLite DB (persisted merged sources)
        if not match:
            match = db_service.get_match_by_id(slug_id)

        # 3. Check StreamedAPI (fallback)
        if not match:
            match = await streamed_api.find_match_by_slug_and_id(slug_id)

        # 4. Check DaddyLive (fallback)
        if not match:
            dl_matches = await daddylive_api.get_matches()
            for m in dl_matches:
                if m.get("id") == slug_id or slug_id in str(m.get("id", "")):
                    match = m
                    break

        if not match:
            return []

        # Time-window check: 20 min before start, live_window minutes after start
        date_ms = match.get("date", 0)
        if date_ms:
            now_ms = time.time() * 1000
            diff_mins = int((date_ms - now_ms) / 60000)
            live_window = CatalogService.get_live_window_minutes(match)

            # Pre-match: more than 20 minutes before start -> show countdown card
            if diff_mins > 20:
                return self.generate_status_card(match, user_tz)

            # Concluded match: more than live_window minutes after start -> highlights & full match replays
            if diff_mins < -live_window:
                if not ENABLE_REPLAYS:
                    return [{
                        "name": "🏁 Evento Concluso",
                        "title": "Questo evento si è concluso. Lo streaming in diretta è terminato.",
                        "url": "",
                        "behaviorHints": {"notWebReady": True},
                    }]

                title = match.get("title", "")
                category = match.get("category", "")
                recap_tasks = [
                    youtube_service.get_highlight_streams(title, base_url=base_url),
                    dailymotion_service.get_highlight_streams(title, base_url=base_url),
                    sportvideo_service.get_replay_streams(title, category=category, event_date_ms=date_ms),
                ]
                recap_results = await asyncio.gather(*recap_tasks, return_exceptions=True)
                recap_streams: List[Dict[str, Any]] = []
                for res in recap_results:
                    if isinstance(res, list):
                        recap_streams.extend(res)

                if recap_streams:
                    return recap_streams

                return [{
                    "name": "🏁 Evento Concluso",
                    "title": "Nessuna sintesi o replay ancora disponibile online per questo evento.",
                    "url": "",
                    "behaviorHints": {"notWebReady": True},
                }]

        # Match is LIVE or within 20 minutes before start!
        final_streams: List[Dict[str, Any]] = []
        sources = match.get("sources", [])

        # 1. Process TvVoo (Vavoo) streams with top priority
        tvvoo_streams: List[Dict[str, Any]] = []
        tvvoo_sources = [s for s in sources if s.get("source") == "tvvoo"]
        seen_tvvoo_channels: Dict[str, int] = {}
        for s in tvvoo_sources:
            raw_name = s.get("name") or "Canale TV"
            vavoo_url = s.get("url")
            if not vavoo_url:
                continue

            seen_tvvoo_channels[raw_name] = seen_tvvoo_channels.get(raw_name, 0) + 1
            count = seen_tvvoo_channels[raw_name]
            name_label = f"🇮🇹 {raw_name}" if count == 1 else f"🇮🇹 {raw_name} (Server {count})"

            stream_url = self.build_easyproxy_url(ep_url, ep_pass, "Vavoo", vavoo_url)
            tvvoo_streams.append({
                "name": name_label,
                "title": "⚡ Fonte: TvVoo • Qualità FHD 1080p",
                "url": stream_url,
                "behaviorHints": {"notWebReady": False},
            })

        # 2. Process DaddyLive (dlhd) streams with StreamViX / MediaFlow Proxy format
        dlhd_streams: List[Dict[str, Any]] = []
        dlhd_sources = [s for s in sources if s.get("source") == "dlhd"]
        dl_domain = await daddylive_api.get_active_domain() if dlhd_sources else "dlive.sx"
        for idx, s in enumerate(dlhd_sources, 1):
            ch_id = s.get("id")
            ch_name = s.get("name") or f"Canale {idx}"
            flag = self.get_channel_flag(ch_name)
            flag_prefix = f"{flag} " if flag else ""

            # Exact StreamViX destination URL and EasyProxy parameters: host=DLHD, redirect_stream=true
            d_url = f"https://{dl_domain}/watch.php?id={ch_id}"
            stream_url = self.build_easyproxy_url(ep_url, ep_pass, "DLHD", d_url)

            dlhd_streams.append({
                "name": f"{flag_prefix}{ch_name}",
                "title": "📡 Fonte: DaddyLive • Qualità: Live TV",
                "url": stream_url,
                "behaviorHints": {"notWebReady": False},
            })

        # 3. Process Streamed upstream sources (hotel, delta, etc.)
        streamed_streams: List[Dict[str, Any]] = []
        other_sources = [(s.get("source"), s.get("id")) for s in sources if s.get("source") not in ("dlhd", "tvvoo") and s.get("source") and s.get("id")]
        if other_sources:
            tasks = [streamed_api.get_streams_for_source(src_name, src_id) for src_name, src_id in other_sources]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            stream_index = 1
            for res in results:
                if isinstance(res, list):
                    for stream_info in res:
                        embed_url = stream_info.get("embedUrl")
                        if not embed_url:
                            continue

                        host = self.detect_host(embed_url)
                        stream_url = self.build_easyproxy_url(ep_url, ep_pass, host, embed_url)

                        raw_lang = (stream_info.get("language") or "en").lower().strip()
                        flag, lang_label = STREAMED_LANG_MAP.get(raw_lang, ("", raw_lang.capitalize()))
                        flag_prefix = f"{flag} " if flag else ""

                        hd = " [HD]" if stream_info.get("hd") else ""
                        name_display = f"{flag_prefix}Stream #{stream_index}{hd}"

                        desc_parts = []
                        if lang_label:
                            desc_parts.append(f"{flag_prefix}Lingua: {lang_label}".strip())
                        if stream_info.get("hd"):
                            desc_parts.append("Risoluzione: HD")
                        desc_header = " • ".join(desc_parts)

                        title_display = f"{desc_header}\n📡 Fonte: Streamed" if desc_header else "📡 Fonte: Streamed"

                        streamed_streams.append({
                            "name": name_display,
                            "title": title_display,
                            "url": stream_url,
                            "behaviorHints": {
                                "notWebReady": False,
                            },
                        })
                        stream_index += 1

        # Sort non-TvVoo streams: Italian DaddyLive channels immediately below TvVoo
        secondary_streams = dlhd_streams + streamed_streams
        secondary_streams.sort(key=lambda s: 0 if "🇮🇹" in s.get("name", "") else 1)

        # Assemble final stream list: TvVoo strictly first!
        final_streams = tvvoo_streams + secondary_streams

        if not final_streams:
            return []

        return final_streams


stream_service = StreamService()
