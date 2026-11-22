import sys, openpyxl, glob, re, json
files = {'1': glob.glob(sys.argv[1]+'/891f*')[0], '2': glob.glob(sys.argv[1]+'/785c*')[0],
         '3': glob.glob(sys.argv[1]+'/e14b*')[0]}
TIME = re.compile(r'^\s*\d{1,2}[.:]\d{2}\s*-\s*\d{1,2}[.:]\d{2}')
out = []
for fcourse, f in files.items():
    wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        title = ''; date=''
        for r in rows[:12]:
            for v in r:
                if isinstance(v,str):
                    if 'курс' in v and 'специальн' in v and not title: title = re.sub(r'\s+',' ',v).strip()
                    if re.search(r'20\d\d\s*г', v): date = v
        if not title or '2026' not in date: continue
        m = re.match(r'(\d) курса', title)
        course = m.group(1)
        lessons = []
        tc = next((j for r in rows[:16] for j,v in enumerate(r) if isinstance(v,str) and v.strip()=='Время'), 1)
        for r in rows:
            r = list(r)+[None]*8
            t=r[tc]
            if t and isinstance(t,str) and TIME.match(t) and r[tc+1]:
                lessons.append((str(r[tc+1]).strip(), str(r[tc+2] or '').strip()))
        out.append({'sheet': ws.title.strip(), 'file': fcourse, 'course': course, 'title': title, 'lessons': lessons})
# МВД
f = glob.glob(sys.argv[1]+'/d7b1*')[0]
wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
for ws in wb.worksheets:
    m = re.match(r'\s*(\d)', ws.title)
    if 'МВД' not in ws.title: continue
    rows = list(ws.iter_rows(values_only=True))
    lessons=[]
    for r in rows:
        r=list(r)+[None]*6
        if r[1] and isinstance(r[1],str) and TIME.match(r[1]) and r[2]:
            lessons.append((str(r[2]).strip(), ''))
    hdr = [v for r in rows[:10] for v in r if isinstance(v,str) and '6B' in v]
    out.append({'sheet': ws.title.strip(), 'file':'mvd', 'course': m.group(1), 'title': (hdr[0] if hdr else ''), 'lessons': lessons})
json.dump(out, open('raw.json','w'), ensure_ascii=False, indent=1)
for o in out: print(o['file'], o['course'], o['sheet'], len(o['lessons']), '|', o['title'][:110])
