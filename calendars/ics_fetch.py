"""외부 ICS 피드를 안전하게 가져온다.

사용자가 임의의 URL을 등록하는 구조라 SSRF가 가장 큰 위험이다. 서버는 사설망
안에 있고 클라우드 메타데이터 주소(169.254.169.254)에도 닿을 수 있으므로,
"URL을 받아서 그냥 요청"하면 안 된다. 방어는 다음과 같다.

1. https만 허용 (webcal://은 등록 시 https로 정규화). 등록 때와 fetch 때 모두 검사
2. 호스트를 직접 DNS 조회해서 **응답된 모든 IP**가 공인 IP인지 검사
   (라운드로빈에 사설 IP를 섞는 우회 차단)
3. 검사한 IP로 직접 연결하고 SNI·인증서 검증만 원래 호스트명으로 한다
   → 검사 후 DNS 응답이 바뀌는 리바인딩 차단
4. 리다이렉트를 자동으로 따라가지 않고, hop마다 1~3을 다시 적용한다
5. 응답 크기·시간 상한 (1 GB 서버를 거대 파일로 죽이지 못하게)
"""

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit, urlunsplit

TIMEOUT_SECONDS = 10
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
USER_AGENT = "dodamthepig-calendar/1.0 (+https://cal.dodamthepig.duckdns.org)"

REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class SourceError(Exception):
    """소스 URL이 거부되었거나 가져오기에 실패했다."""


def normalize_url(raw):
    """등록 시점 정규화. webcal://은 https://로 바꾸고, https가 아니면 거부한다.

    아이클라우드·구글이 주는 구독 링크가 webcal:// 형태인 경우가 많은데,
    실체는 https라서 스킴만 바꿔주면 된다.
    """
    if not raw or not raw.strip():
        raise SourceError("URL이 비어 있습니다.")
    url = raw.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() in ("webcal", "webcals"):
        parts = parts._replace(scheme="https")
        url = urlunsplit(parts)
    if parts.scheme.lower() != "https":
        raise SourceError(
            f"https만 허용됩니다 (받은 스킴: {parts.scheme or '없음'}). "
            "webcal:// 주소는 자동으로 https로 바꿔 드립니다."
        )
    if not parts.hostname:
        raise SourceError("URL에 호스트명이 없습니다.")
    return url


def _check_ip(value):
    """공인 IP가 아니면 거부한다."""
    ip = ipaddress.ip_address(value)
    # ::ffff:10.0.0.1 같은 IPv4-mapped 주소로 우회하는 것을 막는다.
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (
        ip.is_private  # 10/8, 172.16/12, 192.168/16, 127/8, 169.254/16 …
        or ip.is_loopback
        or ip.is_link_local  # 169.254.169.254 (클라우드 메타데이터) 포함
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise SourceError(f"사설·특수 IP 주소는 허용되지 않습니다: {ip}")
    return ip


def _resolve_public(host, port):
    """호스트를 조회하고 모든 결과 IP를 검사한 뒤 연결 대상을 돌려준다."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SourceError(f"DNS 조회 실패: {host} ({exc.strerror})") from exc
    if not infos:
        raise SourceError(f"DNS 결과가 없습니다: {host}")
    targets = []
    for family, _type, _proto, _canon, sockaddr in infos:
        _check_ip(sockaddr[0])  # 하나라도 사설이면 전체를 거부한다
        targets.append((family, sockaddr))
    return targets


def _request_once(url):
    """검사된 IP로 직접 연결해 한 번 요청한다. 리다이렉트는 따라가지 않는다."""
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise SourceError(f"https만 허용됩니다: {parts.scheme}")
    host = parts.hostname
    if not host:
        raise SourceError("URL에 호스트명이 없습니다.")
    port = parts.port or 443
    family, sockaddr = _resolve_public(host, port)[0]

    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT_SECONDS)
    try:
        sock.connect(sockaddr)
        context = ssl.create_default_context()
        # 연결은 검사한 IP로, 인증서 검증은 원래 호스트명으로.
        tls_sock = context.wrap_socket(sock, server_hostname=host)
    except (OSError, ssl.SSLError) as exc:
        sock.close()
        raise SourceError(f"연결 실패: {exc}") from exc

    conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT_SECONDS)
    conn.sock = tls_sock  # 이미 만든 소켓을 쓰므로 conn이 다시 DNS를 보지 않는다
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    try:
        conn.request(
            "GET",
            target,
            headers={
                "Host": parts.netloc,
                "User-Agent": USER_AGENT,
                "Accept": "text/calendar, text/plain, */*",
                "Accept-Encoding": "identity",
            },
        )
        return conn, conn.getresponse()
    except (OSError, http.client.HTTPException) as exc:
        conn.close()
        raise SourceError(f"요청 실패: {exc}") from exc


def fetch_ics(url):
    """ICS 본문을 bytes로 돌려준다. 실패는 모두 SourceError."""
    current = normalize_url(url)
    for _hop in range(MAX_REDIRECTS + 1):
        conn, response = _request_once(current)
        try:
            if response.status in REDIRECT_STATUSES:
                location = response.headers.get("Location")
                if not location:
                    raise SourceError(f"HTTP {response.status}인데 Location이 없습니다.")
                # 다음 hop도 normalize_url + _resolve_public 검사를 다시 통과해야 한다.
                current = normalize_url(urljoin(current, location))
                continue
            if response.status != 200:
                raise SourceError(f"HTTP {response.status} {response.reason}")

            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_BYTES:
                raise SourceError(
                    f"응답이 너무 큽니다: {int(declared)} bytes (상한 {MAX_BYTES})"
                )
            # 상한보다 1바이트 더 읽어서 초과 여부를 판단한다.
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise SourceError(f"응답이 너무 큽니다: 상한 {MAX_BYTES} bytes 초과")
            if not body.lstrip().startswith(b"BEGIN:VCALENDAR"):
                raise SourceError("ICS 형식이 아닙니다 (BEGIN:VCALENDAR로 시작하지 않음).")
            return body
        except socket.timeout as exc:
            raise SourceError(f"{TIMEOUT_SECONDS}초 안에 응답이 없습니다.") from exc
        finally:
            conn.close()
    raise SourceError(f"리다이렉트가 {MAX_REDIRECTS}회를 넘었습니다.")
