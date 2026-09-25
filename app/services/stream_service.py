import asyncio
import logging
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from app.services.daddylive_api import daddylive_api
from app.services.dailymotion_service import dailymotion_service
from app.services.db_service import db_service
from app.services.genre_classifier import genre_classifier
from app.services.sportvideo_service import sportvideo_service
from app.services.streamed_api import streamed_api
from app.services.youtube_service import youtube_service

logger = logging.getLogger("streamsport.stream")

# Flag mappings for languages
LANGUAGE_FLAGS = {
    "english": "🇬🇧",
    "en": "🇬🇧",
    "uk": "🇬🇧",
    "usa": "🇺🇸",
    "us": "🇺🇸",
    "italian": "🇮🇹",
    "italy": "🇮🇹",
    "it": "🇮🇹",
    "spanish": "🇪🇸",
    "spain": "🇪🇸",
    "es": "🇪🇸",
    "french": "🇫🇷",
    "france": "🇫🇷",
    "fr": "🇫🇷",
    "german": "🇩🇪",
    "germany": "🇩🇪",
    "de": "🇩🇪",
    "portuguese": "🇵🇹",
    "brazil": "🇧🇷",
    "pt": "🇵🇹",
    "canada": "🇨🇦",
    "ca": "🇨🇦",
    "dutch": "🇳🇱",
    "nl": "🇳🇱",
    "arabic": "🇸🇦",
    "ar": "🇸🇦",
}

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

    def get_flag_for_language(self, lang_text: str) -> str:
        """Finds flag emoji for a given language/channel string."""
        lang_lower = lang_text.lower()
        for key, flag in LANGUAGE_FLAGS.items():
            if key in lang_lower:
                return flag
        return ""

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
        elif diff_mins >= -240:
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
        Enforces time-window rules: hides streams until 20 min before start,
        and provides live streams or post-match highlights/replays.
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
            return self.generate_status_card(None, user_tz)

        # Time-window check: 20 min before start, 4 hours (240 min) after start
        date_ms = match.get("date", 0)
        if date_ms:
            now_ms = time.time() * 1000
            diff_mins = int((date_ms - now_ms) / 60000)

            # Pre-match: more than 20 minutes before start -> show countdown card
            if diff_mins > 20:
                return self.generate_status_card(match, user_tz)

            # Concluded match: more than 4 hours after start -> highlights & full match replays
            if diff_mins < -240:
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

        # Process DaddyLive (dlhd) streams with StreamViX / MediaFlow Proxy format
        dlhd_sources = [s for s in sources if s.get("source") == "dlhd"]
        dl_domain = await daddylive_api.get_active_domain() if dlhd_sources else "dlive.sx"
        for idx, s in enumerate(dlhd_sources, 1):
            ch_id = s.get("id")
            ch_name = s.get("name") or f"Canale {idx}"
            ch_upper = ch_name.upper()

            is_it = any(k in ch_upper for k in (" IT", "SKY SPORT", "DAZN", "RAI", "MEDIASET", "TV8", "SPORTITALIA"))
            flag_prefix = "[IT 🇮🇹] " if is_it else ""

            # Exact StreamViX destination URL and EasyProxy parameters: host=DLHD, redirect_stream=true
            d_url = f"https://{dl_domain}/watch.php?id={ch_id}"
            stream_url = self.build_easyproxy_url(ep_url, ep_pass, "DLHD", d_url)

            final_streams.append({
                "name": f"{flag_prefix}{ch_name}",
                "title": f"{flag_prefix}{ch_name}\n🚀 EasyProxy Playback",
                "url": stream_url,
                "behaviorHints": {"notWebReady": False},
            })

        # Process Streamed upstream sources (hotel, delta, etc.)
        other_sources = [(s.get("source"), s.get("id")) for s in sources if s.get("source") != "dlhd" and s.get("source") and s.get("id")]
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

                        lang = stream_info.get("language", "en")
                        flag = self.get_flag_for_language(lang)
                        flag_display = f"[{flag}] " if flag else ""

                        hd = " [HD]" if stream_info.get("hd") else ""
                        name_display = f"Stream #{stream_index}{hd}"
                        title_display = f"{flag_display}{name_display}\n🚀 EasyProxy Playback"

                        final_streams.append({
                            "name": f"[EasyProxy] {flag_display}Stream #{stream_index}",
                            "title": title_display,
                            "url": stream_url,
                            "behaviorHints": {
                                "notWebReady": False,
                            },
                        })
                        stream_index += 1

        if not final_streams:
            return self.generate_status_card(match, user_tz)

        return final_streams

stream_service = StreamService()
