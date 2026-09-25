import logging
import ssl
import time
import httpx
from typing import Dict, Optional, Tuple

logger = logging.getLogger("easysports.doh")

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
        # Insecure SSL context for upstream routing
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE

        # In-memory image cache: url -> (content_bytes, content_type, expire_timestamp)
        self._image_cache: Dict[str, Tuple[bytes, str, float]] = {}
        self._image_cache_ttl = 86400  # 24 hours
        self._image_cache_max = 600

        # Shared singleton transport and client with expanded connection pooling
        self._limits = httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=60.0)
        self._transport = httpx.AsyncHTTPTransport(verify=self._ssl_context, limits=self._limits)
        self._client = httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(8.0, connect=4.0))

    async def close(self):
        """Closes the underlying shared HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def resolve(self, hostname: str) -> Optional[str]:
        """Resolves hostname to IPv4 using DoH resolvers with TTL caching."""
        now = time.time()
        if hostname in self._dns_cache:
            ip, expire_at = self._dns_cache[hostname]
            if now < expire_at:
                return ip

        for endpoint in self._doh_endpoints:
            try:
                params = {"name": hostname, "type": "A"}
                headers = {"accept": "application/dns-json"}
                res = await self._client.get(endpoint, params=params, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    answers = data.get("Answer", [])
                    for ans in answers:
                        if ans.get("type") == 1 and "data" in ans:
                            ip = ans["data"]
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
                return cached_data, cached_type
            else:
                del self._image_cache[url]

        parsed = httpx.URL(url)
        hostname = parsed.host

        resolved_ip = await self.resolve(hostname)
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
                # Save to cache and manage capacity
                if len(self._image_cache) >= self._image_cache_max:
                    # Remove oldest 50 items
                    keys_to_remove = list(self._image_cache.keys())[:50]
                    for k in keys_to_remove:
                        self._image_cache.pop(k, None)
                self._image_cache[url] = (content, content_type, now + self._image_cache_ttl)
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
                        self._image_cache[url] = (content, content_type, now + self._image_cache_ttl)
                        return content, content_type
                except Exception:
                    pass
            logger.error("Failed raw request to %s: %s", url, e)

        return None, None

    async def proxy_image(self, url: str) -> Tuple[Optional[bytes], Optional[str]]:
        """Proxies an image URL and returns (content_bytes, content_type)."""
        return await self.get_raw(url)

doh_client = DoHClient()
