import io, os, re, asyncio, json, requests
from pathlib import Path
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse

app = FastAPI(title='المساعد الدراسي')
TIMEOUT = float(os.getenv('MODEL_TIMEOUT', '22'))
API_KEY = os.getenv('LLM_API_KEY', 'local')
PUBLIC_FALLBACK_URL = os.getenv('PUBLIC_FALLBACK_URL', 'https://text.pollinations.ai/openai')
PUBLIC_FALLBACK_MODEL = os.getenv('PUBLIC_FALLBACK_MODEL', 'openai-fast')
try:
    MODELS = json.loads(os.getenv('LLM_ENDPOINTS_JSON', '[]'))
except Exception:
    MODELS = []

GRADE_STAGE = {
    'الأول الابتدائي':'الابتدائي','الثاني الابتدائي':'الابتدائي','الثالث الابتدائي':'الابتدائي',
    'الرابع الابتدائي':'الابتدائي','الخامس الابتدائي':'الابتدائي','السادس الابتدائي':'الابتدائي',
    'الأول المتوسط':'المتوسط','الثاني المتوسط':'المتوسط','الثالث المتوسط':'المتوسط',
    'الأول الثانوي':'الثانوي','الثاني الثانوي':'الثانوي','الثالث الثانوي':'الثانوي',
}

CURRICULUM = {
    'الابتدائي': [
        'القرآن الكريم والدراسات الإسلامية','لغتي','الرياضيات','العلوم','اللغة الإنجليزية',
        'الدراسات الاجتماعية','المهارات الرقمية','المهارات الحياتية والأسرية','التربية الفنية','التربية البدنية'
    ],
    'المتوسط': [
        'القرآن الكريم والدراسات الإسلامية','لغتي الخالدة','الرياضيات','العلوم','اللغة الإنجليزية',
        'الدراسات الاجتماعية','المهارات الرقمية','المهارات الحياتية والأسرية','التربية الفنية','التربية البدنية'
    ],
    'الثانوي': [
        'الدراسات الإسلامية','اللغة العربية','اللغة الإنجليزية','الرياضيات','الأحياء','الكيمياء','الفيزياء',
        'التقنية الرقمية','الدراسات الاجتماعية','التفكير الناقد','التربية الصحية والبدنية','إدارة الأعمال'
    ],
}

SUBJECT_ALIASES = {
    'رياضيات':'الرياضيات','الرياضيات':'الرياضيات','علوم':'العلوم','العلوم':'العلوم',
    'فيزياء':'الفيزياء','الفيزياء':'الفيزياء','كيمياء':'الكيمياء','الكيمياء':'الكيمياء',
    'أحياء':'الأحياء','احياء':'الأحياء','الأحياء':'الأحياء','لغتي':'لغتي','لغتي الخالدة':'لغتي الخالدة',
    'عربي':'اللغة العربية','لغة عربية':'اللغة العربية','إنجليزي':'اللغة الإنجليزية','انجليزي':'اللغة الإنجليزية',
    'الدراسات الإسلامية':'الدراسات الإسلامية','اسلاميات':'الدراسات الإسلامية','إسلاميات':'الدراسات الإسلامية',
    'دراسات اجتماعية':'الدراسات الاجتماعية','اجتماعيات':'الدراسات الاجتماعية','مهارات رقمية':'المهارات الرقمية',
    'تقنية رقمية':'التقنية الرقمية','تفكير ناقد':'التفكير الناقد','إدارة الأعمال':'إدارة الأعمال',
}

SCIENCE_INDEPENDENT = {'الكيمياء','الفيزياء','الأحياء'}


def detect_subject(text: str):
    low = text.lower()
    # longest labels first so "لغتي الخالدة" wins over "لغتي"
    for k in sorted(SUBJECT_ALIASES, key=len, reverse=True):
        if k.lower() in low:
            return SUBJECT_ALIASES[k]
    return None


def curriculum_validate(stage: str, grade: str, subject: str, text: str):
    errors = []
    if grade not in GRADE_STAGE:
        errors.append('الصف غير محدد أو غير معروف.')
    elif GRADE_STAGE[grade] != stage:
        errors.append(f'الصف «{grade}» لا ينتمي إلى المرحلة «{stage}».')
    if subject not in CURRICULUM.get(stage, []):
        if stage in {'الابتدائي','المتوسط'} and subject in SCIENCE_INDEPENDENT:
            errors.append(f'«{subject}» ليست مادة مستقلة في المرحلة {stage}. اختر «العلوم» لهذه المرحلة.')
        else:
            errors.append(f'المادة «{subject}» غير مفعلة لهذه المرحلة في النظام.')
    mentioned = detect_subject(text)
    if mentioned and mentioned != subject:
        # hard curriculum separation, especially for chemistry/physics/biology in lower stages
        if stage in {'الابتدائي','المتوسط'} and mentioned in SCIENCE_INDEPENDENT:
            errors.append(f'السؤال مصنف كـ«{mentioned}»، وهذه ليست مادة مستقلة في المرحلة {stage}. لا يمكن حله تحت هذا الصف. استخدم «العلوم» أو اختر مرحلة صحيحة.')
        else:
            errors.append(f'السؤال يبدو تابعًا لمادة «{mentioned}» بينما المادة المختارة هي «{subject}». صحح المادة قبل الحل.')
    return errors


def parse_file(name, b):
    ext = Path(name).suffix.lower()
    try:
        if ext == '.pdf':
            import fitz
            d = fitz.open(stream=b, filetype='pdf')
            return '\n'.join(p.get_text() for p in d)
        if ext == '.docx':
            from docx import Document
            d = Document(io.BytesIO(b))
            return '\n'.join(p.text for p in d.paragraphs)
        if ext in {'.txt','.md','.csv','.json'}:
            return b.decode('utf-8','replace')
        if ext in {'.png','.jpg','.jpeg','.webp'}:
            from PIL import Image
            import pytesseract
            return pytesseract.image_to_string(Image.open(io.BytesIO(b)), lang='ara+eng')
    except Exception:
        return ''
    return ''


def normalize_math(s: str):
    s = s.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩×÷−','0123456789*/-')).replace('^','**')
    sup = {'⁰':'0','¹':'1','²':'2','³':'3','⁴':'4','⁵':'5','⁶':'6','⁷':'7','⁸':'8','⁹':'9'}
    s = re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹]+', lambda m: '**' + ''.join(sup[c] for c in m.group(0)), s)
    s = re.sub(r'(?<=\d)(?=[A-Za-z])', '*', s)
    s = re.sub(r'(?<=[A-Za-z0-9)])(?=\()', '*', s)
    return s


def linear_steps(q: str):
    """Generate explicit school-style steps for one-variable linear equations when possible."""
    try:
        import sympy as sp
        nq = normalize_math(q)
        m = re.search(r'([0-9a-zA-Z_+\-*/().\s*]+)=([0-9a-zA-Z_+\-*/().\s*]+)', nq)
        if not m:
            return None
        left, right = sp.sympify(m.group(1)), sp.sympify(m.group(2))
        syms = list(left.free_symbols | right.free_symbols)
        if len(syms) != 1:
            return None
        x = syms[0]
        expr = sp.expand(left - right)
        poly = sp.Poly(expr, x)
        if poly.degree() != 1:
            return None
        a, b = poly.all_coeffs()
        sol = sp.simplify(-b/a)
        original = f'{sp.sstr(left)} = {sp.sstr(right)}'
        steps = [
            '### فهم السؤال',
            f'نريد إيجاد قيمة **{x}** التي تحقق المعادلة: `{original}`.',
            '### الحل خطوة بخطوة',
            f'1. ننقل جميع الحدود إلى طرف واحد ونبسط: `{sp.sstr(expr)} = 0`.',
        ]
        if b != 0:
            steps.append(f'2. نعزل الحد الذي يحتوي على {x}: `{sp.sstr(a*x)} = {sp.sstr(-b)}`.')
            steps.append(f'3. نقسم الطرفين على `{sp.sstr(a)}`: `{x} = {sp.sstr(sol)}`.')
        else:
            steps.append(f'2. بقسمة الطرفين على `{sp.sstr(a)}` نحصل على: `{x} = {sp.sstr(sol)}`.')
        check_l = sp.simplify(left.subs(x, sol)); check_r = sp.simplify(right.subs(x, sol))
        steps += [
            '### التحقق',
            f'بالتعويض بـ **{x} = {sp.sstr(sol)}**: الطرف الأيسر = `{sp.sstr(check_l)}` والطرف الأيمن = `{sp.sstr(check_r)}`، إذن الحل صحيح.',
            '### الإجابة النهائية',
            f'`{x} = {sp.sstr(sol)}`'
        ]
        return '\n\n'.join(steps)
    except Exception:
        return None


def arithmetic_steps(q: str):
    try:
        import sympy as sp
        nq = normalize_math(q)
        candidates = sorted(re.findall(r'[0-9][0-9+\-*/().\s*]{2,}', nq), key=len, reverse=True)
        for raw in candidates:
            raw = raw.strip()
            v = sp.sympify(raw)
            if not v.free_symbols:
                return (f'### فهم السؤال\nنحسب التعبير: `{raw}`.\n\n'
                        f'### الحل خطوة بخطوة\n1. نطبق ترتيب العمليات: الأقواس، ثم الضرب والقسمة، ثم الجمع والطرح.\n'
                        f'2. بعد التبسيط نحصل على: `{sp.simplify(v)}`.\n\n'
                        f'### الإجابة النهائية\n`{sp.simplify(v)}`')
    except Exception:
        pass
    return None


def polynomial_steps(q: str):
    try:
        import sympy as sp
        nq = normalize_math(q)
        m = re.search(r'(\([^()\n]+\)\s*\*\*\s*\d+)', nq)
        if not m:
            return None
        raw = m.group(1).replace(' ', '')
        expr = sp.sympify(raw)
        if not expr.free_symbols:
            return None
        expanded = sp.expand(expr)
        if expanded == expr:
            return None
        if isinstance(expr, sp.Pow) and expr.exp.is_Integer:
            n = int(expr.exp)
            base = sp.expand(expr.base)
            terms = list(base.as_ordered_terms())
            if n == 2 and len(terms) == 2:
                a, b = terms
                a2, mid, b2 = sp.expand(a**2), sp.expand(2*a*b), sp.expand(b**2)
                return (
                    f'### فهم السؤال\nالمطلوب تبسيط/توسيع التعبير: `{sp.sstr(expr)}`.\n\n'
                    '### القاعدة\nنستخدم مربع مجموع حدين: `(a + b)^2 = a^2 + 2ab + b^2`.\n\n'
                    '### الحل خطوة بخطوة\n'
                    f'1. نحدد الحدين: `a = {sp.sstr(a)}` و `b = {sp.sstr(b)}`.\n'
                    f'2. مربع الحد الأول: `({sp.sstr(a)})^2 = {sp.sstr(a2)}`.\n'
                    f'3. الحد الأوسط: `2({sp.sstr(a)})({sp.sstr(b)}) = {sp.sstr(mid)}`.\n'
                    f'4. مربع الحد الثاني: `({sp.sstr(b)})^2 = {sp.sstr(b2)}`.\n'
                    f'5. نجمع الحدود: `{sp.sstr(a2)} + {sp.sstr(mid)} + {sp.sstr(b2)}`.\n'
                    f'6. بعد الترتيب والتبسيط: `{sp.sstr(expanded)}`.\n\n'
                    '### التحقق\nتم التحقق جبريًا بتوسيع التعبير الأصلي ومقارنته بالناتج.\n\n'
                    f'### الإجابة النهائية\n`{sp.sstr(expanded)}`'
                )
            if n == 3 and len(terms) == 2:
                a, b = terms
                t1,t2,t3,t4 = sp.expand(a**3), sp.expand(3*a**2*b), sp.expand(3*a*b**2), sp.expand(b**3)
                return (
                    f'### فهم السؤال\nالمطلوب توسيع: `{sp.sstr(expr)}`.\n\n'
                    '### القاعدة\nنستخدم مكعب مجموع حدين: `(a+b)^3 = a^3 + 3a^2b + 3ab^2 + b^3`.\n\n'
                    '### الحل خطوة بخطوة\n'
                    f'1. `a = {sp.sstr(a)}` و `b = {sp.sstr(b)}`.\n'
                    f'2. `a^3 = {sp.sstr(t1)}`.\n'
                    f'3. `3a^2b = {sp.sstr(t2)}`.\n'
                    f'4. `3ab^2 = {sp.sstr(t3)}`.\n'
                    f'5. `b^3 = {sp.sstr(t4)}`.\n'
                    f'6. بجمع الحدود نحصل على: `{sp.sstr(expanded)}`.\n\n'
                    f'### الإجابة النهائية\n`{sp.sstr(expanded)}`'
                )
            if 2 <= n <= 5:
                repeated = ' × '.join([f'({sp.sstr(base)})'] * n)
                return (
                    f'### فهم السؤال\nالمطلوب توسيع: `{sp.sstr(expr)}`.\n\n'
                    '### الحل خطوة بخطوة\n'
                    f'1. نحول القوة إلى ضرب متكرر: `{repeated}`.\n'
                    '2. نوزع الضرب على الحدود، ثم نجمع الحدود المتشابهة.\n'
                    f'3. بعد التوسيع والتجميع: `{sp.sstr(expanded)}`.\n\n'
                    '### التحقق\nتمت مقارنة التوسيع بالتعبير الأصلي جبريًا.\n\n'
                    f'### الإجابة النهائية\n`{sp.sstr(expanded)}`'
                )
        return (
            f'### فهم السؤال\nالمطلوب تبسيط التعبير: `{sp.sstr(expr)}`.\n\n'
            f'### الحل خطوة بخطوة\n1. نفك الأقواس ونوزع الضرب.\n2. نجمع الحدود المتشابهة.\n3. الناتج المبسط: `{sp.sstr(expanded)}`.\n\n'
            f'### الإجابة النهائية\n`{sp.sstr(expanded)}`'
        )
    except Exception:
        return None


def local_math(q: str):
    return linear_steps(q) or polynomial_steps(q) or arithmetic_steps(q)


def math_to_latex_text(text: str):
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application, convert_xor
        transformations = standard_transformations + (implicit_multiplication_application, convert_xor)
        def parse_piece(piece):
            piece = normalize_math(piece.strip())
            return parse_expr(piece, transformations=transformations, evaluate=True)
        def convert(match):
            raw = match.group(1).strip()
            try:
                if '=' in raw and raw.count('=') == 1:
                    left, right = raw.split('=', 1)
                    l = parse_piece(left); r = parse_piece(right)
                    return r'\(' + sp.latex(l) + ' = ' + sp.latex(r) + r'\)'
                expr = parse_piece(raw)
                return r'\(' + sp.latex(expr) + r'\)'
            except Exception:
                cleaned = re.sub(r'\*\*(\d+)', lambda m: '^{' + m.group(1) + '}', raw)
                cleaned = cleaned.replace('*', r'\cdot ')
                return r'\(' + cleaned + r'\)'
        return re.sub(r'`([^`]+)`', convert, text)
    except Exception:
        return text

def format_math_input(text: str):
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import (
            parse_expr, standard_transformations,
            implicit_multiplication_application, convert_xor,
        )
        transformations = standard_transformations + (implicit_multiplication_application, convert_xor)
        raw = normalize_math(text.strip())
        raw = re.sub(r'^\s*(حل|احسب|بسّط|بسط|أوجد|اوجد|جد|فك)\s*[:：-]?\s*', '', raw, flags=re.I)
        raw = raw.strip()
        def parse_piece(piece):
            return parse_expr(piece.strip(), transformations=transformations, evaluate=False)
        if '=' in raw and raw.count('=') == 1:
            left, right = raw.split('=', 1)
            l, r = parse_piece(left), parse_piece(right)
            return sp.latex(l) + ' = ' + sp.latex(r)
        expr = parse_piece(raw)
        return sp.latex(expr)
    except Exception:
        return ''


def extract_context(q, txt):
    if not txt.strip(): return ''
    words = [x for x in re.findall(r'[\w\u0600-\u06ff]+', q.lower()) if len(x) > 2]
    paras = [p.strip() for p in re.split(r'\n{2,}|(?<=[.!؟])\s+', txt) if len(p.strip()) > 25]
    ranked = sorted(((sum(w in p.lower() for w in words), p) for p in paras), key=lambda z: z[0], reverse=True)
    chosen = [p for n,p in ranked if n][:5] or paras[:3]
    return '\n\n'.join(chosen)[:8000]


def subject_methodology(subject: str):
    if subject == 'الرياضيات':
        return 'اكتب المعطيات ثم القانون أو القاعدة ثم كل تحول جبري في سطر مستقل، ثم تحقق من الناتج.'
    if subject in {'الفيزياء','الكيمياء'}:
        return 'اكتب المعطيات ووحداتها، ثم القانون، ثم التعويض، ثم الحساب خطوة بخطوة مع الوحدات، ثم تحقق من منطق الناتج.'
    if subject == 'الأحياء':
        return 'حدد المفهوم المطلوب، ثم اربط السبب بالنتيجة، ثم اكتب الإجابة العلمية المباشرة ومراجعتها.'
    if subject in {'العلوم'}:
        return 'اشرح المفهوم بحسب مستوى المرحلة، ثم طبّق على السؤال خطوة بخطوة، ولا تستخدم مصطلحات مقررات أعلى إل٧ للضرورة مع تبسيطها.'
    if subject in {'لغتي','لغتي الخالدة','اللغة العربية'}:
        return 'حدد القاعدة اللغوية أو الفكرة، استخرج الشاهد، طبق القاعدة خطوة بخطوة، ثم اكتب الإجابة النهائية.'
    if subject == 'اللغة الإنجليزية':
        return 'حدد المهارة المطلوبة، اشرح القاعدة باختصار، طبّقها على السؤال، ثم اكتب الإجابة مع تصحيح الأخطاء.'
    if subject in {'الدراسات الإسلامية','القرآن الكريم والدراسات الإسلامية'}:
        return 'أجب من محتوى المقرر، وميّز النص الشرعي عن الشرح، ولا تنسب نصًا أو حكمًا بلا سند من السياق المتاح.'
    return 'اشرح المفهوم من مستوى المقرر، ثم حل المطلوب في خطوات واضحة، ثم اكتب الإجابة النهائية ومراجعة قصيرة.'


def call_model(m, q, ctx, stage, grade, subject):
    h = {'Authorization': f'Bearer {API_KEY}', 'Content-Type':'application/json'}
    system = f'''أنت معلم سعودي خبير بالمناهج. المرحلة: {stage}. الصف: {grade}. المادة: {subject}.
ممنوع الخلط بين المراحل أو المواد. لا تجب عن سؤال خارج المادة/المرحلة المحددة.
{subject_methodology(subject)}
اكتب دائمًا بهذا البناء:
1) فهم السؤال
2) المعطيات/المفاهيم أو القاعدة
3) الحل خطوة بخطوة وبشكل مكتوب بالكامل
4) الإجابة النهائية
5) التحقق أو المراجعة
إذا كان السؤال حسابيًا فلا تقفز إلى النتيجة ولا تختصر العمليات. إذا كانت بيانات السؤال ناقصة فاذكر الناقص بدل اختراعه.
اكتب كل المعادلات والتعابير الرياضية بصيغة LaTeX بين \\( و \\). استخدم \\frac{البسط}{المقام} للكسور و x^{2} للأسس و \\sqrt{x} لل٬ذور. لا تعرض الكسور بالشرطة / ولا الأسس بالرمز ^ في النص المرئي.'''
    user = q + ('\n\nسياق من الملف/الكتاب:\n' + ctx if ctx else '')
    payload = {'model':m['model'],'messages':[{'role':'system','content':system},{'role':'user','content':user}], 'temperature':0.05,'max_tokens':1800}
    r = requests.post(m['url'].rstrip('/') + '/v1/chat/completions', headers=h, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()['choices'][0]['message']['content']



def call_public_fallback(q, ctx, stage, grade, subject):
    system = f'''أنت معلم خبير بالمناهج المدرسية، وتشرح بالعربية الواضحة.
المرحلة: {stage}
الصف: {grade}
المادة: {subject}

التزم بمستوى الصف والمادة المحددين فقط، ولا تخلط بين المراحل أو المناهج.
{subject_methodology(subject)}

اكتب الحل دائمًا بهذا الترتيب:
### فهم السؤال
### المعطيات / المفاهيم / القاعدة
### الحل خطوة بخطوة
### الإجابة النهائية
### التحقق أو المراجعة

لأي سؤال حسابي: اكتب جميع الخطوات، والقوانين، والتعويضات، والوحدات عند وجودها.
اكتب الرياضيات بصيغة LaTeX بين \( و \) أو بين \[ و \].
استخدم \frac{{البسط}}{{المقام}} للكسور، و x^{{2}} للأسس، و \sqrt{{x}} للجذور.
لا تستخدم / لعرض الكسور ولا ^ كتنسيق مرئي للمستخدم.
إذا كان السؤال غير متوافق فعلًا مع الصف أو المادة، وضّح ذلك بدل اختراع إجابة.
لا تذكر أي معلومات تقنية عن النموذج أو الاستضافة.'''

    user = q + (f"\n\nسياق من الملف أو الكتاب:\n{ctx}" if ctx else "")
    payload = {
        'model': PUBLIC_FALLBACK_MODEL,
        'messages': [
            {'role':'system','content':system},
            {'role':'user','content':user},
        ],
        'temperature':0.05,
        'max_tokens':1800,
    }
    r = requests.post(PUBLIC_FALLBACK_URL, json=payload, timeout=max(TIMEOUT, 60))
    r.raise_for_status()
    data = r.json()
    return data['choices'][0]['message']['content'].strip()


@app.get('/health')
def health():
    return {'ok':True,'models':[m.get('name') for m in MODELS], 'curriculum':'strict'}

@app.get('/', response_class=HTMLResponse)
def home(): return HTMLResponse(HTML, headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache'})

@app.post('/format-math')
async def format_math(text: str = Form('')):
    return {'ok': True, 'latex': format_math_input(text)}


@app.post('/solve')
async def solve(stage:str=Form(...), grade:str=Form(...), subject:str=Form(...), question:str=Form(''), files:list[UploadFile]=File(default=[])):
    texts=[]; names=[]
    for f in files[:5]:
        b=await f.read(); t=parse_file(f.filename or 'file', b)
        if t.strip(): texts.append(t[:30000]); names.append(f.filename)
    alltxt='\n'.join(texts)
    q=(question or alltxt[:5000]).strip()
    if not q: return {'ok':False,'error':'اكتب سؤالًا أو ارفع ملفًا.'}
    problems=curriculum_validate(stage, grade, subject, q+' '+alltxt[:2000])
    if problems:
        return {'ok':False,'curriculum_error':True,'error':'\n'.join('• '+x for x in problems), 'allowed_subjects':CURRICULUM.get(stage,[])}

    ctx=extract_context(q, alltxt)
    raw_local = local_math(q) if subject=='الرياضيات' else None
    local = math_to_latex_text(raw_local) if raw_local else None
    panel=[]

    async def one(m):
        try:
            text=await asyncio.to_thread(call_model,m,q,ctx,stage,grade,subject)
            return {'name':m.get('name','LLM'),'ok':True,'text':text}
        except Exception as e:
            return {'name':m.get('name','LLM'),'ok':False,'error':str(e)[:180]}

    if MODELS:
        panel=await asyncio.gather(*(one(m) for m in MODELS[:5]))

    good=[x for x in panel if x.get('ok')]

    if local:
        ans = local
    elif good:
        ans = good[0]['text']
    else:
        try:
            fallback_text = await asyncio.to_thread(
                call_public_fallback, q, ctx, stage, grade, subject
            )
            panel.append({'name':'GPT-OSS 20B','ok':True,'text':fallback_text})
            ans = fallback_text
        except Exception as e:
            panel.append({'name':'GPT-OSS 20B','ok':False,'error':str(e)[:180]})
            if ctx:
                ans=('### المعلومات ذات الصلة من الملف\n\n'+ctx+
                     '\n\n### تعذر إكمال الحل مؤقتًا\nتمت قراءة الملف بنجاح، لكن خدمة الاستدلال لم تستجب. أعد المحاولة بعد لحظات.')
            else:
                ans='تعذر الوصول إلى خدمة الاستدلال مؤقتًا. أعد المحاولة بعد لحظات.'

    return {'ok':True,'meta':{'stage':stage,'grade':grade,'subject':subject},'answer':ans,'models':panel,'files':names}


HTML='''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>المساعد الدراسي</title>
<script>
window.MathJax = {
  tex: {
    inlineMath: [['\\(','\\)']],
    displayMath: [['\\[','\\]']]
  },
  svg: {fontCache: 'global'}
};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
<style>
body{font-family:Tahoma,Arial;background:#f5f7fb;margin:0;color:#172033}.w{max-width:900px;margin:auto;padding:18px}.c{background:#fff;border:1px solid #e5e7eb;border-radius:20px;padding:18px;margin:14px 0;box-shadow:0 7px 25px #00000009}h1{text-align:center}textarea,input,select{width:100%;box-sizing:border-box;padding:12px;border:1px solid #d6dae0;border-radius:12px;margin:7px 0;font:inherit}textarea{min-height:145px}.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:9px}button{background:#0f766e;color:#fff;border:0;border-radius:13px;padding:13px 22px;font-weight:700;font-size:16px;cursor:pointer}.ans{white-space:pre-wrap;line-height:2.15;font-size:1.08rem}.ans mjx-container{direction:ltr;margin:.45em .1em!important;font-size:1.12em!important}.math-preview{direction:ltr;text-align:center;background:#f8fafc;border:1px solid #dbe4ee;border-radius:12px;padding:12px;margin:6px 0 10px;font-size:1.18rem;min-height:26px}.math-preview mjx-container{margin:0!important}.pill{display:inline-block;background:#ecfdf5;padding:5px 9px;border-radius:20px;margin:3px}.muted{color:#6b7280}.hide{display:none}.err{white-space:pre-wrap;color:#991b1b;background:#fef2f2;border:1px solid #fecaca;padding:12px;border-radius:12px}.note{background:#fffbeb;border:1px solid #fde68a;padding:10px;border-radius:12px;margin:10px 0}@media(max-width:650px){.grid{grid-template-columns:1fr}}
</style></head><body><div class="w"><h1>المساعد الدراسي</h1><p class="muted" style="text-align:center">اختر المرحلة والصف والمادة أولًا لمنع خلط المناهج، ثم اكتب السؤال أو ارفع الواجب.</p><div class="c"><form id="f"><div class="grid"><select id="stage" name="stage" required></select><select id="grade" name="grade" required></select><select id="subject" name="subject" required></select></div><textarea name="question" placeholder="اكتب السؤال كاملًا هنا"></textarea><div id="mathPreview" class="math-preview hide"></div><input type="file" name="files" multiple accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.webp"><button id="b">حل الواجب بالخطوات</button> <span id="s"></span></form></div><div id="r" class="c hide"><div id="err" class="err hide"></div><div id="meta"></div><h2>الحل</h2><div id="ans" class="ans"></div><details><summary>تفاصيل النماذج</summary><div id="models"></div></details></div></div><script>
const curricula={"الابتدائي":['القرآن الكريم والدراسات الإسلامية','لغتي','الرياضيات','العلوم','اللغة الإنجليزية','الدراسات الاجتماعية','المهارات الرقمية','المهارات الحياتية والأسرية','التربية الفنية','التربية البدنية'],"المتوسط":['القرآن الكريم والدراسات الإسلامية','لغتي الخالدة','الرياضيات','العلوم','اللغة الإنجليزية','الدراسات الاجتماعية','المهارات الرقمية','المهارات الحياتية والأسرية','التربية الفنية','التربية البدنية'],"الثانوي":['الدراسات الإسلامية','اللغة العربية','اللغة الإنجليزية','الرياضيات','الأحياء','الكيمياء','الفيزياء','التقنية الرقمية','الدراسات الاجتماعية','التفكير الناقد','التربية الصحية والبدنية','إدارة الأعمال']};
const grades={"الابتدائي":['الأول الابتدائي','الثاني الابتدائي','الثالث الابتدائي','الرابع الابتدائي','الخامس الابتدائي','السادس الابتدائي'],"المتوسط":['الأول المتوسط','الثاني المتوسط','الثالث المتوسط'],"الثانوي":['الأول الثانوي','الثاني الثانوي','الثالث الثانوي']};
let st=document.querySelector('#stage'),gr=document.querySelector('#grade'),su=document.querySelector('#subject'); st.innerHTML='<option value="">اختر المرحلة</option>'+Object.keys(grades).map(x=>`<option>${x}</option>`).join(''); function refresh(){let x=st.value;gr.innerHTML='<option value="">اختر الصف</option>'+((grades[x]||[]).map(v=>`<option>${v}</option>`).join(''));su.innerHTML='<option value="">اختر المادة</option>'+((curricula[x]||[]).map(v=>`<option>${v}</option>`).join(''))} st.onchange=refresh;refresh();
function escHtml(t){return (t||'').replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function renderAnswer(t){
  var h=escHtml(t);
  h=h.replace(/^### (.+)$/gm,'<h3>$1</h3>');
  h=h.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  h=h.replace(/\n/g,'<br>');
  return h;
}
const qbox=document.querySelector('textarea[name="question"]');
const preview=document.querySelector('#mathPreview');
let previewTimer=null;
qbox.addEventListener('input',function(){
  clearTimeout(previewTimer);
  const val=qbox.value.trim();
  if(!val){preview.classList.add('hide');preview.textContent='';return;}
  previewTimer=setTimeout(async function(){
    try{
      const fd=new FormData(); fd.append('text',val);
      const res=await fetch('/format-math',{method:'POST',body:fd});
      const data=await res.json();
      if(data.latex){
        preview.textContent='\\['+data.latex+'\\]';
        preview.classList.remove('hide');
        if(window.MathJax&&MathJax.typesetPromise){
          MathJax.typesetClear([preview]);
          await MathJax.typesetPromise([preview]);
        }
      }else{preview.classList.add('hide');preview.textContent='';}
    }catch(e){preview.classList.add('hide');}
  },280);
});
let f=document.querySelector('#f'),b=document.querySelector('#b'),s=document.querySelector('#s'),r=document.querySelector('#r'),err=document.querySelector('#err');f.onsubmit=async e=>{e.preventDefault();b.disabled=true;s.textContent='جاري الحل خطوة بخطوة…';r.classList.add('hide');err.classList.add('hide');try{let z=await fetch('/solve',{method:'POST',body:new FormData(f)}),x=await z.json();r.classList.remove('hide');if(!x.ok){err.textContent=x.error||'تعذر الحل';err.classList.remove('hide');document.querySelector('#ans').textContent='';document.querySelector('#meta').innerHTML='';return}let m=x.meta||{};document.querySelector('#meta').innerHTML=[m.stage,m.grade,m.subject].map(v=>`<span class="pill">${v||''}</span>`).join('');let ae=document.querySelector('#ans'); ae.innerHTML=renderAnswer(x.answer||''); if(window.MathJax&&MathJax.typesetPromise){MathJax.typesetClear([ae]);MathJax.typesetPromise([ae]);}document.querySelector('#models').innerHTML=(x.models||[]).map(v=>`<p><b>${v.name} ${v.ok?'✓':'⚠'}</b><br>${(v.text||v.error||'').slice(0,1000)}</p>`).join('')||'<p>تم استخدام محرك الحل المتاح.</p>';s.textContent='تم'}catch(e){r.classList.remove('hide');err.textContent=e.message;err.classList.remove('hide');s.textContent=''}finally{b.disabled=false}};
</script></body></html>'''
