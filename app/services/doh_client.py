import asyncio
from collections import OrderedDict
import ipaddress
import logging
import socket
import ssl
import time
import urllib.parse
from typing import Dict, Optional, Tuple
import httpx

logger = logging.getLogger("easysports.doh")


def is_ip_private_or_reserved(ip_str: str) -> bool:
    """Checks whether an IP address is private, loopback, link-local, multicast, or reserved."""
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
        if getattr(ip, "ipv4_mapped", None) and (
            ip.ipv4_mapped.is_private
            or ip.ipv4_mapped.is_loopback
            or ip.ipv4_mapped.is_link_local
            or ip.ipv4_mapped.is_reserved
        ):
            return True
        return False
    except ValueError:
        return True


def parse_ip_safe(host: str) -> Optional[ipaddress._BaseAddress]:
    """Attempts to parse standard, decimal, octal or hex IP addresses."""
    clean_host = host.strip().strip("[]")
    try:
        return ipaddress.ip_address(clean_host)
    except ValueError:
        pass
    if clean_host.isdigit():
        try:
            return ipaddress.ip_address(int(clean_host))
        except ValueError:
            pass
    try:
        raw_bytes = socket.inet_aton(clean_host)
        return ipaddress.ip_address(raw_bytes)
    except (OSError, ValueError):
        pass
    return None


class DoHClient:
    """
    HTTP Client with built-in DNS-over-HTTPS resolution to bypass local/ISP DNS blocks.
    Uses a single shared connection pool with strict memory and socket limits.
    """

    def __init__(self):
        self._dns_cache: Dict[str, Tuple[str, float]] = {}  # hostname -> (ip, expire_time)
        self._doh_endpoints = [
            "https://cloudflare-dns.com/dns-query",
            "https://dns.google/resolve",
        ]
        # 1. Secure DoH Client: Strict TLS/SSL verification for Cloudflare & Google DNS resolvers
        self._doh_limits = httpx.Limits(max_keepalive_connections=20, max_connections=40, keepalive_expiry=60.0)
        self._doh_client = httpx.AsyncClient(
            verify=True,
            limits=self._doh_limits,
            timeout=httpx.Timeout(6.0, connect=3.0)
        )

        # 2. Upstream Client: Direct IP / CDN fetching with custom SNI support
        # Allows routing via direct numerical IP when local ISP DNS blocks hostnames
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE

        # In-memory image cache with authentic LRU eviction: url -> (content_bytes, content_type, expire_timestamp)
        self._image_cache: OrderedDict[str, Tuple[bytes, str, float]] = OrderedDict()
        self._image_cache_ttl = 86400  # 24 hours
        self._image_cache_max = 400

        # Shared singleton transport and client with expanded connection pooling
        self._limits = httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=60.0)
        self._transport = httpx.AsyncHTTPTransport(verify=self._ssl_context, limits=self._limits)
        self._client = httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(8.0, connect=4.0))
        self._resolve_lock: asyncio.Lock = asyncio.Lock()

    def _save_to_image_cache(self, url: str, content: bytes, content_type: str, now: float) -> None:
        """Stores image in RAM with true LRU eviction (Least Recently Used) and bounded capacity."""
        if url in self._image_cache:
            self._image_cache.move_to_end(url)
        elif len(self._image_cache) >= self._image_cache_max:
            # Pop least recently used item (head of OrderedDict)
            self._image_cache.popitem(last=False)
        self._image_cache[url] = (content, content_type, now + self._image_cache_ttl)

    async def close(self):
        """Closes the underlying HTTP clients and clears image cache."""
        self._image_cache.clear()
        if hasattr(self, "_doh_client") and self._doh_client and not self._doh_client.is_closed:
            await self._doh_client.aclose()
        if hasattr(self, "_client") and self._client and not self._client.is_closed:
            await self._client.aclose()

    async def resolve(self, hostname: str) -> Optional[str]:
        """Resolves hostname to IPv4 using DoH resolvers with TTL caching and concurrency lock."""
        now = time.time()
        if hostname in self._dns_cache:
            ip, expire_at = self._dns_cache[hostname]
            if now < expire_at:
                return ip

        async with self._resolve_lock:
            # Re-check cache after acquiring lock
            now = time.time()
            if hostname in self._dns_cache:
                ip, expire_at = self._dns_cache[hostname]
                if now < expire_at:
                    return ip

            for endpoint in self._doh_endpoints:
                try:
                    params = {"name": hostname, "type": "A"}
                    headers = {"accept": "application/dns-json"}
                    res = await self._doh_client.get(endpoint, params=params, headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        answers = data.get("Answer", [])
                        for ans in answers:
                            if ans.get("type") == 1 and "data" in ans:
                                ip = ans["data"]
                                if is_ip_private_or_reserved(ip):
                                    logger.warning("DoH lookup blocked: %s resolved to private/reserved IP %s", hostname, ip)
                                    return None
                                ttl = ans.get("TTL", 300)
                                self._dns_cache[hostname] = (ip, now + min(ttl, 600))
                                logger.debug("DoH resolved %s -> %s (TTL: %ds)", hostname, ip, ttl)
                                return ip
                except Exception as e:
                    logger.warning("DoH lookup via %s failed for %s: %s", endpoint, hostname, e)

            return None

    async def get_json(self, url: str, host_header: Optional[str] = None, timeout: float = 8.0) -> Optional[dict]:
        """
        Executes an HTTP GET request resolving host via DoH using shared client.
        """
        parsed = httpx.URL(url)
        hostname = parsed.host

        resolved_ip = await self.resolve(hostname)
        target_url = url
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }

        if resolved_ip and resolved_ip != hostname:
            target_url = str(parsed.copy_with(host=resolved_ip))
            headers["Host"] = host_header or hostname

        try:
            response = await self._client.get(target_url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                return response.json()
            logger.warning("Request to %s returned status %d", url, response.status_code)
        except Exception as e:
            if target_url != url:
                try:
                    # Fallback to direct URL if SNI was rejected by target server
                    fallback_headers = {"User-Agent": headers["User-Agent"], "Accept": headers.get("Accept", "*/*")}
                    response = await self._client.get(url, headers=fallback_headers, timeout=timeout)
                    if response.status_code == 200:
                        return response.json()
                except Exception:
                    pass
            logger.error("Failed requesting %s: %s", url, e)

        return None

    @staticmethod
    def _check_safe_target_pre_dns(url: str) -> Tuple[bool, str]:
        """Performs fast URL scheme, hostname, and IP-literal validation before DNS lookup."""
        if not url or not isinstance(url, str):
            return False, "URL non valida o vuota"

        try:
            parsed = urllib.parse.urlsplit(url.strip())
        except Exception:
            return False, "Formato URL malformato"

        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            return False, f"Schema non consentito: '{scheme}' (ammessi solo http e https)"

        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            return False, "Hostname mancante nella richiesta"

        # Explicit forbidden hostnames
        if hostname in ("localhost", "0.0.0.0", "metadata.google.internal") or hostname.endswith(
            (".localhost", ".local", ".internal", ".lan")
        ):
            return False, f"Accesso all'host locale o interno vietato ({hostname})"

        # Check if hostname represents an IP literal (IPv4, IPv6, decimal, octal, hex)
        parsed_ip = parse_ip_safe(hostname)
        if parsed_ip is not None:
            if is_ip_private_or_reserved(str(parsed_ip)):
                return False, f"Accesso all'indirizzo IP privato o riservato vietato ({parsed_ip})"

        return True, "OK"

    @classmethod
    async def is_safe_image_target_async(cls, url: str) -> Tuple[bool, str]:
        """
        Asynchronously validates whether a target URL is safe to proxy (Anti-SSRF guard),
        offloading blocking system DNS resolution (socket.getaddrinfo) to a worker thread via asyncio.to_thread.
        """
        is_safe, reason = cls._check_safe_target_pre_dns(url)
        if not is_safe:
            return False, reason

        parsed = urllib.parse.urlsplit(url.strip())
        hostname = (parsed.hostname or "").strip().lower()

        # Verify system DNS resolution asynchronously without blocking the event loop
        try:
            addr_info = await asyncio.to_thread(socket.getaddrinfo, hostname, None, proto=socket.IPPROTO_TCP)
            for entry in addr_info:
                resolved_addr = entry[4][0]
                if is_ip_private_or_reserved(resolved_addr):
                    return False, f"L'hostname {hostname} risolve su un indirizzo interno vietato ({resolved_addr})"
        except Exception:
            # If system DNS resolution fails (e.g. ISP censorship), DoH will handle resolution
            pass

        return True, "OK"

    @classmethod
    def is_safe_image_target(cls, url: str) -> Tuple[bool, str]:
        """
        Synchronously validates whether a target URL is safe to proxy (Anti-SSRF guard).
        Maintained for synchronous callers and unit tests.
        """
        is_safe, reason = cls._check_safe_target_pre_dns(url)
        if not is_safe:
            return False, reason

        parsed = urllib.parse.urlsplit(url.strip())
        hostname = (parsed.hostname or "").strip().lower()

        # Verify system DNS resolution
        try:
            addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
            for entry in addr_info:
                resolved_addr = entry[4][0]
                if is_ip_private_or_reserved(resolved_addr):
                    return False, f"L'hostname {hostname} risolve su un indirizzo interno vietato ({resolved_addr})"
        except Exception:
            # If system DNS resolution fails (e.g. ISP censorship), DoH will handle resolution
            pass

        return True, "OK"

    async def get_raw(
        self,
        url: str,
        host_header: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        timeout: float = 8.0,
        **kwargs,
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Executes an HTTP GET request resolving host via DoH and returns raw bytes and content-type.
        Caches images in memory to prevent connection pool exhaustion during catalog browsing.
        """
        now = time.time()
        if url in self._image_cache:
            cached_data, cached_type, expire_at = self._image_cache[url]
            if now < expire_at:
                self._image_cache.move_to_end(url)
                return cached_data, cached_type
            else:
                del self._image_cache[url]

        parsed = httpx.URL(url)
        hostname = parsed.host
        if not hostname:
            return None, None

        # Verify host itself is not a private IP
        parsed_host_ip = parse_ip_safe(hostname)
        if parsed_host_ip and is_ip_private_or_reserved(str(parsed_host_ip)):
            logger.warning("Blocked raw request to private/reserved IP host: %s", hostname)
            return None, None

        resolved_ip = await self.resolve(hostname)
        if resolved_ip and is_ip_private_or_reserved(resolved_ip):
            logger.warning("Blocked raw request: %s resolved to private/reserved IP %s", hostname, resolved_ip)
            return None, None

        target_url = url
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
        extra_h = headers or custom_headers
        if extra_h:
            req_headers.update(extra_h)

        if resolved_ip and resolved_ip != hostname:
            target_url = str(parsed.copy_with(host=resolved_ip))
            req_headers["Host"] = host_header or hostname

        try:
            response = await self._client.get(target_url, headers=req_headers, timeout=timeout)
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "image/webp")
                content = response.content
                self._save_to_image_cache(url, content, content_type, now)
                return content, content_type
            logger.warning("Raw request to %s returned status %d", url, response.status_code)
        except Exception as e:
            if target_url != url:
                try:
                    fallback_headers = req_headers.copy()
                    fallback_headers.pop("Host", None)
                    response = await self._client.get(url, headers=fallback_headers, timeout=timeout)
                    if response.status_code == 200:
                        content_type = response.headers.get("content-type", "image/webp")
                        content = response.content
                        self._save_to_image_cache(url, content, content_type, now)
                        return content, content_type
                except Exception:
                    pass
            logger.error("Failed raw request to %s: %s", url, e)

        return None, None

    async def proxy_image(self, url: str) -> Tuple[Optional[bytes], Optional[str]]:
        """Proxies an image URL and returns (content_bytes, content_type) with anti-SSRF validation."""
        is_safe, reason = await self.is_safe_image_target_async(url)
        if not is_safe:
            logger.warning("Blocked unsafe image proxy request to %s: %s", url, reason)
            return None, None

        content, content_type = await self.get_raw(url)
        if content and content_type:
            # Enforce that only image content types are proxied
            clean_ct = content_type.split(";")[0].strip().lower()
            if not clean_ct.startswith("image/"):
                logger.warning("Rejected non-image Content-Type '%s' for URL %s", content_type, url)
                return None, None
            return content, content_type

        return None, None

doh_client = DoHClient()
