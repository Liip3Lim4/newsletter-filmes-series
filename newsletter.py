#!/usr/bin/env python3
"""
Newsletter semanal de filmes e séries — dados do TMDB, envio via SMTP.
Só usa a biblioteca padrão do Python (nenhum pip install necessário).

Seções:
  CINEMA (corredor de cinema)
    - Em cartaz: estreias de filmes americanos nos cinemas do Brasil nos próximos dias
    - No streaming: filmes americanos que chegaram às plataformas nos últimos dias
      (o TMDB só sabe a plataforma depois que o filme entra nela)
  VIDEOLOCADORA (prateleira de fitas)
    - Novas temporadas e séries novas americanas
    - Doramas (Coreia, Japão, China, Taiwan, Tailândia): lançamentos e continuações
  Itens sem sinopse em português são descartados.

Variáveis de ambiente:
  TMDB_TOKEN        (obrigatória) API Read Access Token (v4) ou API Key (v3) do TMDB
  SMTP_HOST, SMTP_PORT (465 SSL / 587 STARTTLS), SMTP_USER, SMTP_PASSWORD
  MAIL_FROM         remetente (padrão: SMTP_USER)
  MAIL_TO           destinatários separados por vírgula (vão em Cco)
  LANGUAGE          padrão: pt-BR
  REGION            padrão: BR (datas de estreia e plataformas do país)
  MAIN_COUNTRY      país de origem das produções principais. Padrão: US
  DORAMA_COUNTRIES  padrão: KR,JP,CN,TW,TH
  WINDOW_DAYS       janela futura (cinema e séries). Padrão: 7
  STREAMING_LOOKBACK_DAYS  janela passada para o streaming. Padrão: 7
  MAX_MOVIES        filmes por seção. Padrão: 6
  MAX_SERIES        séries por seção. Padrão: 6
  MAX_DORAMAS       doramas por subseção. Padrão: 3
  EXCLUDE_TV_GENRES IDs TMDB ignorados. Padrão: notícias, reality, novela, talk show
  DRY_RUN           "1" = só gera newsletter.html, não envia
"""
import concurrent.futures as cf
import datetime as dt
import html
import json
import os
import pathlib
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

API = "https://api.themoviedb.org/3"
IMG_BASE = os.getenv("IMG_BASE", "https://image.tmdb.org/t/p/")
SITE = "https://www.themoviedb.org"

LANG = os.getenv("LANGUAGE", "pt-BR")
REGION = os.getenv("REGION", "BR")
MAIN_COUNTRY = os.getenv("MAIN_COUNTRY", "US")
DORAMA_COUNTRIES = [c.strip() for c in os.getenv("DORAMA_COUNTRIES", "KR,JP,CN,TW,TH").split(",") if c.strip()]
DAYS = int(os.getenv("WINDOW_DAYS", "7"))
LOOKBACK = int(os.getenv("STREAMING_LOOKBACK_DAYS", "7"))
MAX_MOVIES = int(os.getenv("MAX_MOVIES", "6"))
MAX_SERIES = int(os.getenv("MAX_SERIES", "6"))
MAX_DORAMAS = int(os.getenv("MAX_DORAMAS", "3"))
EXCLUDE_TV = os.getenv("EXCLUDE_TV_GENRES", "10763,10764,10766,10767")
EXCLUDE_DORAMAS = EXCLUDE_TV + ",16"  # sem animação: anime não entra como dorama
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
WORKERS = 8


def assets_url():
    """Onde o e-mail busca as texturas da estante (pasta assets/ do projeto).
    No GitHub Actions: CDN jsDelivr do próprio repositório (exige repositório público).
    Localmente: arquivos da pasta assets/ (funciona na prévia do navegador)."""
    if os.getenv("ASSETS_URL"):
        return os.environ["ASSETS_URL"].rstrip("/") + "/"
    repo, ref = os.getenv("GITHUB_REPOSITORY"), os.getenv("GITHUB_REF_NAME", "main")
    if repo:
        return f"https://cdn.jsdelivr.net/gh/{repo}@{ref}/assets/"
    return (pathlib.Path(__file__).resolve().parent / "assets").as_uri() + "/"


ASSETS = assets_url()


# ================================================================ TMDB
def tmdb(path, **params):
    token = os.environ["TMDB_TOKEN"].strip()
    params.setdefault("language", LANG)
    headers = {"accept": "application/json"}
    if token.startswith("eyJ"):  # token v4 (JWT)
        headers["Authorization"] = f"Bearer {token}"
    else:  # api key v3
        params["api_key"] = token
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:  # limite de requisições: espera e tenta de novo
                time.sleep(2 * (attempt + 1))
                continue
            raise


def pmap(fn, items):
    """Executa fn em paralelo, preservando a ordem; falhas viram None."""
    def safe(x):
        try:
            return fn(x)
        except Exception as e:  # noqa: BLE001
            print(f"aviso: {x}: {e}", file=sys.stderr)
            return None
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(safe, items))


def clean(text):
    return " ".join((text or "").split())


def unique_by_popularity(results, limit):
    seen, out = set(), []
    for r in sorted(results, key=lambda r: r.get("popularity", 0), reverse=True):
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r["id"])
    return out[:limit]


# Nome curto das plataformas: "Netflix Standard with Ads" -> "Netflix"
PLATFORM_ALIASES = [
    (r"netflix", "Netflix"),
    (r"prime video|amazon video|amazon prime", "Prime Video"),
    (r"disney|star\s*\+|star plus", "Disney+"),
    (r"hbo|^max\b", "HBO Max"),
    (r"apple tv\s*(\+|plus)", "Apple TV+"),
    (r"paramount", "Paramount+"),
    (r"globoplay", "Globoplay"),
    (r"crunchyroll", "Crunchyroll"),
    (r"mubi", "MUBI"),
    (r"viki", "Viki"),
    (r"iqiyi", "iQIYI"),
    (r"claro", "Claro TV+"),
    (r"google play", "Google Play"),
    (r"youtube", "YouTube"),
    (r"pluto", "Pluto TV"),
    (r"telecine", "Telecine"),
    (r"looke", "Looke"),
    (r"^apple tv\b", "Apple TV"),
    (r"peacock", "Peacock"),
    (r"hulu", "Hulu"),
]
# sufixos de canal/plano removidos quando a plataforma não está na lista acima
PLATFORM_NOISE = re.compile(
    r"\s+(amazon channel|apple tv channel|roku premium channel|channel|"
    r"standard with ads|basic with ads|with ads|standard|basic|premium|kids)\b.*$",
    re.I,
)


def short_platform(name):
    name = clean(name)
    low = name.lower()
    for pattern, short in PLATFORM_ALIASES:
        if re.search(pattern, low):
            return short
    return PLATFORM_NOISE.sub("", name).replace(" Plus", "+").strip()


def top_provider(lst):
    """A plataforma mais relevante (menor display_priority do TMDB), com nome curto."""
    lst = sorted(lst or [], key=lambda p: p.get("display_priority", 999))
    return short_platform(lst[0]["provider_name"]) if lst else ""


def where_to_watch(block, allow_paid=True):
    r = (block or {}).get("results", {}).get(REGION, {})
    sub = top_provider(r.get("flatrate"))
    if sub:
        return "Disponível em: " + sub
    if allow_paid:
        paid = top_provider((r.get("rent") or []) + (r.get("buy") or []))
        if paid:
            return "Para alugar: " + paid
    return ""


def origin_ok(det, countries):
    origin = det.get("origin_country") or [c["iso_3166_1"] for c in det.get("production_countries", [])]
    return bool(set(origin) & set(countries))


def first_genre(det):
    g = det.get("genres") or []
    return g[0]["name"] if g else ""


# ---------------------------------------------------------------- filmes
def discover_movies(region, rtype, start, end, pages=2):
    res = []
    for page in range(1, pages + 1):
        d = tmdb(
            "/discover/movie",
            region=region,
            with_release_type=rtype,
            with_origin_country=MAIN_COUNTRY,
            sort_by="popularity.desc",
            include_adult="false",
            page=page,
            **{"release_date.gte": start, "release_date.lte": end},
        )
        res += d.get("results", [])
        if page >= d.get("total_pages", 1):
            break
    return res


def regional_release(det, countries, types, start, end):
    """Data (e nota, ex.: 'Netflix') do lançamento do tipo pedido dentro da janela."""
    by = {r["iso_3166_1"]: r["release_dates"] for r in det.get("release_dates", {}).get("results", [])}
    for c in countries:
        for rd in sorted(by.get(c, []), key=lambda x: x.get("release_date", "")):
            d = (rd.get("release_date") or "")[:10]
            if rd.get("type") in types and start <= d <= end:
                return d, clean(rd.get("note"))
    return None, ""


def movie_section(kind, start, end):
    # release types do TMDB: 2/3 = cinema, 4 = digital (streaming/VOD)
    if kind == "cinema":
        cands = discover_movies(REGION, "2|3", start, end)
        countries, types = [REGION], {2, 3}
    else:
        cands = discover_movies(REGION, "4", start, end) + discover_movies("US", "4", start, end)
        countries, types = [REGION, "US"], {4}

    ids = unique_by_popularity(cands, 40)
    dets = pmap(lambda i: tmdb(f"/movie/{i}", append_to_response="watch/providers,release_dates"), ids)
    out = []
    for det in dets:
        if not det or not det.get("poster_path"):
            continue
        overview = clean(det.get("overview"))
        if not overview or not origin_ok(det, [MAIN_COUNTRY]):
            continue
        date, note = regional_release(det, countries, types, start, end)
        if not date:
            continue
        watch = ""
        if kind == "streaming":
            watch = where_to_watch(det.get("watch/providers"))
            if not watch and note:
                watch = "Disponível em: " + short_platform(note)
            if not watch:  # sem plataforma conhecida, não entra
                continue
        meta = [("Estreia " if kind == "cinema" else "Chegou em ") + fmt_date(date)]
        if first_genre(det):
            meta.append(first_genre(det))
        if det.get("vote_count", 0) >= 20:
            meta.append(f"★ {det['vote_average']:.1f}")
        out.append({
            "title": det.get("title") or det.get("original_title"),
            "overview": overview,
            "poster": det["poster_path"],
            "url": f"{SITE}/movie/{det['id']}",
            "meta": meta,
            "watch": watch,
            "badge": "",
        })
        if len(out) >= MAX_MOVIES:
            break
    return out


# ---------------------------------------------------------------- séries
def season_premiere(det, start, end):
    """Temporada (>=1) cuja estreia cai na janela. Pega estreias com episódio duplo."""
    for s in det.get("seasons") or []:
        n, d = s.get("season_number") or 0, s.get("air_date") or ""
        if n >= 1 and start <= d <= end:
            return n, d
    return None


def tv_section(countries, exclude, pages, limit, start, end):
    cands = []
    for country in countries:
        for page in range(1, pages + 1):
            d = tmdb(
                "/discover/tv",
                with_origin_country=country,
                without_genres=exclude,
                sort_by="popularity.desc",
                page=page,
                **{"air_date.gte": start, "air_date.lte": end},
            )
            cands += d.get("results", [])
            if page >= d.get("total_pages", 1):
                break

    ids = unique_by_popularity(cands, 80)
    dets = pmap(lambda i: tmdb(f"/tv/{i}", append_to_response="watch/providers"), ids)
    new, returning = [], []
    for det in dets:
        if not det or not det.get("poster_path") or not origin_ok(det, countries):
            continue
        overview = clean(det.get("overview"))
        if not overview:
            continue
        prem = season_premiere(det, start, end)
        if not prem:
            continue
        season, date = prem
        watch = where_to_watch(det.get("watch/providers"), allow_paid=False)
        if not watch and det.get("networks"):
            watch = "Exibição: " + short_platform(det["networks"][0]["name"])
        meta = [f"Estreia {fmt_date(date)}"]
        if first_genre(det):
            meta.append(first_genre(det))
        item = {
            "title": det.get("name") or det.get("original_name"),
            "overview": overview,
            "poster": det["poster_path"],
            "url": f"{SITE}/tv/{det['id']}",
            "meta": meta,
            "watch": watch,
            "badge": "LANÇAMENTO" if season == 1 else f"TEMPORADA {season}",
        }
        bucket = new if season == 1 else returning
        if len(bucket) < limit:
            bucket.append(item)
        if len(new) >= limit and len(returning) >= limit:
            break
    return new, returning


# ================================================================ HTML
def fmt_date(iso):
    return dt.date.fromisoformat(iso).strftime("%d/%m")


def esc(s):
    return html.escape(s or "")


SANS = "Arial,Helvetica,sans-serif"
DISPLAY = "Impact,'Arial Black',Arial,sans-serif"
MONO = "'Courier New',Courier,monospace"


def grid(cards, per_row, col_w, after_row="", before_row="", valign="top"):
    """Grade responsiva para e-mail: inline-block (empilha no celular) + tabela para o Outlook desktop."""
    rows = []
    for i in range(0, len(cards), per_row):
        cells = "".join(
            f'<!--[if mso]><td width="{col_w}" valign="{valign}"><![endif]-->'
            f'<div style="display:inline-block;vertical-align:{valign};width:100%;max-width:{col_w}px;'
            f'font-size:14px;line-height:normal;">{c}</div>'
            f"<!--[if mso]></td><![endif]-->"
            for c in cards[i : i + per_row]
        )
        rows.append(
            f"{before_row}"
            '<tr><td align="center" style="font-size:0;text-align:center;">'
            '<!--[if mso]><table role="presentation" cellpadding="0" cellspacing="0" align="center"><tr><![endif]-->'
            f"{cells}<!--[if mso]></tr></table><![endif]--></td></tr>{after_row}"
        )
    return "".join(rows)


def wood(image, color, extra=""):
    """Atributos de fundo com textura (atributo + CSS) e cor sólida de reserva (Outlook desktop)."""
    url = ASSETS + image
    return (f'background="{url}" bgcolor="{color}" '
            f'style="background:{color} url(\'{url}\') repeat;{extra}"')


# ---------------------------------------------------------------- cinema
def marquee(title, subtitle):
    bulbs = "● " * 14
    bulb_row = (
        f'<tr><td align="center" style="padding:8px 8px 0;font-family:{SANS};font-size:12px;'
        f'letter-spacing:3px;color:#ffd75e;white-space:nowrap;overflow:hidden;">{bulbs}</td></tr>'
    )
    return f"""
<tr><td style="padding:34px 20px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#1b0e08"
 style="background:#1b0e08;border:4px solid #d4a017;border-radius:8px 8px 0 0;">
{bulb_row}
<tr><td align="center" style="padding:6px 12px 2px;font-family:{DISPLAY};font-size:32px;line-height:38px;
 letter-spacing:4px;color:#fff6d8;text-shadow:0 0 10px #ffb300;">{title}</td></tr>
<tr><td align="center" style="padding:0 12px;font-family:{SANS};font-size:13px;color:#e8c77a;">{subtitle}</td></tr>
{bulb_row.replace('padding:8px 8px 0', 'padding:4px 8px 8px')}
</table></td></tr>"""


def poster_card(i):
    watch = ""
    if i["watch"]:
        watch = (
            '<div style="padding-top:10px;text-align:left;">'
            f'<span style="display:inline-block;background:#d4a017;color:#1b0e08;font-family:{SANS};'
            f'font-size:12px;font-weight:bold;padding:5px 9px;border-radius:3px;">▶ {esc(i["watch"])}</span></div>'
        )
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:12px 10px 18px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#0b0b0b"
   style="background:#0b0b0b;border:6px solid #c9a227;border-radius:4px;box-shadow:0 0 22px rgba(255,196,64,.35);">
   <tr><td style="padding:6px;"><a href="{i['url']}"><img src="{IMG_BASE}w342{i['poster']}" width="236" alt="{esc(i['title'])}"
    style="display:block;width:100%;max-width:236px;height:auto;border:0;"></a></td></tr>
  </table>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:12px 2px 0;text-align:left;">
    <a href="{i['url']}" style="font-family:{DISPLAY};font-size:19px;line-height:23px;letter-spacing:.5px;color:#fff6d8;text-decoration:none;">{esc(i['title'])}</a>
    <div style="font-family:{SANS};font-size:12px;color:#e8c77a;padding-top:3px;">{esc(' · '.join(i['meta']))}</div>
    <div style="font-family:{SANS};font-size:13px;line-height:19px;color:#e6dccf;padding-top:7px;">{esc(i['overview'])}</div>
    {watch}
  </td></tr></table>
</td></tr></table>"""


def cinema_hall(title, subtitle, items):
    if not items:
        return ""
    carpet = (
        '<tr><td style="padding:0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td height="5" bgcolor="#d4a017" style="background:#d4a017;font-size:0;line-height:0;">&nbsp;</td></tr>'
        '<tr><td height="24" bgcolor="#9e1b1b" style="background:#9e1b1b;font-size:0;line-height:0;">&nbsp;</td></tr>'
        '<tr><td height="5" bgcolor="#d4a017" style="background:#d4a017;font-size:0;line-height:0;">&nbsp;</td></tr>'
        "</table></td></tr>"
    )
    return f"""{marquee(title, subtitle)}
<tr><td style="padding:0 20px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#2a0c10"
 style="background:#2a0c10;border-left:4px solid #d4a017;border-right:4px solid #d4a017;">
<tr><td style="padding:10px 6px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{grid([poster_card(i) for i in items], 2, 280)}
</table></td></tr>
{carpet}
</table></td></tr>"""


# ---------------------------------------------------------------- videolocadora
def store_sign(title, subtitle, neon, sub_color):
    return f"""
<tr><td style="padding:40px 20px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#140a24"
 style="background:#140a24;border:3px solid {neon};border-radius:10px;">
<tr><td align="center" style="padding:18px 10px 4px;font-family:'Arial Black',{SANS};font-size:30px;line-height:36px;
 letter-spacing:4px;color:#fff0f8;text-shadow:0 0 6px {neon},0 0 16px {neon};">{title}</td></tr>
<tr><td align="center" style="padding:0 10px 16px;font-family:{MONO};font-size:13px;font-weight:bold;
 letter-spacing:2px;color:{sub_color};">{subtitle}</td></tr>
</table></td></tr>"""


def vhs_card(i):
    sticker_bg = "#e8262d" if i["badge"] == "LANÇAMENTO" else "#ffd400"
    sticker_fg = "#ffffff" if i["badge"] == "LANÇAMENTO" else "#111111"
    watch = (
        f'<div style="font-family:{SANS};font-size:11px;font-weight:bold;color:#0f5f5a;padding-top:7px;">'
        f'📺 {esc(i["watch"])}</div>'
        if i["watch"] else ""
    )
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:4px 7px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#111111"
   style="background:#111111;border-radius:3px 3px 0 0;box-shadow:4px 0 8px rgba(0,0,0,.55);">
    <tr><td style="padding:5px 7px;font-family:'Arial Black',{SANS};font-size:10px;letter-spacing:2px;color:#ffffff;">
      VHS&nbsp;<span style="color:#ff5a36;">■</span><span style="color:#ffb02e;">■</span><span style="color:#ffe14d;">■</span></td></tr>
    <tr><td style="padding:0 7px;"><div style="background:{sticker_bg};color:{sticker_fg};font-family:'Arial Black',{SANS};
      font-size:10px;letter-spacing:1px;text-align:center;padding:3px 0;">{esc(i['badge'])}</div></td></tr>
    <tr><td style="padding:0 7px;"><a href="{i['url']}"><img src="{IMG_BASE}w185{i['poster']}" width="154" alt="{esc(i['title'])}"
      style="display:block;width:100%;max-width:154px;height:auto;border:0;"></a></td></tr>
    <tr><td style="padding:4px 7px 6px;font-family:{SANS};font-size:8px;letter-spacing:2px;color:#9a9a9a;">HI-FI · STEREO · SP</td></tr>
  </table>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#fff8e1"
   style="background:#fff8e1;border:1px solid #d9c9a3;border-top:0;box-shadow:4px 2px 8px rgba(0,0,0,.55);">
    <tr><td style="padding:8px 9px 9px;text-align:left;">
      <a href="{i['url']}" style="font-family:{SANS};font-size:14px;line-height:17px;font-weight:bold;color:#111111;text-decoration:none;">{esc(i['title'])}</a>
      <div style="font-family:{MONO};font-size:11px;font-weight:bold;color:#8a2a2a;padding-top:3px;">{esc(' · '.join(i['meta']))}</div>
      <div style="font-family:{SANS};font-size:12px;line-height:16px;color:#333333;padding-top:5px;">{esc(i['overview'])}</div>
      {watch}
    </td></tr>
  </table>
</td></tr></table>"""


def shelf(label, items, label_bg="#ffd400"):
    """Estante de madeira: cornija com placa, colunas laterais, fundo de tábuas e prateleiras."""
    if not items:
        return ""
    shadow = f'<tr><td height="18" {wood("estante-sombra.jpg", "#24160c", "font-size:0;line-height:0;")}>&nbsp;</td></tr>'
    board = f'<tr><td height="26" {wood("estante-prateleira.jpg", "#9a6634", "font-size:0;line-height:0;")}>&nbsp;</td></tr>'
    rows = grid([vhs_card(i) for i in items], 3, 182, after_row=board, before_row=shadow, valign="bottom")
    return f"""
<tr><td style="padding:26px 20px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#2b1a0f" style="background:#2b1a0f;">
  <tr><td colspan="3" height="56" align="center" valign="middle" {wood("estante-topo.jpg", "#7a4c26")}>
    <table role="presentation" cellpadding="0" cellspacing="0"><tr>
      <td bgcolor="{label_bg}" style="background:{label_bg};border:2px solid #111;padding:6px 14px;font-family:'Arial Black',{SANS};
       font-size:13px;letter-spacing:2px;color:#111;box-shadow:2px 3px 0 rgba(0,0,0,.45);">{label}</td>
    </tr></table>
  </td></tr>
  <tr>
    <td width="22" {wood("estante-lateral.jpg", "#7a4c26", "font-size:0;line-height:0;")}>&nbsp;</td>
    <td valign="top" {wood("estante-fundo.jpg", "#3d2614")}>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
    </td>
    <td width="22" {wood("estante-lateral.jpg", "#7a4c26", "font-size:0;line-height:0;")}>&nbsp;</td>
  </tr>
  <tr><td colspan="3" height="26" {wood("estante-base.jpg", "#5c3a1d", "font-size:0;line-height:0;")}>&nbsp;</td></tr>
</table></td></tr>"""


# ---------------------------------------------------------------- página
def render(data, start, end):
    period = f"{fmt_date(start)} a {fmt_date(end)}"
    parts = []

    parts.append(cinema_hall("EM CARTAZ", f"Estreias nos cinemas · {period}", data["cinema"]))
    parts.append(cinema_hall("NO STREAMING", "Acabaram de chegar às plataformas", data["streaming"]))

    if data["us_returning"] or data["us_new"]:
        parts.append(store_sign("VIDEOLOCADORA", "SÉRIES · FITAS NOVAS TODA SEMANA", "#ff3ea5", "#7df9ff"))
        parts.append(shelf("🔁 NOVAS TEMPORADAS", data["us_returning"]))
        parts.append(shelf("✨ SÉRIES NOVAS", data["us_new"], "#7df9ff"))

    if data["dorama_new"] or data["dorama_returning"]:
        parts.append(store_sign("🏮 DORAMAS", "SEÇÃO ÁSIA · COREIA · JAPÃO · CHINA · TAIWAN · TAILÂNDIA", "#ff4d4d", "#ffd36e"))
        parts.append(shelf("LANÇAMENTOS", data["dorama_new"], "#ff8a8a"))
        parts.append(shelf("CONTINUAÇÕES", data["dorama_returning"], "#ffd36e"))

    body = "".join(parts) or (
        f'<tr><td align="center" style="padding:40px 20px;font-family:{SANS};color:#e6dccf;">'
        "Semana fraca: nenhum lançamento com sinopse encontrado. 🍿</td></tr>"
    )

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>Sessão da semana</title></head>
<body style="margin:0;padding:0;background:#0d0b12;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#0d0b12" style="background:#0d0b12;">
<tr><td align="center" style="padding:16px 0 32px;">
<!--[if mso]><table role="presentation" width="640" cellpadding="0" cellspacing="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;">

<tr><td style="padding:12px 20px 0;">
 <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#f3e3c3"
  style="background:#f3e3c3;border:2px dashed #b3261e;border-radius:6px;">
  <tr><td align="center" style="padding:18px 16px;">
   <div style="font-family:{MONO};font-size:12px;font-weight:bold;letter-spacing:4px;color:#b3261e;">★ INGRESSO SEMANAL ★</div>
   <div style="font-family:{DISPLAY};font-size:36px;line-height:42px;letter-spacing:1px;color:#1a1a1a;padding:4px 0;">SESSÃO DA SEMANA</div>
   <div style="font-family:{MONO};font-size:13px;color:#5a4632;">Lançamentos de {period} · Fila A · Poltrona 7</div>
  </td></tr>
 </table>
</td></tr>

{body}

<tr><td align="center" style="padding:36px 20px 0;font-family:{MONO};font-size:12px;color:#9a8fb0;">
 ★ Seja gentil: rebobine a fita antes de devolver ★</td></tr>
<tr><td align="center" style="padding:10px 20px 0;font-family:{SANS};font-size:10px;line-height:14px;color:#6f6880;">
 Dados: TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.</td></tr>

</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>"""


def render_text(data):
    sections = [
        ("EM CARTAZ NOS CINEMAS", data["cinema"]),
        ("NO STREAMING", data["streaming"]),
        ("NOVAS TEMPORADAS", data["us_returning"]),
        ("SÉRIES NOVAS", data["us_new"]),
        ("DORAMAS · LANÇAMENTOS", data["dorama_new"]),
        ("DORAMAS · CONTINUAÇÕES", data["dorama_returning"]),
    ]
    lines = []
    for title, items in sections:
        if items:
            lines.append(f"\n== {title} ==")
            for i in items:
                extra = " · ".join(([i["badge"]] if i["badge"] else []) + i["meta"])
                lines.append(f"- {i['title']} ({extra}) — {i['url']}")
                if i["watch"]:
                    lines.append(f"  {i['watch']}")
    return "\n".join(lines) or "Nada relevante esta semana."


# ================================================================ envio
def send(subject, html_body, text_body):
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    pwd = os.environ["SMTP_PASSWORD"]
    sender = os.getenv("MAIL_FROM") or user
    to = [a.strip() for a in os.environ["MAIL_TO"].split(",") if a.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = sender
    msg["Bcc"] = ", ".join(to)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx) as s:
            s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            s.starttls(context=ctx)
            s.login(user, pwd)
            s.send_message(msg)


def collect(today):
    start = today.isoformat()
    end = (today + dt.timedelta(days=DAYS - 1)).isoformat()
    s_start = (today - dt.timedelta(days=LOOKBACK)).isoformat()
    s_end = (today - dt.timedelta(days=1)).isoformat()

    us_new, us_ret = tv_section([MAIN_COUNTRY], EXCLUDE_TV, 4, MAX_SERIES, start, end)
    dr_new, dr_ret = tv_section(DORAMA_COUNTRIES, EXCLUDE_DORAMAS, 2, MAX_DORAMAS, start, end)
    data = {
        "cinema": movie_section("cinema", start, end),
        "streaming": movie_section("streaming", s_start, s_end),
        "us_returning": us_ret,
        "us_new": us_new,
        "dorama_new": dr_new,
        "dorama_returning": dr_ret,
    }
    return data, start, end


def main():
    data, start, end = collect(dt.date.today())
    html_body = render(data, start, end)
    with open("newsletter.html", "w", encoding="utf-8") as f:
        f.write(html_body)

    for k, v in data.items():
        print(f"{k:18} {len(v)} itens")
    if DRY_RUN:
        print("DRY_RUN=1 -> e-mail não enviado; veja newsletter.html")
        return
    send(f"🎬 Sessão da semana: {fmt_date(start)} a {fmt_date(end)}", html_body, render_text(data))
    print("E-mail enviado.")


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        sys.exit(f"Variável de ambiente ausente: {e}")
