"""
QS Lab 주간 논문 다이제스트 자동 생성기 (engine v2)
--------------------------------------------------
하는 일:
  1) arXiv(프리프린트) + Semantic Scholar(출판 저널) 두 곳에서 최근 논문을 찾는다
  2) 중복 제거 + 저널 등급(T1/T2…) 분류 + (등급 × 관련도 × 최신) 순으로 정렬
  3) 각 논문을 Claude로 한글 요약/개념/아이디어로 정리
  4) 이번 주 페이지(digests/날짜.html) + 전체 목록(index.html) 생성
GitHub Actions에서 자동 실행됩니다. (비밀 ANTHROPIC_API_KEY 필요)
"""

import re, json, html, time, datetime, pathlib, urllib.parse, urllib.request
import arxiv
import anthropic

# ===== 설정: 여기만 바꾸면 됩니다 =================================
MAX_PAPERS   = 8                     # 이번 주에 정리할 논문 수
DAYS_BACK    = 7                     # arXiv: 최근 며칠 이내 프리프린트
JOURNAL_DAYS = 150                   # 저널: 최근 며칠 이내 출판본까지 포함
MODEL        = "claude-sonnet-4-6"   # 저렴하게: "claude-haiku-4-5-20251001"
CATEGORIES   = ["cond-mat.mtrl-sci", "cond-mat.supr-con", "cond-mat.mes-hall"]
KEYWORDS     = [
    "van der Waals", "2D material", "transition metal dichalcogenide",
    "topological superconductor", "Majorana", "Josephson",
    "ferroelectric", "single-photon", "quantum emitter", "MOCVD",
]
OUT_DIR = pathlib.Path("digests")
# =================================================================


def relevance(text):
    t = (text or "").lower()
    return sum(1 for k in KEYWORDS if k.lower() in t)


def norm_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())[:60]


def tier_of(venue):
    """저널 이름 -> (등급 라벨, 가중치). 가중치가 클수록 위로 정렬됨."""
    v = (venue or "").lower()
    if not v or "arxiv" in v:
        return ("Preprint", 30)
    # Physical Review 계열 ('review'가 들어가지만 리뷰 저널 아님 — 먼저 처리)
    if "physical review x" in v or v.strip() == "prx" or "prx quantum" in v:
        return ("T1-OA", 90)
    if "physical review" in v or v.strip() in ("prl", "prb"):
        return ("T2", 70)
    # 오픈액세스 플래그십
    if any(k in v for k in ["nature communications", "science advances", "npj ",
                            "communications physics", "communications materials"]):
        return ("T1-OA", 90)
    # 리뷰 저널 (구체 토큰만)
    if any(k in v for k in ["reviews of modern physics", "chemical reviews",
                            "nature reviews", "annual review", "chem soc rev"]):
        return ("Review", 60)
    # 플래그십 분야지
    if any(k in v for k in ["nature materials", "nature nanotechnology", "nature physics",
                            "nature photonics", "nature electronics", "nature chemistry"]):
        return ("T1", 100)
    # 최상위 종합지
    if v.startswith("nature") or v == "science":
        return ("T1", 100)
    # 강한 전문지
    if any(k in v for k in ["advanced materials", "advanced functional materials",
                            "advanced energy", "acs nano", "nano letters", "2d materials",
                            "applied physics letters", "angewandte",
                            "journal of the american chemical society"]):
        return ("T2", 70)
    return ("Journal", 50)


def search_arxiv():
    """최근 며칠 이내, 워치리스트 주제의 arXiv 프리프린트."""
    cat_q = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    kw_q  = " OR ".join(f'abs:"{k}"' for k in KEYWORDS)
    search = arxiv.Search(
        query=f"({cat_q}) AND ({kw_q})",
        max_results=60,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS_BACK)
    out = []
    for r in arxiv.Client().results(search):
        if r.published < cutoff:
            break
        out.append({
            "title": r.title,
            "authors": [a.name for a in r.authors],
            "abstract": r.summary.replace("\n", " "),
            "url": r.entry_id,
            "date": r.published.date().isoformat(),
            "venue": "arXiv (preprint)",
            "tier": "Preprint", "weight": 30,
            "doi": getattr(r, "doi", None),
            "arxiv_id": r.get_short_id(),
        })
        if len(out) >= 25:
            break
    return out


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "qs-lab-digest/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def search_s2():
    """Semantic Scholar에서 최근 출판된 관련 저널 논문. 실패하면 빈 목록(=arXiv만 사용)."""
    try:
        fields = ("title,abstract,authors,venue,publicationVenue,"
                  "year,publicationDate,externalIds,url")
        query = ("2D van der Waals quantum material superconductor topological "
                 "ferroelectric single-photon emitter")
        url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(
            {"query": query, "fields": fields, "limit": 40})
        data = None
        for _ in range(2):                       # 가벼운 재시도 (속도제한 대비)
            try:
                data = _get_json(url); break
            except Exception as e:
                print("  S2 재시도:", e); time.sleep(3)
        if not data:
            return []
        cutoff = datetime.date.today() - datetime.timedelta(days=JOURNAL_DAYS)
        out = []
        for p in data.get("data", []):
            venue = ((p.get("publicationVenue") or {}).get("name")) or p.get("venue") or ""
            if not venue or "arxiv" in venue.lower():
                continue                         # 프리프린트/무명 venue는 arXiv 쪽에서 처리
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            if relevance(title + " " + abstract) < 1:
                continue                         # 주제와 무관하면 제외
            pdate = p.get("publicationDate") or ""
            try:
                if pdate and datetime.date.fromisoformat(pdate) < cutoff:
                    continue                     # 너무 오래된 출판본 제외
            except Exception:
                pass
            ext = p.get("externalIds") or {}
            tier, weight = tier_of(venue)
            out.append({
                "title": title,
                "authors": [a.get("name", "") for a in (p.get("authors") or [])],
                "abstract": abstract,
                "url": p.get("url") or (f"https://doi.org/{ext.get('DOI')}" if ext.get("DOI") else ""),
                "date": pdate,
                "venue": venue,
                "tier": tier, "weight": weight,
                "doi": ext.get("DOI"),
                "arxiv_id": ext.get("ArXiv"),
            })
        return out
    except Exception as e:
        print("  S2 검색 실패(무시):", e)
        return []


def dedupe(papers):
    """같은 논문(arXiv ID/DOI/제목)이 두 소스에 있으면, 등급 높은(출판본) 쪽만 남긴다."""
    best = {}
    for p in papers:
        key = p.get("arxiv_id") or p.get("doi") or norm_title(p["title"])
        if key not in best or p["weight"] > best[key]["weight"]:
            best[key] = p
    return list(best.values())


PROMPT = """당신은 2D 양자소재 연구실(QS Lab, 성균관대)의 논문 분석가입니다.
아래 논문을 대학원생이 보기 좋게 정리하세요. 한글 중심으로 쓰되, 전문용어만 영어를 병기합니다.

제목: {title}
저자: {authors}
초록: {abstract}

아래 형식의 JSON만 출력하세요 (설명·마크다운 없이, 중괄호로 시작해 끝):
{{
  "title_ko": "한글 제목 한 줄",
  "summary": "3~4문장 한글 요약",
  "points": ["핵심 발견 1", "핵심 발견 2", "핵심 발견 3"],
  "relevance": "QS Lab(2D 합성·강유전 게이팅·위상초전도·양자방출체) 관점에서 왜 중요한지 한 줄",
  "concepts": [{{"term": "개념(영문)", "desc": "한 줄 설명"}}, {{"term": "개념2", "desc": "한 줄 설명"}}],
  "idea": "이 논문에서 출발할 수 있는 연구 아이디어 한 줄"
}}"""


def analyze(client, p):
    msg = client.messages.create(
        model=MODEL, max_tokens=1500,
        messages=[{"role": "user", "content": PROMPT.format(
            title=p["title"], authors=", ".join(p["authors"][:8]), abstract=p["abstract"])}],
    )
    text = msg.content[0].text
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s:e + 1])


def esc(x):
    return html.escape(str(x))


HEAD = """<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--paper:#f6f3ec;--panel:#fffdf8;--ink:#1c1b19;--muted:#6c665d;--rule:#e2dccd;--accent:#2b3a63;--sc:#2f6f5e;--ox:#7a3526}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:"Source Serif 4","Gowun Batang",Georgia,serif;line-height:1.7;word-break:keep-all}
.wrap{max-width:820px;margin:0 auto;padding:24px 20px 70px}
header{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:22px}
.brand{font-weight:700;font-size:21px}
.date{font-family:"IBM Plex Sans",sans-serif;color:var(--muted);font-size:13px;margin-top:4px}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:10px;padding:18px 20px;margin-bottom:16px}
.tag{font-family:"IBM Plex Sans",sans-serif;font-size:12px;color:var(--muted)}
.badge{display:inline-block;font-size:10px;font-weight:600;padding:1px 7px;border-radius:5px;margin-right:7px}
.b1{background:#7a3526;color:#fff}.b1oa{background:#2f6f5e;color:#fff}.b2{background:#2b3a63;color:#fff}
.brev{background:#e6efe9;color:#2f6f5e;border:1px solid #cfe2d6}.bj{background:#eee9dd;color:#6c665d}
.bpre{background:transparent;color:#6c665d;border:1px dashed #c9c2b2}
.card h2{font-size:18px;margin:6px 0 2px;font-family:"Gowun Batang",serif}
.orig{font-style:italic;color:var(--muted);font-size:14px}
.auth{font-family:"IBM Plex Sans",sans-serif;font-size:12px;color:var(--muted);margin:3px 0 10px}
.card p{margin:8px 0}.card ul{margin:8px 0;padding-left:20px}
.rel{background:#e6efe9;border-left:3px solid var(--sc);border-radius:0 8px 8px 0;padding:9px 13px;margin:10px 0;font-size:15px}
.rel b{color:var(--sc)}
.idea{background:#f4e7e2;border-left:3px solid var(--ox);border-radius:0 8px 8px 0;padding:9px 13px;margin:10px 0;font-size:15px}
.idea b{color:var(--ox)}
details{margin:8px 0;font-size:14px}
summary{cursor:pointer;font-family:"IBM Plex Sans",sans-serif;font-weight:600;color:var(--accent)}
.link{font-family:"IBM Plex Sans",sans-serif;font-size:13px;color:var(--accent)}
.arch{list-style:none;padding:0}.arch li{border-bottom:1px solid var(--rule);padding:11px 2px}
.arch a{font-family:"IBM Plex Sans",sans-serif;color:var(--accent);text-decoration:none;font-size:16px}
footer{font-family:"IBM Plex Sans",sans-serif;font-size:12px;color:var(--muted);margin-top:24px;line-height:1.6}
</style>"""

BADGE_CLASS = {"T1": "b1", "T1-OA": "b1oa", "T2": "b2", "Review": "brev",
               "Journal": "bj", "Preprint": "bpre"}


def card_html(p, a):
    pts  = "".join(f"<li>{esc(x)}</li>" for x in a.get("points", []))
    cons = "".join(f'<li><b>{esc(c.get("term",""))}</b> — {esc(c.get("desc",""))}</li>'
                   for c in a.get("concepts", []))
    cls  = BADGE_CLASS.get(p["tier"], "bj")
    date = f' · {esc(p["date"])}' if p.get("date") else ""
    return f"""<article class="card">
  <div class="tag"><span class="badge {cls}">{esc(p["tier"])}</span>{esc(p["venue"])}{date}</div>
  <h2>{esc(a.get("title_ko",""))}</h2>
  <div class="orig">{esc(p["title"])}</div>
  <div class="auth">{esc(", ".join(p["authors"][:6]))}</div>
  <p>{esc(a.get("summary",""))}</p>
  <ul>{pts}</ul>
  <div class="rel"><b>QS Lab 관점</b> · {esc(a.get("relevance",""))}</div>
  <details><summary>개념 정리</summary><ul>{cons}</ul></details>
  <div class="idea"><b>아이디어</b> · {esc(a.get("idea",""))}</div>
  <a class="link" href="{esc(p["url"])}" target="_blank">원문 ↗</a>
</article>"""


def page(title, datelabel, body):
    return (f'<!doctype html><html lang="ko"><head>{HEAD}'
            f'<title>{esc(title)}</title></head><body><div class="wrap">'
            f'<header><div class="brand">QS Lab 주간 논문 다이제스트</div>'
            f'<div class="date">{esc(datelabel)}</div></header>'
            f'{body}</div></body></html>')


def main():
    OUT_DIR.mkdir(exist_ok=True)
    print("arXiv 검색…");            arx = search_arxiv(); print(f"  arXiv {len(arx)}편")
    print("Semantic Scholar 검색…"); s2  = search_s2();    print(f"  S2(저널) {len(s2)}편")

    papers = dedupe(arx + s2)
    papers.sort(key=lambda p: (p["weight"],
                               relevance(p["title"] + " " + p["abstract"]),
                               p.get("date", "")), reverse=True)
    papers = papers[:MAX_PAPERS]
    print(f"  최종 {len(papers)}편 (등급순)")

    client = anthropic.Anthropic()
    cards = []
    for p in papers:
        try:
            print(f"  분석[{p['tier']}]:", p["title"][:55])
            cards.append(card_html(p, analyze(client, p)))
        except Exception as err:
            print("  (건너뜀)", err)

    if not cards:
        print("생성된 카드가 없습니다 — 종료")
        return

    today = datetime.date.today().isoformat()
    body = "\n".join(cards) + ('<footer>AI가 만든 자동 초안입니다. 사실관계·수치는 '
                               '원문으로 확인하세요. 저널 등급은 자동 분류라 근사값입니다. PI 검토 후 활용.</footer>')
    (OUT_DIR / f"{today}.html").write_text(page(f"다이제스트 {today}", today, body), encoding="utf-8")
    print("작성:", OUT_DIR / f"{today}.html")

    files = sorted(OUT_DIR.glob("*.html"), reverse=True)
    arch = ('<ul class="arch">'
            + "".join(f'<li><a href="digests/{f.name}">{f.stem}</a></li>' for f in files)
            + "</ul><footer>주차를 클릭하면 그 주 다이제스트가 열립니다.</footer>")
    pathlib.Path("index.html").write_text(page("다이제스트 · 아카이브", "아카이브", arch), encoding="utf-8")
    print("index.html 갱신 — 총", len(files), "주차")


if __name__ == "__main__":
    main()
