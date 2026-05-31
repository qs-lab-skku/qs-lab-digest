"""
QS Lab 주간 논문 다이제스트 자동 생성기 (engine)
--------------------------------------------------
하는 일:
  1) arXiv에서 최근 논문을 찾는다 (워치리스트 주제/카테고리)
  2) 각 논문을 Claude로 한글 요약 + 개념 + 연구 아이디어로 정리한다
  3) 이번 주 다이제스트 페이지(digests/날짜.html)와
     전체 목록 페이지(index.html)를 만든다
GitHub Actions에서 자동 실행됩니다. (비밀 ANTHROPIC_API_KEY 필요)
"""

import os, re, json, html, datetime, pathlib
import arxiv
import anthropic

# ===== 설정: 여기만 바꾸면 됩니다 =================================
MAX_PAPERS = 8                       # 이번 주에 정리할 논문 수 (처음엔 작게)
DAYS_BACK  = 7                       # 최근 며칠 이내 논문만
MODEL      = "claude-sonnet-4-6"     # 저렴하게: "claude-haiku-4-5-20251001"
CATEGORIES = ["cond-mat.mtrl-sci", "cond-mat.supr-con", "cond-mat.mes-hall"]
KEYWORDS   = [
    "van der Waals", "2D material", "transition metal dichalcogenide",
    "topological superconductor", "Majorana", "Josephson",
    "ferroelectric", "single-photon", "quantum emitter", "MOCVD",
]
OUT_DIR = pathlib.Path("digests")
# =================================================================

client = anthropic.Anthropic()   # 키는 환경변수 ANTHROPIC_API_KEY에서 자동으로 읽음


def search_arxiv():
    """최근 며칠 이내, 워치리스트 주제에 맞는 논문 목록을 가져온다."""
    cat_q = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    kw_q  = " OR ".join(f'abs:"{k}"' for k in KEYWORDS)
    query = f"({cat_q}) AND ({kw_q})"
    search = arxiv.Search(
        query=query,
        max_results=60,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS_BACK)
    papers = []
    for r in arxiv.Client().results(search):
        if r.published < cutoff:
            break                    # 최신순이라, 기준보다 오래되면 중단
        papers.append(r)
        if len(papers) >= MAX_PAPERS:
            break
    return papers


PROMPT = """당신은 2D 양자소재 연구실(QS Lab, 성균관대)의 논문 분석가입니다.
아래 arXiv 논문을 대학원생이 보기 좋게 정리하세요. 한글 중심으로 쓰되, 전문용어만 영어를 병기합니다.

제목: {title}
저자: {authors}
초록: {abstract}

아래 형식의 JSON만 출력하세요 (설명이나 마크다운 없이, 중괄호로 시작해서 끝):
{{
  "title_ko": "한글 제목 한 줄",
  "summary": "3~4문장 한글 요약",
  "points": ["핵심 발견 1", "핵심 발견 2", "핵심 발견 3"],
  "relevance": "QS Lab(2D 합성·강유전 게이팅·위상초전도·양자방출체) 관점에서 왜 중요한지 한 줄",
  "concepts": [{{"term": "개념(영문)", "desc": "한 줄 설명"}}, {{"term": "개념2", "desc": "한 줄 설명"}}],
  "idea": "이 논문에서 출발할 수 있는 연구 아이디어 한 줄"
}}"""


def analyze(paper):
    """논문 하나를 Claude로 정리해 dict로 돌려준다."""
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": PROMPT.format(
            title=paper.title,
            authors=", ".join(a.name for a in paper.authors[:8]),
            abstract=paper.summary.replace("\n", " "),
        )}],
    )
    text = msg.content[0].text
    s, e = text.find("{"), text.rfind("}")      # 첫 { 부터 마지막 } 까지만 안전 추출
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
.tag{font-family:"IBM Plex Sans",sans-serif;font-size:11px;color:var(--accent);font-weight:600}
.card h2{font-size:18px;margin:6px 0 2px;font-family:"Gowun Batang",serif}
.orig{font-style:italic;color:var(--muted);font-size:14px}
.auth{font-family:"IBM Plex Sans",sans-serif;font-size:12px;color:var(--muted);margin:3px 0 10px}
.card p{margin:8px 0}
.card ul{margin:8px 0;padding-left:20px}
.rel{background:#e6efe9;border-left:3px solid var(--sc);border-radius:0 8px 8px 0;padding:9px 13px;margin:10px 0;font-size:15px}
.rel b{color:var(--sc)}
.idea{background:#f4e7e2;border-left:3px solid var(--ox);border-radius:0 8px 8px 0;padding:9px 13px;margin:10px 0;font-size:15px}
.idea b{color:var(--ox)}
details{margin:8px 0;font-size:14px}
summary{cursor:pointer;font-family:"IBM Plex Sans",sans-serif;font-weight:600;color:var(--accent)}
.link{font-family:"IBM Plex Sans",sans-serif;font-size:13px;color:var(--accent)}
.arch{list-style:none;padding:0}
.arch li{border-bottom:1px solid var(--rule);padding:11px 2px}
.arch a{font-family:"IBM Plex Sans",sans-serif;color:var(--accent);text-decoration:none;font-size:16px}
footer{font-family:"IBM Plex Sans",sans-serif;font-size:12px;color:var(--muted);margin-top:24px;line-height:1.6}
</style>"""


def card_html(paper, a):
    pts  = "".join(f"<li>{esc(p)}</li>" for p in a.get("points", []))
    cons = "".join(f'<li><b>{esc(c.get("term",""))}</b> — {esc(c.get("desc",""))}</li>'
                   for c in a.get("concepts", []))
    return f"""<article class="card">
  <div class="tag">arXiv · {esc(paper.get_short_id())}</div>
  <h2>{esc(a.get("title_ko",""))}</h2>
  <div class="orig">{esc(paper.title)}</div>
  <div class="auth">{esc(", ".join(x.name for x in paper.authors[:6]))}</div>
  <p>{esc(a.get("summary",""))}</p>
  <ul>{pts}</ul>
  <div class="rel"><b>QS Lab 관점</b> · {esc(a.get("relevance",""))}</div>
  <details><summary>개념 정리</summary><ul>{cons}</ul></details>
  <div class="idea"><b>아이디어</b> · {esc(a.get("idea",""))}</div>
  <a class="link" href="{esc(paper.entry_id)}" target="_blank">arXiv 원문 ↗</a>
</article>"""


def page(title, datelabel, body):
    return (f'<!doctype html><html lang="ko"><head>{HEAD}'
            f'<title>{esc(title)}</title></head><body><div class="wrap">'
            f'<header><div class="brand">QS Lab 주간 논문 다이제스트</div>'
            f'<div class="date">{esc(datelabel)}</div></header>'
            f'{body}</div></body></html>')


def main():
    OUT_DIR.mkdir(exist_ok=True)
    print("arXiv 검색 중…")
    papers = search_arxiv()
    print(f"  후보 {len(papers)}편")

    cards = []
    for p in papers:
        try:
            print("  분석:", p.title[:60])
            cards.append(card_html(p, analyze(p)))
        except Exception as err:
            print("  (건너뜀)", err)

    if not cards:
        print("생성된 카드가 없습니다 — 종료")
        return

    today = datetime.date.today().isoformat()
    body = "\n".join(cards) + ('<footer>AI가 만든 자동 초안입니다. 사실관계·수치는 '
                               '원문(arXiv)으로 확인하세요. PI 검토 후 활용.</footer>')
    (OUT_DIR / f"{today}.html").write_text(page(f"다이제스트 {today}", today, body),
                                           encoding="utf-8")
    print("작성:", OUT_DIR / f"{today}.html")

    files = sorted(OUT_DIR.glob("*.html"), reverse=True)
    arch = ('<ul class="arch">'
            + "".join(f'<li><a href="digests/{f.name}">{f.stem}</a></li>' for f in files)
            + "</ul><footer>주차를 클릭하면 그 주 다이제스트가 열립니다.</footer>")
    pathlib.Path("index.html").write_text(page("다이제스트 · 아카이브", "아카이브", arch),
                                          encoding="utf-8")
    print("index.html 갱신 — 총", len(files), "주차")


if __name__ == "__main__":
    main()
