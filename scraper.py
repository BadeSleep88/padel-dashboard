"""Matchpoint (padel club) history scraper - library version of schedule_history scraper.

Differences from the CLI script: credentials are passed in memory for one run (never written to disk or logs),
no config/output files, progress callbacks for the UI, a hard deadline, and user-safe error messages.
"""
from __future__ import annotations

import logging
import re
import shutil
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)
PREVIOUS_GRID = "ctl00$ctl00$ContentPlaceHolderContenido$ContentPlaceHolderContenido$GridViewListadoAnteriores"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")


class ScrapeError(Exception):
    """Message is safe to show to end users."""


class LoginError(ScrapeError):
    pass


class MatchpointScraper:
    def __init__(self, email: str, password: str, base_url: str, *, progress: Optional[Callable[[str, int], None]] = None,
                 delay: float = 0.15, headless: bool = True, settle_ms: int = 10000,
                 deadline: Optional[float] = None, max_pages: Optional[int] = None):
        self._email, self._password = email, password
        self.base = base_url.rstrip("/")
        self.login_url = f"{self.base}/Login.aspx"
        self.schedule_url = f"{self.base}/Intranet/Schedule.aspx"
        self.progress = progress or (lambda stage, pct: None)
        self.delay, self.headless, self.settle_ms = delay, headless, settle_ms
        self.deadline, self.max_pages = deadline, max_pages
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Referer": self.login_url})

    def _check_deadline(self) -> None:
        if self.deadline and time.time() > self.deadline:
            raise ScrapeError("Fetching your history took too long. Please try again.")

    # ---------------------------------------------------------------- login
    def _dismiss_cookie_banner(self, page) -> None:
        block = page.locator(".banner-block-screen")
        try:
            if not block.is_visible(timeout=2000):
                return
        except Exception:
            return
        btn = page.locator("#ButtonPermitirTodos")
        try:
            btn.wait_for(state="visible", timeout=15000)
            btn.click(timeout=10000)
        except Exception:
            page.evaluate("""() => {
                if (typeof cerrarVentanaUserPreferences === 'function') { cerrarVentanaUserPreferences('todos'); return; }
                document.cookie = 'MPOpcionCookie=todos; path=/; max-age=31536000';
                const o = document.querySelector('.banner-block-screen'); if (o) o.style.display = 'none';
            }""")
        try:
            block.wait_for(state="hidden", timeout=10000)
        except Exception:
            pass

    def login(self) -> None:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as p:
                exe = shutil.which("chromium") or shutil.which("chromium-browser")  # apt chromium (Streamlit Cloud); else Playwright's own
                browser = p.chromium.launch(headless=self.headless, slow_mo=100, executable_path=exe,
                                            args=["--no-sandbox", "--disable-dev-shm-usage"])
                try:
                    ctx = browser.new_context()
                    ctx.add_cookies([{"name": "MPOpcionCookie", "value": "todos", "url": self.login_url}])
                    page = ctx.new_page()
                    page.goto(self.login_url, wait_until="domcontentloaded", timeout=30000)
                    self._dismiss_cookie_banner(page)
                    btn = "#ContentPlaceHolderContenido_Login1_LoginButton"
                    page.wait_for_selector(btn, state="visible", timeout=30000)
                    page.fill("#ContentPlaceHolderContenido_Login1_UserName", self._email)
                    page.fill("#ContentPlaceHolderContenido_Login1_Password", self._password)
                    page.wait_for_function(
                        """sel => { const b = document.querySelector(sel); if (!b) return false;
                            return !b.disabled && !b.hasAttribute("disabled") && b.getAttribute("aria-disabled") !== "true"; }""",
                        arg=btn, timeout=60000)
                    page.wait_for_timeout(self.settle_ms)
                    self._dismiss_cookie_banner(page)
                    page.click(btn)
                    page.wait_for_load_state("load", timeout=30000)
                    if "Login.aspx" in page.url:
                        raise LoginError("Sign-in failed. Check your club email and password, then try again.")
                    for c in ctx.cookies():
                        self.session.cookies.set(name=c.get("name"), value=c.get("value"),
                                                 domain=c.get("domain"), path=c.get("path", "/"))
                finally:
                    browser.close()
        except PWTimeout:
            raise ScrapeError("The club website didn't respond in time. Please try again in a few minutes.") from None
        except PWError as e:
            if "Executable doesn't exist" in str(e):
                raise ScrapeError("The server's browser isn't installed (run `playwright install chromium`, or add `chromium` to packages.txt).") from None
            raise ScrapeError("Couldn't complete sign-in on the club website. Please try again.") from None
        finally:
            self._password = None  # drop the secret as soon as the browser step is over

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _hidden_fields(html: str) -> Dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        return {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}

    def _abs(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return f"{self.base}{href}"
        return urljoin(f"{self.base}/Intranet/", href)

    @staticmethod
    def _text(el) -> str:
        return el.get_text(strip=True) if el else ""

    @staticmethod
    def _time_of_day(value: str) -> Optional[str]:
        m = re.search(r"(\d{1,2}):(\d{2})", value or "")
        return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None

    @staticmethod
    def _date_only(value: str) -> Optional[str]:
        m = re.search(r"(\d{2}/\d{2}/\d{4})", value or "")
        return m.group(1) if m else None

    @staticmethod
    def _duration(time_range: str) -> Optional[int]:
        m = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", time_range or "")
        if not m:
            return None
        start, end = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
        return (end + 1440 if end < start else end) - start

    @staticmethod
    def _dow(date_str: str) -> Optional[str]:
        try:
            return datetime.strptime(date_str, "%d/%m/%Y").strftime("%A")
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _tag_from_tipo(tipo: str) -> str:
        t = (tipo or "").lower()
        if "partida" in t:
            return "Match"
        if "actividad" in t:
            return "Activity"
        if "individual" in t or "reserva" in t:
            return "Booking"
        return "Unknown"

    # ------------------------------------------------------ list + paging
    def _parse_list_items(self, html: str) -> List[Dict[str, Any]]:
        grid = BeautifulSoup(html, "html.parser").find("table", id=lambda x: x and "GridViewListadoAnteriores" in x)
        if not grid:
            return []
        items = []
        for a in grid.select("a.TextoLinkBlanco"):
            href = a.get("href") or ""
            if not href or href.startswith("javascript:"):
                continue
            td = a.find_parent("td")
            tag_span = td.find("span", id=lambda x: x and "LabelTipoReserva" in x) if td else None
            st, en = self._time_of_day(a.get("horainicio", "")), self._time_of_day(a.get("horafin", ""))
            time_range = f"{st}-{en}" if st and en else None
            if not time_range and td:
                time_range = re.sub(r"\s+", "", self._text(td.find("span", id=lambda x: x and "LabelHorario_" in x))) or None
            date_str = self._date_only(a.get("fechainicio", ""))
            court = (a.get("recurso") or "").strip()
            if td:
                pista = td.find("span", id=lambda x: x and "LabelPista_" in x)
                if pista and self._text(pista):
                    court = self._text(pista)
                elif not court:
                    court = self._text(td.find("span", id=lambda x: x and "LabelRecurso_" in x))
            instructor = (a.get("monitor") or "").strip() or None
            if td and not instructor:
                instructor = self._text(td.find("span", id=lambda x: x and "LabelMonitor_" in x)) or None
            items.append({
                "tag": self._text(tag_span) or self._tag_from_tipo(a.get("tipo", "")),
                "tipo": a.get("tipo") or "",
                "name": (a.get("nombreactividad") or "").strip() or None,
                "date": date_str,
                "day_of_week": self._dow(date_str) if date_str else None,
                "time": time_range,
                "duration_minutes": self._duration(time_range or ""),
                "court": court or None,
                "instructor": instructor,
                "localizador": a.get("data-localizador") or None,
                "list_link": self._abs(href),
            })
        return items

    @staticmethod
    def _pager_pages(html: str) -> Tuple[int, List[int]]:
        pager = BeautifulSoup(html, "html.parser").select_one("tr.gridviewestilopaginador")
        if not pager:
            return 1, [1]
        current, pages = 1, []
        for cell in pager.find_all("td"):
            span, link = cell.find("span"), cell.find("a")
            if span and span.get_text(strip=True).isdigit():
                current = int(span.get_text(strip=True))
                pages.append(current)
            elif link:
                m = re.search(r"Page\$(\d+)", link.get("href", ""))
                if m:
                    pages.append(int(m.group(1)))
        return current, sorted(set(pages)) or [1]

    def fetch_all_list_items(self) -> List[Dict[str, Any]]:
        res = self.session.get(self.schedule_url, timeout=30)
        res.raise_for_status()
        html, all_items, seen, visited = res.text, [], set(), set()
        while True:
            self._check_deadline()
            current, pages = self._pager_pages(html)
            if current in visited:
                break
            visited.add(current)
            page_items = self._parse_list_items(html)
            self.progress(f"Reading your activity list (page {current})...", min(25, 8 + 2 * current))
            for item in page_items:
                key = item.get("localizador") or item.get("list_link")
                if key not in seen:
                    seen.add(key)
                    all_items.append(item)
            if (self.max_pages and len(visited) >= self.max_pages) or len(page_items) < 25:
                break  # the site returns 25 rows per page, so a short page is the last one
            nxt = next((p for p in pages if p > current), None)
            if nxt is None:
                break
            payload = self._hidden_fields(html)
            payload["__EVENTTARGET"], payload["__EVENTARGUMENT"] = PREVIOUS_GRID, f"Page${nxt}"
            res = self.session.post(self.schedule_url, data=payload, timeout=30)
            res.raise_for_status()
            html = res.text
            new_current, _ = self._pager_pages(html)
            if new_current == current and nxt not in pages:
                break
            time.sleep(self.delay)
        return all_items

    # ----------------------------------------------------------- details
    def fetch_detail(self, item: Dict[str, Any]) -> Dict[str, Any]:
        url, tag = item["list_link"], item.get("tag") or "Unknown"
        out: Dict[str, Any] = {k: item.get(k) for k in ("tag", "name", "date", "day_of_week", "time", "duration_minutes",
                                                         "court", "instructor")}
        out.update(levels=None, players=None, link=url, localizador=item.get("localizador"))
        try:
            res = self.session.get(url, timeout=20, allow_redirects=True)
            final, html = res.url, res.text
            path = urlparse(final).path.lower()
            follow = None
            if tag == "Match" and "match.aspx" not in path:
                follow = self._find_match_link(html)
            elif tag == "Activity" and "activitybooking.aspx" not in path:
                follow = self._find_activity_link(html)
            if follow:
                res = self.session.get(follow, timeout=20, allow_redirects=True)
                final, html = res.url, res.text
                path = urlparse(final).path.lower()
            out["link"] = final
            if "match.aspx" in path:
                out.update(self._parse_match_page(html, final))
            elif "activitybooking.aspx" in path or "info.aspx" in path:
                out.update(self._parse_activity_page(html, final))
            elif "booking.aspx" in path:
                if tag == "Match":
                    out.update(self._parse_booking_as_match(html, final))
                elif tag == "Activity":
                    d = self._parse_booking_page(html, final)
                    d.update(tag="Activity")
                    d.update({k: item[k] for k in ("name", "instructor") if item.get(k)})
                    out.update(d)
                else:
                    out.update(self._parse_booking_page(html, final))
            elif tag == "Match":
                out.update(self._parse_match_page(html, final))
            elif tag == "Activity":
                out.update(self._parse_activity_page(html, final))
            else:
                out.update(self._parse_booking_page(html, final))
        except Exception as e:
            log.warning("detail fetch failed: %s", type(e).__name__)
            out["error"] = type(e).__name__
        for key in ("date", "time", "court", "instructor", "name", "day_of_week"):
            if not out.get(key) and item.get(key):
                out[key] = item[key]
        if out.get("time") and not out.get("duration_minutes"):
            out["duration_minutes"] = self._duration(out["time"])
        if out.get("date") and not out.get("day_of_week"):
            out["day_of_week"] = self._dow(out["date"])
        return out

    def _find_match_link(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if "Match.aspx" in a["href"]:
                return self._abs(a["href"])
        og = soup.find("meta", attrs={"property": "og:url"}) or soup.find("meta", attrs={"name": "og:url"})
        return og["content"] if og and "Match.aspx" in (og.get("content") or "") else None

    def _find_activity_link(self, html: str) -> Optional[str]:
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            if "ActivityBooking.aspx" in a["href"] or "Info.aspx" in a["href"]:
                return self._abs(a["href"])
        return None

    def _parse_match_page(self, html: str, url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        data: Dict[str, Any] = {"link": url, "tag": "Match"}
        court = self._text(soup.find("span", id=lambda x: x and "LabelRecurso" in x))
        if court:
            data["court"] = court
        date_raw = self._text(soup.find("span", id=lambda x: x and "LabelHorario" in x and "Partida" in x)) or \
            self._text(soup.find("span", id=lambda x: x and x.endswith("WUCCabeceraPartida_LabelHorario")))
        if date_raw:
            parsed = None
            for fmt in ("%d/%m/%Y", "%d %B %Y", "%d %b %Y"):
                try:
                    parsed = datetime.strptime(date_raw, fmt)
                    break
                except ValueError:
                    continue
            if parsed:
                data["date"], data["day_of_week"] = parsed.strftime("%d/%m/%Y"), parsed.strftime("%A")
            elif (m := re.search(r"\d{2}/\d{2}/\d{4}", date_raw)):
                data["date"], data["day_of_week"] = m.group(0), self._dow(m.group(0))
        time_raw = self._text(soup.find("span", id=lambda x: x and x.endswith("WUCCabeceraPartida_LabelHora")))
        if time_raw:
            data["time"] = re.sub(r"\s+", "", time_raw)
            data["duration_minutes"] = self._duration(data["time"])

        def selected(sel_id):
            sel = soup.find("select", id=lambda x: x and sel_id in x)
            opt = sel.find("option", selected=True) if sel else None
            return ((opt.get("value") if opt else None) or self._text(opt)) if sel else None

        lo, hi = selected("DropDownListNivelDesde"), selected("DropDownListNivelHasta")
        if lo and hi:
            data["levels"] = f"{lo} - {hi}"
        elif lo or hi:
            data["levels"] = lo or hi
        else:
            og = soup.find("meta", attrs={"name": "og:description"}) or soup.find("meta", attrs={"property": "og:description"})
            m = re.search(r"(\d+[.,]\d+)\s*-\s*(\d+[.,]\d+)", (og.get("content") if og else "") or "")
            if m:
                data["levels"] = f"{m.group(1)} - {m.group(2)}"

        players = []
        for span in soup.find_all("span", id=lambda x: x and "LabelNombre_" in x and "Jugador" in x):
            name = self._text(span)
            if not name:
                continue
            sid = span.get("id", "")
            team = "A" if "EquipoA" in sid else ("B" if "EquipoB" in sid else None)
            nivel = soup.find("span", id=sid.replace("LabelNombre_", "LabelNivel_"))
            if not nivel:
                card = span.find_parent("div", class_=lambda c: c and "contenedorContenidoPartidas" in c)
                nivel = card.find("span", id=lambda x: x and "LabelNivel_" in x) if card else None
            players.append({"name": name, "team": team, "rating": (self._text(nivel) or None) if nivel else None})
        if players:
            data["players"] = players
        sex = soup.find("select", id=lambda x: x and "DropDownListSexo" in x)
        opt = sex.find("option", selected=True) if sex else None
        if opt:
            data["name"] = f"{self._text(opt)} Padel Match"
        return data

    def _parse_activity_page(self, html: str, url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        data: Dict[str, Any] = {"link": url, "tag": "Activity"}
        title = self._text(soup.find("span", id=lambda x: x and "LabelReservaPistas" in x)) or \
            self._text(soup.find("span", id=lambda x: x and "LabelNombreActividad" in x))
        if title:
            data["name"] = title
        horario = self._text(soup.find("span", id=lambda x: x and x.endswith("LabelHorario")))
        if horario:
            dm = re.search(r"(\d{2}/\d{2}/\d{4})", horario)
            tm = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", horario)
            if dm:
                data["date"], data["day_of_week"] = dm.group(1), self._dow(dm.group(1))
            if tm:
                data["time"] = f"{tm.group(1)}-{tm.group(2)}"
                data["duration_minutes"] = self._duration(data["time"])
        instructor = self._text(soup.find("span", id=lambda x: x and x.endswith("LabelMonitor")))
        if instructor:
            data["instructor"] = instructor
        place = self._text(soup.find("span", id=lambda x: x and "LabelUbicacion" in x)) or \
            self._text(soup.find("span", id=lambda x: x and "LabelLugar" in x))
        if place:
            data["court"] = place.split(" - ")[0].strip()  # "Court 2: Sponsor - Club name"
        levels = self._text(soup.find("span", id=lambda x: x and "LabelNiveles" in x))
        if levels:
            data["levels"] = levels
        return data

    def _parse_booking_page(self, html: str, url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        data: Dict[str, Any] = {"link": url, "tag": "Booking"}
        val = lambda frag: ((soup.find("input", id=lambda x: x and frag in x) or {}).get("value") or "").strip()
        if val("TextBoxRecurso"):
            data["court"] = val("TextBoxRecurso")
        if val("TextBoxFecha_Inicio"):
            data["date"] = val("TextBoxFecha_Inicio")
            data["day_of_week"] = self._dow(data["date"])
        if val("TextBoxHoraInicio") and val("TextBoxHoraFin"):
            data["time"] = f"{val('TextBoxHoraInicio')}-{val('TextBoxHoraFin')}"
            data["duration_minutes"] = self._duration(data["time"])
        return data

    def _parse_booking_as_match(self, html: str, url: str) -> Dict[str, Any]:
        data = self._parse_booking_page(html, url)
        data["tag"] = "Match"
        players = [{"name": self._text(a), "team": None, "rating": None}
                   for a in BeautifulSoup(html, "html.parser").select("a.TextoLink")
                   if self._text(a) and "Perfil.aspx" in (a.get("href") or "")]
        if players:
            data["players"] = players
        return data

    # --------------------------------------------------------------- run
    def run(self) -> Dict[str, Any]:
        self.progress("Signing in to the club website...", 3)
        self.login()
        self.progress("Reading your activity list...", 8)
        try:
            items = self.fetch_all_list_items()
        except requests.RequestException:
            raise ScrapeError("Couldn't read your activity list from the club website. Please try again.") from None
        if not items:
            raise ScrapeError("No past activities were found on your account.")
        results = []
        for i, item in enumerate(items, 1):
            self._check_deadline()
            self.progress(f"Fetching session {i} of {len(items)}...", 25 + int(70 * i / len(items)))
            results.append(self.fetch_detail(item))
            time.sleep(self.delay)
        return {"scraped_at": datetime.now().isoformat(timespec="seconds"), "count": len(results), "items": results}
