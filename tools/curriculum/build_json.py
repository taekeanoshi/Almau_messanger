import json, re, collections, sys
raw = json.load(open('raw.json'))
SPEC = {
 'Digital filmmaking': 'Digital Filmmaking', 'New Media': 'New Media', 'Связь с общественностью': 'Связь с общественностью',
}
MAP = {
 ('1','DF1кРО'):['Digital Filmmaking'], ('1','NM 1ро'):['New Media'], ('1','СО 1кРО'):['Связь с общественностью'],
 ('1','ЮР1кРО'):['Юриспруденция'], ('1','УиСМ1кРО'):['Урбанистика и сити-менеджмент'],
 ('1','МОиЭ1кРО'):['Международные отношения и экономика'], ('1','ПС 1кРО'):['Психология'],
 ('1','РДиГБ 1кРО'):['Ресторанное дело и гостиничный бизнес'], ('1','ТУР 1кРО'):['Tourism and Events Management'],
 ('1','ИС, PM- 1кРО'):['Информационные системы','Product Management'], ('1','SE,DS 1кРО'):['Software Engineering','Data Science'],
 ('1','Con.MG,DA.Fin1к ро'):['Content, Marketing and Data Analysis','Fintech and Artificial Intelligence'],
 ('1','МН 1кРО'):['Менеджмент'], ('1','СМ 1кРО'):['Спортивный менеджмент'], ('1','МГ 1кРО'):['Маркетинг'],
 ('1','ЛОГ 1к ро'):['Логистика'], ('1','ФН 1кРО'):['Финансы'], ('1','УиА 1кРО'):['Учёт и аудит'],
 ('1','БАиЭ 1кРО'):['Бизнес-аналитика и экономика'], ('1','БАОП  1кРО'):['Бизнес-администрирование в предпринимательстве'],
 ('1','DC  1кРО'):['Digital Commerce'],
 ('2','NМ 2кро'):['New Media'], ('2','DFМ 2кРО'):['Digital Filmmaking'], ('2','СО 2кРО'):['Связь с общественностью'],
 ('2','УиСМ 2кРО'):['Урбанистика и сити-менеджмент'], ('2','ПС 2кРО'):['Психология'], ('2','ЮР 2кРО'):['Юриспруденция'],
 ('2','МОиЭ 2кРО'):['Международные отношения и экономика'], ('2','РДиГБ 2кРО'):['Ресторанное дело и гостиничный бизнес'],
 ('2','ТУР 2кРО'):['Tourism and Events Management'], ('2','ИС, PM 2кРО'):['Информационные системы','Product Management'],
 ('2','SE,DS 2кРО'):['Software Engineering','Data Science'], ('2','МН 2кРО+НиШ'):['Менеджмент'],
 ('2','СМ 2кРО'):['Спортивный менеджмент'], ('2','МГ 2кРО'):['Маркетинг'], ('2','ЛОГ 2к ро'):['Логистика'],
 ('2','ФН 2кРО'):['Финансы'], ('2','УиА 2кРО'):['Учёт и аудит'], ('2','БАиЭ 2кРО'):['Бизнес-аналитика и экономика'],
 ('2','БАОП  2кРО'):['Бизнес-администрирование в предпринимательстве'],
 ('3','NM3кРО'):['New Media'], ('3','DFM3кРО'):['Digital Filmmaking'], ('3','СО 3кРО'):['Связь с общественностью'],
 ('3','ПС 3кРО'):['Психология'], ('3','ЮР3кРО + ТиПО'):['Юриспруденция'], ('3','МОиЭ 3кРО'):['Международные отношения и экономика'],
 ('3','РДиГБ 3кРО + ТиПО'):['Ресторанное дело и гостиничный бизнес'], ('3','ТУР 3кРО + ТиПО'):['Tourism and Events Management'],
 ('3','ИС.DS 3кРО + ТиПО'):['Информационные системы','Data Science'], ('3','SE.PM 3кРО, АО'):['Software Engineering','Product Management'],
 ('3','3к CMDA. FINtech'):['Content, Marketing and Data Analysis','Fintech and Artificial Intelligence'],
 ('3','МН 3кРО+ТиПО'):['Менеджмент'], ('3','СМ 3кРО'):['Спортивный менеджмент'], ('3','МГ 3кРО + ТиПО'):['Маркетинг'],
 ('3','ЛОГ 3к ро + ТиПО'):['Логистика'], ('3','ФН 3кРО + ТиПО'):['Финансы'], ('3','УиА 3кРО + ТиПО'):['Учёт и аудит'],
 ('3','БАиЭ 3кРО + ТиПО'):['Бизнес-аналитика и экономика'], ('3','БАОП  3кРО + ТиПО'):['Бизнес-администрирование в предпринимательстве'],
}
MVD = 'Вычислительная техника и ПО (МВД)'
U = 'А-ЯЁӘҒҚҢӨҰҮҺІ'; L = 'а-яёәғқңөұүһі'
NAME = re.compile(rf'(?<![{U}{L}])([{U}][{L}]{{2,}}(?:-[{U}][{L}]+)?)\s+([{U}])\s?\.?\s?(?:([{U}])(?![{L}])\s?\.?)?')
BAD = {'Академия','Дуал','Практикум','Применение','Центральной','Платонус','Вакансия','Модуль','Занятия','Платонусе','Минор','Введение','Иностранный','Казахский','Поток','Дуальный','Для'}
def teachers_in(text):
    out = []
    for m in NAME.finditer(text):
        sur, i1, i2 = m.groups()
        if sur in BAD or sur.lower().startswith('вакан'): continue
        out.append(f"{sur} {i1}." + (f"{i2}." if i2 else ""))
    return out
CODE = re.compile(r'\s*\b[A-ZА-Яa-z]{1,14}\s*\d{6,9}[\d-]*-CH\b,?')
def split_slash(s):
    depth = 0
    for i, ch in enumerate(s):
        depth += ch == '('; depth -= ch == ')'
        if ch == '/' and depth == 0 and (s[i-1:i] == ' ' or s[i+1:i+2] == ' '):
            return s[:i]
    return s
def clean(subj):
    s = re.sub(r'\s+', ' ', subj).strip()
    if s in (',,', '') or 'ритейк' in s: return None
    if s.startswith('Minor'):
        s = re.split(r'\s+(?=Minor \d)', s)[0]
        m = re.match(r'(Minor \d)\s*-\s*([^()]+?)\s*(\(|$)', s)
        if m: return f"{m.group(1)}: {m.group(2).strip()}"
    s = CODE.sub(' | ', s).split(' | ')[0]
    s = split_slash(s)
    s = re.sub(r'\((?:поток|для|Для|Занятия|дуальный|\d)[^)]*\)', '', s)
    s = re.sub(r'\+.*$', '', s)
    s = re.split(r'\s,', s)[0]
    s = re.sub(rf'\s[{U}][{L}]+\s[{U}]\.?$', '', s)
    s = s.replace('Развитие потребителей RP', 'Развитие потребителей')
    s = re.sub(rf',\s*[{U}][{L}]+\s+[{U}]\.?.*$', '', s)  # ", Рамазанов Е.Т."
    s = re.sub(r'\s+', ' ', s).strip(' ,.;-/')
    s = s.replace('когнетивно', 'когнитивно').replace('Когнитивно-поведенческой', 'Когнитивно-поведенческая')
    s = s.replace('Бизнес и технология (BT ACCA)', 'Бизнес и технологии (BT ACCA)').replace('TX АССА','TX ACCA').replace('(MA ACCA )','(MA ACCA)').replace('(FA ACCA )','(FA ACCA)')
    s = s.replace('Статистика с Excel, R /Python', 'Статистика с Excel и R/Python')
    if s == 'Критическое мышление': s = 'Критическое мышление, креативное письмо'
    if s == 'Иностранный язык 2 (для ТиПО)': s = 'Иностранный язык 2'
    return s or None
spec = collections.defaultdict(lambda: collections.defaultdict(list))
teach = collections.defaultdict(set)
for o in raw:
    if o['file'] == 'mvd':
        names = [MVD]
    else:
        names = MAP.get((o['course'], o['sheet']))
        if not names: print('SKIP', o['course'], o['sheet'], file=sys.stderr); continue
    for subj, t in o['lessons']:
        c = clean(subj)
        if not c: continue
        for n in names:
            if c not in spec[n][o['course']]: spec[n][o['course']].append(c)
        src = t + ' ' + (subj if o['file'] == 'mvd' else ' '.join(re.findall(rf',\s*[{U}][{L}]+\s+[{U}]\.[^,(]*', subj)))
        for tn in teachers_in(src): teach[c].add(tn.replace('ё','е').replace('Бакироваа','Бакирова').replace('Кирилова','Кириллова'))
for c, v in teach.items():
    for n in list(v):
        if any(o != n and o.startswith(n) for o in v): v.discard(n)
glob_names = {n for v in teach.values() for n in v}
alias = {}
for n in glob_names:
    longer = [o for o in glob_names if o != n and o.startswith(n)]
    if len(longer) == 1: alias[n] = longer[0]
for c in teach: teach[c] = {alias.get(n, n) for n in teach[c]}
allteach = sorted({t for v in teach.values() for t in v})
out = {
  "source": "Расписание AlmaU, осенний семестр 2026-2027, русское отделение (файлы учебного отдела)",
  "specialties": [{"name": n, "courses": {k: v for k, v in sorted(cs.items())}} for n, cs in sorted(spec.items())],
  "teachers": {s: sorted(v) for s, v in sorted(teach.items()) if v},
}
json.dump(out, open('curriculum.json','w'), ensure_ascii=False, indent=1)
subs = {s for n in spec.values() for v in n.values() for s in v}
print('specialties', len(spec), 'subjects', len(subs), 'teachers', len(allteach), 'subjects w/o teacher', len(subs - set(out['teachers'])))
for n, cs in sorted(spec.items()): print(n, {k: len(v) for k, v in cs.items()})
print(sorted(subs))
print(allteach)
