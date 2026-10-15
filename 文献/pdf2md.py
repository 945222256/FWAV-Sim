# -*- coding: utf-8 -*-
"""PDF→Markdown v2：行首标题识别（IEEE罗马数字/字母小节 + Springer/Elsevier数字编号），
标题后粘连的正文拆回正文流；页码标记；图表题注索引。"""
import re, sys, pathlib
import pymupdf

LIG = {"\ufb00":"ff","\ufb01":"fi","\ufb02":"fl","\ufb03":"ffi","\ufb04":"ffl",
       "\u2010":"-","\u2011":"-","\u00ad":""}

# IEEE:  "I. INTRODUCTION"（全大写，后面可能粘连右栏正文）
IEEE_SEC  = re.compile(r"^([IVXLC]{1,6})\.\s+([A-Z][A-Z0-9 ,\-+/&'()]*(?:[A-Z0-9)]))(?=\s{2,}|$)")
IEEE_SUB  = re.compile(r"^([A-Z])\.\s+([A-Z][A-Za-z0-9 ,\-()']{2,60}?)(?=\s{2,}|$)")
# Springer/Elsevier: "2  Flapping-wing flying robot prototype..." / "4.3  Autonomous flight..."
NUM_SEC   = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?)\.?\s{2,}([A-Z][^\n]{3,80}?)(?=\s{2,}|$)")
FIG = re.compile(r"^(Fig(?:ure)?\.?\s*\d+[:.]?\s*.{0,250})")
TAB = re.compile(r"^(TABLE\s+[IVXLC]+.{0,150})")
SPECIAL = {"ABSTRACT","REFERENCES","APPENDIX","ACKNOWLEDGMENT","INDEX TERMS","NOMENCLATURE"}

def clean(t): 
    for k,v in LIG.items(): t=t.replace(k,v)
    return t

def convert(pdf_path):
    doc = pymupdf.open(pdf_path)
    out, figs, tabs, secs = [], [], [], []
    for i, page in enumerate(doc, 1):
        raw = page.get_text("text", sort=True)
        out.append(f"\n\n---\n<!-- 第 {i} 页 / 共 {len(doc)} 页 -->\n")
        for ln in raw.splitlines():
            s = clean(ln.strip())
            if not s: continue
            hit = None; rest = ""
            m = IEEE_SEC.match(s)
            if m and len(m.group(2).split()) >= 1:
                hit, rest = f"{m.group(1)}. {m.group(2)}", s[m.end():].strip()
            if not hit:
                m = NUM_SEC.match(s)
                if m and not s[0].islower():
                    hit, rest = f"{m.group(1)} {m.group(2)}", s[m.end():].strip()
            if not hit:
                m = IEEE_SUB.match(s)
                if m and m.group(2)[0].isupper():
                    hit, rest = f"{m.group(1)}. {m.group(2)}", s[m.end():].strip()
            if not hit and s.upper() in SPECIAL:
                hit, rest = s, ""
            if hit:
                out.append(f"\n## {hit}"); secs.append((i, hit))
                if rest: out.append(rest)
                continue
            if FIG.match(s):
                figs.append((i, s)); out.append(f"\n> **[图]** {s}"); continue
            if TAB.match(s):
                tabs.append((i, s)); out.append(f"\n> **[表]** {s}"); continue
            out.append(s)
    return "\n".join(out), secs, figs, tabs, len(doc)

if __name__ == "__main__":
    src = pathlib.Path(sys.argv[1]); meta = sys.argv[2]
    body, secs, figs, tabs, n = convert(str(src))
    md = src.with_suffix(".md")
    md.write_text(f"{meta}\n\n{body}", encoding="utf-8")
    print(f"OK {md.name}: {n}页 {len(body)}字 | 章节{len(secs)} 图题{len(figs)} 表题{len(tabs)}")
    for p,t in secs: print(f"  p{p}  {t[:70]}")
