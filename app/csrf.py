"""App-weiter CSRF-Schutz über Fetch-Metadata (Sec-Fetch-Site) + Origin-Host-Check.

Zweite Verteidigungsschicht neben SameSite=Lax: mutierende Requests, die ein
Browser erkennbar cross-site abschickt, werden mit 403 abgewiesen. Beide Signale
müssen sauber sein — ein mismatchender Origin blockt auch bei erlaubtem
Sec-Fetch-Site und umgekehrt. Requests ohne jedes Browser-Signal (curl, Scripte,
Tests) passieren im Default — CSRF ist ein Problem von Ambient-Browser-
Credentials, nicht von Clients, die ihre Cookies selbst mitbringen; wer auch
diese Klasse sperren will (Alt-Browser-Restrisiko), setzt CSRF_STRICT=true.

Bewusst KEIN scheme-Vergleich: hinter einem TLS-terminierenden Reverse-Proxy ist
der Origin ``https://…``, die interne Sicht aber ``http://…`` — verglichen wird
nur Host[:Port]. Die Downgrade-Kante (http-Origin auf https-App) fängt bei
modernen Browsern der Sec-Fetch-Site-Check (``same-site`` → Block); eine
X-Forwarded-Proto-Abhängigkeit würde die Proxy-Fragilität wieder einführen,
die der Host-only-Vergleich gerade vermeidet.
"""
import logging

from urllib.parse import urlsplit

from .config import settings

log = logging.getLogger("sammelmappe.csrf")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Sec-Fetch-Site-Werte, die eine Mutation auslösen dürfen: same-origin (App selbst)
# und none (direkte Nutzer-Navigation). same-site (andere Subdomain) ist bewusst
# geblockt — die App hat keine Subdomain-Integration.
_ALLOWED_FETCH_SITES = {"same-origin", "none"}


def _normalize_host(value: str) -> str:
    """Lowercase + Default-Ports strippen, damit ``example.com:443`` und
    ``example.com`` als derselbe Host gelten (der externe scheme ist hinter
    einem Proxy nicht zuverlässig bekannt, daher beide Default-Ports)."""
    host = value.strip().lower().rstrip(".")
    if "://" in host:
        # Volle Origins ("https://alias.example") in der Config tolerieren.
        try:
            host = urlsplit(host).netloc
        except ValueError:
            return ""
    for default_port in (":443", ":80"):
        if host.endswith(default_port):
            host = host[: -len(default_port)]
    return host


def _origin_host(origin: str) -> str | None:
    """Host[:Port] aus einem Origin-Header-Wert, None wenn unparsebar."""
    try:
        netloc = urlsplit(origin.strip()).netloc
    except ValueError:
        return None
    return _normalize_host(netloc) or None


def trusted_hosts(headers, extra_trusted: set[str], trust_xfh: bool) -> set[str]:
    """Hosts, gegen die ein Origin gematcht werden darf: Host, konfigurierte
    CSRF_TRUSTED_ORIGINS, und — NUR wenn der Request nachweislich vom
    konfigurierten Reverse-Proxy kommt (trusted_proxies, wie beim
    Rate-Limit-X-Forwarded-For) — X-Forwarded-Host (erster Eintrag)."""
    hosts = {_normalize_host(h) for h in extra_trusted}
    if trust_xfh:
        forwarded = headers.get("x-forwarded-host")
        if forwarded:
            hosts.add(_normalize_host(forwarded.split(",")[0]))
    host = headers.get("host")
    if host:
        hosts.add(_normalize_host(host))
    hosts.discard("")
    return hosts


def csrf_block_reason(
    method: str,
    headers,
    extra_trusted: set[str] | None = None,
    trust_xfh: bool = False,
    strict: bool = False,
) -> str | None:
    """None = Request darf passieren, sonst der Block-Grund (für Log + Response).

    ``headers`` braucht nur ``.get(name_lowercase)`` (Starlette-Headers, dict).
    Beide Browser-Signale werden geprüft, wenn vorhanden — das schwächere darf
    das stärkere nicht überstimmen.
    """
    if method.upper() in SAFE_METHODS:
        return None

    origin = headers.get("origin")
    origin_ok = False
    if origin:
        origin = origin.strip()
        if origin.lower() == "null":
            return "Origin=null"
        origin_host = _origin_host(origin)
        if origin_host is None:
            # !r: escaped Steuerzeichen — der Wert landet im Warning-Log.
            return f"Origin unparsebar ({origin[:100]!r})"
        if origin_host not in trusted_hosts(headers, extra_trusted or set(), trust_xfh):
            return f"Origin-Host {origin_host} passt nicht zum Request-Host"
        origin_ok = True

    fetch_site = headers.get("sec-fetch-site")
    if fetch_site:
        if fetch_site.strip().lower() in _ALLOWED_FETCH_SITES:
            return None
        return f"Sec-Fetch-Site={fetch_site.strip()}"

    if origin_ok:
        return None

    # Weder Sec-Fetch-Site noch Origin: im Default passieren lassen (curl,
    # Scripte, alte Clients — kein Ambient-Credential-Szenario). CSRF_STRICT
    # sperrt auch das, für Betreiber, die das Alt-Browser-Restrisiko (Browser
    # ohne Origin-Header UND ohne SameSite-Enforcement) ausschließen wollen.
    if strict:
        return "kein Browser-Herkunfts-Signal (CSRF_STRICT aktiv)"
    return None


def csrf_extra_trusted() -> set[str]:
    """CSRF_TRUSTED_ORIGINS aus der Config (komma-separierte Hostnamen oder
    volle Origins) — Escape-Hatch für Proxys, die weder Host noch
    X-Forwarded-Host auf den öffentlichen Hostnamen setzen."""
    return {h.strip() for h in settings.csrf_trusted_origins.split(",") if h.strip()}
