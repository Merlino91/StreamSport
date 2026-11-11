import asyncio
from collections import OrderedDict
import ipaddress
import logging
import socket
import ssl
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple
import httpcore
import httpx

logger = logging.getLogger("streamsport.doh")


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
        self._doh_client_loop: Optional[asyncio.AbstractEventLoop] = None

        # 2. Image / Upstream Cache & Limits
        # In-memory image cache with authentic LRU eviction: url -> (content_bytes, content_type, expire_timestamp)
        self._image_cache: OrderedDict[str, Tuple[bytes, str, float]] = OrderedDict()
        self._image_cache_ttl = 86400  # 24 hours
        self._image_cache_max = 400

        # Shared singleton DoH transport and client with strict TLS + DoH SNI bypass
        self._limits = httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=60.0)
        self._transport = self.create_transport(verify=True)
        self._client = httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(8.0, connect=4.0))
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        self._host_locks: Dict[str, asyncio.Lock] = {}
        self._host_locks_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_host_lock(self, hostname: str) -> asyncio.Lock:
        """Returns a per-hostname asyncio.Lock attached to the current running event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._host_locks_loop != current_loop:
            self._host_locks.clear()
            self._host_locks_loop = current_loop

        if hostname not in self._host_locks:
            self._host_locks[hostname] = asyncio.Lock()
        return self._host_locks[hostname]

    def _ensure_doh_client(self) -> httpx.AsyncClient:
        """Returns the active DoH client, recreating it if the previous event loop was closed."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._doh_client_loop is None:
            self._doh_client_loop = current_loop
        elif self._doh_client_loop != current_loop or getattr(self._doh_client, "is_closed", False):
            self._doh_client = httpx.AsyncClient(
                verify=True,
                limits=self._doh_limits,
                timeout=httpx.Timeout(6.0, connect=3.0)
            )
            self._doh_client_loop = current_loop
        return self._doh_client

    def _ensure_client(self) -> httpx.AsyncClient:
        """Returns the active upstream client, recreating it if the previous event loop was closed."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._client_loop is None:
            self._client_loop = current_loop
        elif self._client_loop != current_loop or getattr(self._client, "is_closed", False):
            self._transport = self.create_transport(verify=True)
            self._client = httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(8.0, connect=4.0))
            self._client_loop = current_loop
        return self._client

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
        """Resolves hostname to IPv4 using DoH resolvers with TTL caching and per-hostname lock."""
        if not hostname:
            return None

        # Immediate bypass for IP literals (IPv4, IPv6)
        parsed_ip = parse_ip_safe(hostname)
        if parsed_ip is not None:
            ip_str = str(parsed_ip)
            return None if is_ip_private_or_reserved(ip_str) else ip_str

        now = time.time()
        if hostname in self._dns_cache:
            ip, expire_at = self._dns_cache[hostname]
            if now < expire_at:
                return ip

        host_lock = self._get_host_lock(hostname)
        async with host_lock:
            # Re-check cache after acquiring lock
            now = time.time()
            if hostname in self._dns_cache:
                ip, expire_at = self._dns_cache[hostname]
                if now < expire_at:
                    return ip

            doh_http = self._ensure_doh_client()
            for endpoint in self._doh_endpoints:
                try:
                    params = {"name": hostname, "type": "A"}
                    headers = {"accept": "application/dns-json"}
                    res = await doh_http.get(endpoint, params=params, headers=headers)
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
        Executes an HTTP GET request resolving host via DoH with full TLS SNI and cert verification.
        """
        parsed = httpx.URL(url)
        hostname = parsed.host

        # Prime DoH cache and guard against private IPs before transport connect
        if hostname:
            await self.resolve(hostname)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }
        if host_header and host_header != hostname:
            headers["Host"] = host_header

        try:
            http_cli = self._ensure_client()
            response = await http_cli.get(url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                return response.json()
            logger.warning("Request to %s returned status %d", url, response.status_code)
        except Exception as e:
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

        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
        extra_h = headers or custom_headers
        if extra_h:
            req_headers.update(extra_h)

        if host_header and host_header != hostname:
            req_headers["Host"] = host_header

        try:
            http_cli = self._ensure_client()
            response = await http_cli.get(url, headers=req_headers, timeout=timeout)
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "image/webp")
                content = response.content
                self._save_to_image_cache(url, content, content_type, now)
                return content, content_type
            if response.status_code in (301, 302, 307, 308):
                redirect_loc = response.headers.get("location")
                if redirect_loc:
                    if redirect_loc.startswith("/"):
                        redirect_loc = f"{parsed.scheme}://{hostname}{redirect_loc}"
                    is_safe, _ = await self.is_safe_image_target_async(redirect_loc)
                    if is_safe and redirect_loc != url:
                        return await self.get_raw(redirect_loc, headers=headers, custom_headers=custom_headers, timeout=timeout)
            logger.warning("Raw request to %s returned status %d", url, response.status_code)
        except Exception as e:
            logger.error("Failed raw request to %s: %s", url, e)

        return None, None

    @staticmethod
    def _build_insecure_ssl_context() -> ssl.SSLContext:
        """Creates an unverified SSL context only when explicitly requested by a scraper."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def create_transport(self, verify: bool = True, **kwargs) -> "DoHAsyncHTTPTransport":
        """Creates a DoHAsyncHTTPTransport attached to this DoHClient."""
        ssl_ctx: Any = True if verify else self._build_insecure_ssl_context()
        return DoHAsyncHTTPTransport(verify=ssl_ctx, limits=self._limits, doh_resolver=self, **kwargs)

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


class DoHNetworkBackend(httpcore.AnyIOBackend):
    """
    Asynchronous network backend that intercepts connect_tcp to resolve
    hostnames via DoHClient before opening TCP sockets. Preserves TLS SNI
    and Host headers across reverse proxies and CDNs (e.g. Cloudflare).
    """

    def __init__(self, doh_resolver: Optional["DoHClient"] = None):
        super().__init__()
        self._doh_resolver = doh_resolver

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        resolver = self._doh_resolver or doh_client
        try:
            resolved_ip = await resolver.resolve(host)
        except Exception:
            resolved_ip = None

        target_host = resolved_ip if resolved_ip else host
        return await super().connect_tcp(
            target_host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class DoHAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """
    HTTPX Async Transport backed by DoHNetworkBackend.
    Ensures all TCP connections bypass ISP DNS blocking while keeping
    valid TLS SNI negotiation for Cloudflare / DDOS-guarded domains.
    """

    def __init__(self, *args, doh_resolver: Optional["DoHClient"] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pool._network_backend = DoHNetworkBackend(doh_resolver=doh_resolver)


doh_client = DoHClient()
