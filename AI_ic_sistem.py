import os
import io
import json
from typing import List, Dict, Any
import openai
st.sidebar.write("OpenAI version:", openai.__version__)



import streamlit as st
from dotenv import load_dotenv
load_dotenv()


# Optional deps (add to requirements.txt when deploying on Streamlit Cloud):
# streamlit
# python-docx
# PyPDF2
# openai>=1.30.0
# jsonschema

try:
    from docx import Document  # python-docx
except Exception:
    Document = None

try:
    import PyPDF2
except Exception:
    PyPDF2 = None

try:
    from jsonschema import validate as jsonschema_validate, ValidationError
except Exception:
    ValidationError = Exception
    def jsonschema_validate(instance, schema):
        return True

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# ------------------------------
# UI CONFIG
# ------------------------------
st.set_page_config(page_title="Hiperaktivist – 20 Soru Tasarımcısı", page_icon="❓", layout="wide")

st.title("Hiperaktivist • İç Sistem: 20 Soru Tasarımcısı")
st.caption("Eğitim içeriği + Teknik & Yöntemler dosyalarına sadık kalarak, analiz için en uygun 20 soruyu üretir.")

# ------------------------------
# HELPERS
# ------------------------------

def read_file(file) -> str:
    """Return plain text from supported file types."""
    name = file.name.lower()
    if name.endswith(".txt") or name.endswith(".md"):
        return file.read().decode("utf-8", errors="ignore")
    if name.endswith(".docx"):
        if not Document:
            return "(python-docx yok – requirements'e ekleyin)"
        buf = io.BytesIO(file.read())
        doc = Document(buf)
        return "\n".join([p.text for p in doc.paragraphs])
    if name.endswith(".pdf"):
        if not PyPDF2:
            return "(PyPDF2 yok – requirements'e ekleyin)"
        buf = io.BytesIO(file.read())
        reader = PyPDF2.PdfReader(buf)
        pages = []
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                pages.append("")
        return "\n".join(pages)
    # Fallback: try decode
    try:
        return file.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def chunk_text(text: str, max_chars: int = 6000) -> List[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks = []
    cur = []
    cur_len = 0
    for line in text.splitlines():
        if cur_len + len(line) + 1 > max_chars:
            chunks.append("\n".join(cur))
            cur = [line]
            cur_len = len(line)
        else:
            cur.append(line)
            cur_len += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


GA_TECHNIQUES = [
    ("Duyusal Entegrasyon", "duyusal_enteg"),
    ("Paralel Anlatım / Hikâye", "hikaye"),
    ("Ters Paradoksal", "paradoksal"),
    ("Sokratik Yöntem", "sokratik"),
    ("Kontrastlı Anlatım", "kontrast"),
    ("GA Dili (samimi/yargısız)", "ga_dili"),
    ("Somutlaştırma & Küçük Adımlar", "somut"),
    ("Şok Uyandırıcı Giriş", "sok_giris"),
    ("Eylem Odaklı Kapanış", "eylem_kapanis"),
]

QUESTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "meta": {
            "type": "object",
            "properties": {
                "education_title": {"type": "string"},
                "num_questions": {"type": "integer"},
                "language": {"type": "string"},
                "technique_weights": {"type": "object"},
            },
            "required": ["education_title", "num_questions", "language"],
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "category": {"type": "string"},
                    "target_signal": {"type": "string"},
                    "why_this": {"type": "string"},
                    "technique_tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "question", "category", "target_signal", "why_this", "technique_tags"],
            },
        },
    },
    "required": ["meta", "questions"],
}


SYSTEM_TEMPLATE = """
Sen Hiperaktivist'in iç sistemi için çalışan bir "Soru Tasarımcısı" yapay zekâsın.
Görevin: Yüklenen Eğitim Metni + Teknik & Yöntemler çerçevesine sadık kalarak, kullanıcıdan anlamlı ve analiz edilebilir yanıtlar alacak N adet soru üretmek.
Kurallar:
- GA Teknikleri (Duyusal Entegrasyon, Paralel Anlatım, Ters Paradoksal, Sokratik, Kontrast, GA dili, Somut adımlar, Şok giriş, Eylem kapanışı) eğitim sahibinin üslubuna saygılı biçimde harmanlanmalı.
- Sorular yönlendirici olmamalı; açık uçlu, deneyim ve içgörü çıkarıcı olmalı.
- Çıktıyı SADECE geçerli JSON olarak ver (şema aşağıda). Ek açıklama, ön/arka metin verme.
- Her soruya: kategori, hedeflenen sinyal (target_signal), neden bu soru (why_this) ve teknik etiketleri ekle.
- Dil ve ton: {language} – samimi, yargısız, profesyonel.
- Soru sayısı: {num_questions}
""".strip()


USER_TEMPLATE = """
# EĞİTİM ÖZETİ
{education_summary}

# TEKNİK & YÖNTEMLER ÖZETİ
{techniques_summary}

# TEKNİK AĞIRLIKLARI (0-100)
{technique_weights}

# JSON ŞEMA
{json_schema}
""".strip()


def summarize_text(client, model: str, text: str, label: str) -> str:
    prompt = f"Metni 10-12 maddeyle kısa, öz ve bilgi kaybı olmadan özetle. Başlık: {label}.\n\nMetin:\n{text[:12000]}"
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Kısa ve bilgi kaybı olmadan özetleyen bir yardımcı yazarsın."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"(Özetlenemedi: {e})"


def generate_questions(client, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.4) -> Dict[str, Any]:
    resp = client.responses.create(
        model=model,
        temperature=temperature,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "questions_schema",
                "schema": QUESTION_SCHEMA,
                "strict": True
            }
        }
    )
    # json_schema modunda model çıktısı zaten Python dict olarak gelir
    try:
        return resp.output_parsed
    except Exception:
        return {"raw": resp.output_text}


def technique_weight_sidebar() -> Dict[str, int]:
    st.subheader("GA Teknik Ağırlıkları")
    weights = {}
    cols = st.columns(2)
    for i, (label, key) in enumerate(GA_TECHNIQUES):
        with cols[i % 2]:
            weights[key] = st.slider(label, 0, 100, 50, 5)
    return weights


def questions_table(data: Dict[str, Any]):
    if not data or "questions" not in data:
        st.info("Henüz soru üretilmedi.")
        return
    rows = data.get("questions", [])
    st.write(f"Toplam Soru: **{len(rows)}**")
    st.dataframe([{k: r.get(k) for k in ["id", "question", "category", "target_signal", "why_this", "technique_tags"]} for r in rows], use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button("JSON indir", data=json.dumps(data, ensure_ascii=False, indent=2), file_name="sorular.json", mime="application/json")
    with c2:
        # CSV export (flattened)
        import csv
        from io import StringIO
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "question", "category", "target_signal", "why_this", "technique_tags"])
        for r in rows:
            writer.writerow([
                r.get("id", ""),
                r.get("question", ""),
                r.get("category", ""),
                r.get("target_signal", ""),
                r.get("why_this", ""),
                "; ".join(r.get("technique_tags", [])),
            ])
        st.download_button("CSV indir", data=output.getvalue(), file_name="sorular.csv", mime="text/csv")


# ------------------------------
# SIDEBAR
# ------------------------------
st.sidebar.header("Ayarlar")
openai_key = st.sidebar.text_input("OpenAI API Key", type="password", value=os.environ.get("OPENAI_API_KEY", ""))
model = st.sidebar.text_input("Model", value="gpt-4o-mini")
num_questions = st.sidebar.number_input("Soru sayısı", min_value=5, max_value=40, value=20, step=1)
temperature = st.sidebar.slider("Temperature", 0.0, 1.0, 0.4, 0.05)
language = st.sidebar.selectbox("Dil", ["Türkçe", "English"], index=0)

tech_weights = technique_weight_sidebar()

# ------------------------------
# FILE INPUT
# ------------------------------
col1, col2 = st.columns(2)
with col1:
    edu_file = st.file_uploader("Eğitim Dosyası (docx/pdf/txt/md)", type=["docx", "pdf", "txt", "md"], key="edu")
with col2:
    ty_file = st.file_uploader("Teknik & Yöntemler (docx/pdf/txt/md)", type=["docx", "pdf", "txt", "md"], key="ty")

if 'state' not in st.session_state:
    st.session_state.state = {}

# ------------------------------
# PROCESS INPUTS
# ------------------------------
edu_text = ""
ty_text = ""
if edu_file:
    with st.expander("Eğitim Metni (önizleme)", expanded=False):
        edu_text = read_file(edu_file)
        st.text_area("Eğitim Metni (önizleme)", value=edu_text[:6000], height=200, label_visibility="collapsed")
if ty_file:
    with st.expander("Teknik & Yöntemler (önizleme)", expanded=False):
        ty_text = read_file(ty_file)
        st.text_area("Teknik & Yöntemler (önizleme)", value=ty_text[:6000], height=200, label_visibility="collapsed")

# ------------------------------
# LLM CLIENT
# ------------------------------
client = None
if openai_key and OpenAI:
    try:
        client = OpenAI(api_key=openai_key)
    except Exception as e:
        st.sidebar.error(f"OpenAI istemcisi başlatılamadı: {e}")

# ------------------------------
# SUMMARIZE + GENERATE
# ------------------------------
c_gen, c_val = st.columns([2,1])
with c_gen:
    disabled = not (client and edu_text and ty_text)
    if st.button("⚙️ 20 Soruyu Üret", type="primary", use_container_width=True, disabled=disabled):
        with st.spinner("Özetleniyor ve sorular üretiliyor…"):
            # Summaries (to keep prompt concise)
            edu_summary = summarize_text(client, model, "\n".join(chunk_text(edu_text, 6000)), "Eğitim Özeti")
            ty_summary = summarize_text(client, model, "\n".join(chunk_text(ty_text, 6000)), "Teknik & Yöntemler Özeti")

            system_prompt = SYSTEM_TEMPLATE.format(language=language, num_questions=num_questions)
            user_prompt = USER_TEMPLATE.format(
                education_summary=edu_summary,
                techniques_summary=ty_summary,
                technique_weights=json.dumps(tech_weights, ensure_ascii=False),
                json_schema=json.dumps(QUESTION_SCHEMA, ensure_ascii=False),
            )

            data = generate_questions(client, model, system_prompt, user_prompt, temperature)

            # Validate
            valid = True
            try:
                jsonschema_validate(data, QUESTION_SCHEMA)
            except Exception as e:
                valid = False
                st.error(f"JSON şema doğrulaması başarısız: {e}")

            st.session_state.state = {
                "data": data,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "edu_summary": edu_summary,
                "ty_summary": ty_summary,
                "valid": valid,
            }

with c_val:
    if st.session_state.state.get("system_prompt"):
        with st.expander("Kullanılan Sistem Promptu"):
            st.code(st.session_state.state["system_prompt"], language="markdown")
        with st.expander("Kullanılan Kullanıcı Promptu"):
            st.code(st.session_state.state["user_prompt"], language="markdown")

# ------------------------------
# SHOW RESULTS
# ------------------------------
state = st.session_state.state
if state.get("data"):
    st.markdown("---")
    st.subheader("📋 Üretilen Sorular")
    if not state.get("valid"):
        st.warning("Şema doğrulaması uyarıları var. Yine de veriyi görebilirsiniz.")
    questions_table(state["data"])

    with st.expander("Ham JSON"):
        st.code(json.dumps(state["data"], ensure_ascii=False, indent=2), language="json")

# ------------------------------
# FOOTER
# ------------------------------
st.markdown(
    """
---
**Notlar**  
• Bu arayüz yalnızca **iç kullanım** içindir.  
• Model ve sıcaklık (temperature) analiz kalitesini etkiler.  
• Üretilen sorular, GA teknik ve yöntemlerine (yüklediğiniz dosyadaki çerçeveye) göre optimize edilir.  
• Şema doğrulaması başarısız olursa, model yanıtını yineleyin veya temperature'u düşürün.

**Dağıtım İpuçları**  
1) Streamlit Cloud için `requirements.txt` oluşturun (yukarıdaki kütüphaneler).  
2) Ortama `OPENAI_API_KEY` gizli değişkenini ekleyin.  
3) Bu dosyayı `app.py` olarak deploy edin.
"""
)
