import zipfile, re, os, json, hashlib

from xml.etree import ElementTree as ET

NS='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

def colnum(ref):
    s=re.match(r'([A-Z]+)',ref).group(1); n=0
    for ch in s:n=n*26+ord(ch)-64
    return n

def verify(path, ledger_path):
    with zipfile.ZipFile(path) as z:
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        names=[s.attrib['name'] for s in wb.find(NS+'sheets')]
        rel=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rels={r.attrib['Id']:r.attrib['Target'] for r in rel}
        sheets=[]
        for s in wb.find(NS+'sheets'):
            target=rels[s.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            target='xl/'+target.lstrip('/') if not target.startswith('xl/') else target
            target=target.replace('xl/xl/','xl/')
            sheets.append(ET.fromstring(z.read(target)))
        def cells(sheet):
            out={}
            for c in sheet.findall('.//'+NS+'c'):
                ref=c.attrib.get('r',''); v=c.find(NS+'v')
                if ref and v is not None and v.text:
                    try: out[(int(re.search(r'\d+',ref).group()),colnum(ref))]=float(v.text)
                    except ValueError: pass
            return out
        pc=cells(sheets[0]); ec=cells(sheets[-1]); plan_kwh=sum(pc.get((r,c),0.0) for r in range(2,336) for c in range(2,146)); plan_cost=sum(pc.get((r,147),0.0) for r in range(2,336)); em_kwh=sum(v for (r,c),v in ec.items() if c==3 and r>=2)
        l=json.load(open(ledger_path,encoding='utf8'))['summary']
        print(os.path.basename(path),'sheet_count',len(names),'plan_kwh',plan_kwh,'plan_cost',plan_cost,'em_kwh',em_kwh,'ledger_plan_kwh',l['planned_kwh'],'ledger_plan_cost',l['plan_cost'],'ledger_em_kwh',l['emergency_kwh'],'hash',hashlib.sha256(open(path,'rb').read()).hexdigest())
