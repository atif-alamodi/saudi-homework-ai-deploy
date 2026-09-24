import io, os, re, asyncio, json, requests
from pathlib import Path
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse

app=FastAPI(title='المساعد الدراسي السعودي')
TIMEOUT=float(os.getenv('MODEL_TIMEOUT','18'))
API_KEY=os.getenv('LLM_API_KEY','local')
# JSON example: [{"name":"Llama","url":"https://...","model":"..."}, ...]
try: MODELS=json.loads(os.getenv('LLM_ENDPOINTS_JSON','[]'))
except: MODELS=[]

GRADES=['الأول الابتدائي','الثاني الابتدائي','الثالث الابتدائي','الرابع الابتدائي','الخامس الابتدائي','السادس الابتدائي','الأول المتوسط','الثاني المتوسط','الثالث المتوسط','الأول الثانوي','الثاني الثانوي','الثالث الثانوي']
SUBS={'رياضيات':'الرياضيات','علوم':'العلوم','فيزياء':'الفيزياء','كيمياء':'الكيمياء','أحياء':'الأحياء','احياء':'الأحياء','عربي':'اللغة العربية','لغتي':'لغتي','انجليزي':'اللغة الإنجليزية','إنجليزي':'اللغة الإنجليزية','اسلاميات':'الدراسات الإسلامية','إسلاميات':'الدراسات الإسلامية'}

def classify(t):
 g=next((x for x in GRADES if x in t),'غير محدد'); s=next((v for k,v in SUBS.items() if k in t),'غير محدد')
 st='الابتدائي' if 'الابتدائي' in g else 'المتوسط' if 'المتوسط' in g else 'الثانوي' if 'الثانوي' in g else 'غير محدد'
 return {'stage':st,'grade':g,'subject':s}

def parse_file(name,b):
 ext=Path(name).suffix.lower()
 try:
  if ext=='.pdf':
   import fitz; d=fitz.open(stream=b,filetype='pdf'); return '\n'.join(p.get_text() for p in d)
  if ext=='.docx':
   from docx import Document; d=Document(io.BytesIO(b)); return '\n'.join(p.text for p in d.paragraphs)
  if ext in {'.txt','.md','.csv','.json'}: return b.decode('utf-8','replace')
  if ext in {'.png','.jpg','.jpeg','.webp'}:
   from PIL import Image; import pytesseract; return pytesseract.image_to_string(Image.open(io.BytesIO(b)),lang='ara+eng')
 except: return ''
 return ''

def math_answer(q):
 try:
  import sympy as sp
  q=q.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩×÷−','0123456789*/-')).replace('^','**')
  m=re.search(r'([0-9a-zA-Z_+\-*/().\s*]+)=([0-9a-zA-Z_+\-*/().\s*]+)',q)
  if m and any(c.isalpha() for c in m.group(0)):
   l,r=sp.sympify(m.group(1)),sp.sympify(m.group(2)); sy=list(l.free_symbols|r.free_symbols)
   sol=sp.solve(sp.Eq(l,r),sy,dict=True)
   if sol: return 'الحل المحلي السريع:\n'+ '، '.join('؛ '.join(f'{k} = {sp.simplify(v)}' for k,v in row.items()) for row in sol)
  for x in sorted(re.findall(r'[0-9][0-9+\-*/().\s*]{2,}',q),key=len,reverse=True):
   v=sp.sympify(x.strip())
   if not v.free_symbols:return f'الحل المحلي السريع:\n{x.strip()} = {sp.simplify(v)}'
 except: pass

def extract(q,txt):
 if not txt.strip():return None
 w=[x for x in re.findall(r'[\w\u0600-\u06ff]+',q.lower()) if len(x)>2]; ps=[p.strip() for p in re.split(r'\n{2,}|(?<=[.!؟])\s+',txt) if len(p.strip())>25]
 ranked=sorted(((sum(x in p.lower() for x in w),p) for p in ps),reverse=True)
 a=[p for n,p in ranked if n][:3] or ps[:2]
 return 'أقرب معلومات من الملف:\n\n'+'\n\n'.join(a) if a else None

def call_model(m,q,ctx):
 h={'Authorization':f'Bearer {API_KEY}','Content-Type':'application/json'}
 p={'model':m['model'],'messages':[{'role':'system','content':'أنت معلم خبير بالمناهج السعودية. حل بدقة وبخطوات واضحة.'},{'role':'user','content':q+'\n\nالسياق:\n'+ctx[:8000]}],'temperature':0.1,'max_tokens':1200}
 r=requests.post(m['url'].rstrip('/')+'/v1/chat/completions',headers=h,json=p,timeout=TIMEOUT);r.raise_for_status();return r.json()['choices'][0]['message']['content']

@app.get('/health')
def health():return {'ok':True,'models':[m.get('name') for m in MODELS]}
@app.get('/',response_class=HTMLResponse)
def home():return HTMLResponse(HTML)

@app.post('/solve')
async def solve(question:str=Form(''),files:list[UploadFile]=File(default=[])):
 texts=[];names=[]
 for f in files[:5]:
  b=await f.read(); t=parse_file(f.filename or 'file',b)
  if t.strip():texts.append(t[:30000]);names.append(f.filename)
 alltxt='\n'.join(texts); q=(question or alltxt[:4000]).strip()
 if not q:return {'ok':False,'error':'اكتب سؤالًا أو ارفع ملفًا'}
 local=math_answer(q); ext=extract(q,alltxt); panel=[]
 async def one(m):
  try:return {'name':m.get('name','LLM'),'ok':True,'text':await asyncio.to_thread(call_model,m,q,alltxt)}
  except Exception as e:return {'name':m.get('name','LLM'),'ok':False,'error':str(e)[:160]}
 if MODELS: panel=await asyncio.gather(*(one(m) for m in MODELS[:5]))
 good=[x for x in panel if x['ok']]
 ans=(good[0]['text'] if good else None) or local or ext or 'الخدمة تعمل، لكن هذا السؤال يحتاج نموذجًا لغويًا متصلًا. الحل الحسابي وقراءة الملفات يعمل٧ن مباشرة.'
 return {'ok':True,'meta':classify(q+' '+alltxt[:1000]),'answer':ans,'models':panel,'files':names}

HTML='''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>المساعد الدراسي السعودي</title><style>body{font-family:Tahoma,Arial;background:#f5f7fb;margin:0;color:#172033}.w{max-width:850px;margin:auto;padding:20px}.c{background:white;border:1px solid #e5e7eb;border-radius:20px;padding:18px;margin:14px 0}h1{text-align:center}textarea,input{width:100%;box-sizing:border-box;padding:13px;border:1px solid #ddd;border-radius:13px;margin:8px 0}textarea{min-height:140px}button{background:#0f766e;color:white;border:0;border-radius:13px;padding:13px 22px;font-weight:bold;font-size:16px}.a{white-space:pre-wrap;line-height:2}.p{display:inline-block;background:#ecfdf5;padding:5px 9px;border-radius:20px;margin:3px}.muted{color:#6b7280}.hide{display:none}</style></head><body><div class="w"><h1>🇸🇦 المساعد الدراسي السعودي</h1><p class="muted" style="text-align:center">ارفع الواجب أو اكتب السؤال</p><div class="c"><form id="f"><textarea name="question" placeholder="مثال: رياضيات الثاني المتوسط: حل 2x+3=11"></textarea><input type="file" name="files" multiple accept=".pdf,.docx,.txt,.png,.jpg,.jpeg,.webp"><button id="b">حل الواجب</button> <span id="s"></span></form></div><div id="r" class="c hide"><div id="m"></div><h2>الحل</h2><div id="a" class="a"></div><details><summary>النماذج</summary><div id="d"></div></details></div></div><script>let f=document.querySelector('#f'),b=document.querySelector('#b'),s=document.querySelector('#s'),r=document.querySelector('#r');f.onsubmit=async e=>{e.preventDefault();b.disabled=true;s.textContent='جاري الحل…';r.classList.add('hide');try{let z=await fetch('/solve',{method:'POST',body:new FormData(f)}),x=await z.json();if(!x.ok)throw Error(x.error);let m=x.meta||{};document.querySelector('#m').innerHTML=['stage','grade','subject'].map(k=>`<span class=p>${m[k]||''}</span>`).join('');document.querySelector('#a').textContent=x.answer;document.querySelector('#d').innerHTML=(x.models||[]).map(v=>`<p><b>${v.name} ${v.ok?'✓':'⚠'}</b><br>${(v.text||v.error||'').slice(0,800)}</p>`).join('')||'<p>لا توجد نماذج خارجية متصلة حاليًا.</p>';r.classList.remove('hide');s.textContent='تم'}catch(e){s.textContent=e.message}finally{b.disabled=false}};</script></body></html>'''
