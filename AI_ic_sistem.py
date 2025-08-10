import os
import io
import json
from typing import List, Dict, Any
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

# Ortam değişkenlerini yükle
load_dotenv()

# ------------------------------
# UI Ayarları
# ------------------------------
st.set_page_config(page_title="Hiperaktivist – 20 Soru Tasarımcısı", page_icon="❓", layout="wide")
st.title("Hiperaktivist • İç Sistem: 20 Soru Tasarımcısı")
st.caption("Eğitim içeriği + Teknik & Yöntemler dosyalarına sadık kalarak, analiz için en uygun 20 soruyu üretir.")

# ------------------------------
# Dosya Okuma Fonksiyonu
# ------------------------------
try:
    from docx import Document
except:
    Document = None

try:
    import PyPDF2
except:
    PyPDF2 = None

def read_file(file) -> str:
    name = file.name.lower()
    if name.endswith((".txt", ".md")):
        return file.read().decode("utf-8", errors="ignore")
    if name.endswith(".docx") and Document:
        buf = io.BytesIO(file.read())
        doc = Document(buf)
        return "\n".join([p.text for p in doc.paragraphs])
    if name.endswith(".pdf") and PyPDF2:
        buf = io.BytesIO(file.read())
        reader = PyPDF2.PdfReader(buf)
        return "\n".join([p.extract_text() or "" for p in reader.pages])
    return ""

def chunk_text(text: str, max_chars: int = 6000) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    chunks, cur, cur_len = [], [], 0
    for line in text.splitlines():
        if cur_len + len(line) + 1 > max_chars:
            chunks.append("\n".join(cur))
            cur, cur_len = [line], len(line)
        else:
            cur.append(line)
            cur_len += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks

# ------------------------------
# GA Teknikleri ve JSON Şema
# ------------------------------
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

# ------------------------------
# Prompt Şablonları
# ------------------------------
SYSTEM_TEMPLATE = """
Sen Hiperaktivist'in iç sistemi için çalışan bir "Soru Tasarımcısı" yapay zekâsın.
Görevin: Yüklenen Eğitim Metni + Teknik & Yöntemler çerçevesine sadık kalarak, kullanıcıdan anlamlı ve analiz edilebilir yanıtlar alacak N adet soru üretmek.
Kurallar:
- GA Teknikleri eğitim sahibinin üslubuna saygılı biçimde harmanlanmalı.
- Sorular yönlendirici olmamalı; açık uçlu olmalı.
- Çıktıyı SADECE geçerli JSON olarak ver (şema aşağıda). Ek açıklama verme.
- Dil: {language}
- Soru sayısı: {num_questions}
""".strip()

USER_TEMPLATE = """
# EĞİTİM ÖZETİ
{education_summary}

# TEKNİK & YÖNTEMLER ÖZETİ
{techniques_summary}

# TEKNİK AĞIRLIKLARI
{technique_weights}

# JSON ŞEMA
{json_schema}
""".strip()

# ------------------------------
# OpenAI Client
# ------------------------------
openai_key = st.sidebar.text_input("OpenAI API Key", type="password", value=os.environ.get("OPENAI_API_KEY", ""))
model = st.sidebar.text_input("Model", value="gpt-5-mini")
num_questions = st.sidebar.number_input("Soru sayısı", min_value=5, max_value=40, value=20)
temperature = st.sidebar.slider("Temperature", 0.0, 1.0, 0.4)
language = st.sidebar.selectbox("Dil", ["Türkçe", "English"], index=0)

tech_weights = {}
cols = st.columns(2)
for i, (label, key) in enumerate(GA_TECHNIQUES):
    with cols[i % 2]:
        tech_weights[key] = st.slider(label, 0, 100, 50, 5)

client = OpenAI(api_key=openai_key) if openai_key else None

# ------------------------------
# Dosya Yükleme
# ------------------------------
col1, col2 = st.columns(2)
with col1:
    edu_file = st.file_uploader("Eğitim Dosyası", type=["docx", "pdf", "txt", "md"])
with col2:
    ty_file = st.file_uploader("Teknik & Yöntemler", type=["docx", "pdf", "txt", "md"])

edu_text = read_file(edu_file) if edu_file else ""
ty_text = read_file(ty_file) if ty_file else ""

# ------------------------------
# Özetleme Fonksiyonu
# ------------------------------
def summarize_text(text: str, label: str) -> str:
    if not client: 
        return ""
    
    chunks = chunk_text(text, max_chars=4000)  # Küçük parçalar
    summaries = []

    for i, chunk in enumerate(chunks):
        prompt = f"Metni 5 maddede kısa ve net özetle. Bölüm {i+1}/{len(chunks)}. Başlık: {label}.\n\nMetin:\n{chunk}"
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Sen kısa ve net özetleyen bir asistansın."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        summaries.append(resp.choices[0].message.content.strip())

    # Son özetleme
    final_prompt = f"Tüm özetleri birleştir ve 10 maddede nihai özet oluştur. Başlık: {label}.\n\nÖzetler:\n" + "\n".join(summaries)
    final_resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Sen kısa ve net özetleyen bir asistansın."},
            {"role": "user", "content": final_prompt},
        ],
        temperature=0.2,
    )
    return final_resp.choices[0].message.content.strip()


# ------------------------------
# Soru Üretme Fonksiyonu
# ------------------------------
def generate_questions(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    if not client: return {}
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
    try:
        return resp.output_parsed
    except:
        return {"raw": resp.output_text}

# ------------------------------
# Buton
# ------------------------------
if st.button("⚙️ 20 Soruyu Üret", disabled=not (client and edu_text and ty_text)):
    with st.spinner("Özetleniyor ve sorular üretiliyor…"):
        edu_summary = summarize_text(edu_text, "Eğitim Özeti")
        ty_summary = summarize_text(ty_text, "Teknik & Yöntemler Özeti")
        system_prompt = SYSTEM_TEMPLATE.format(language=language, num_questions=num_questions)
        user_prompt = USER_TEMPLATE.format(
            education_summary=edu_summary,
            techniques_summary=ty_summary,
            technique_weights=json.dumps(tech_weights, ensure_ascii=False),
            json_schema=json.dumps(QUESTION_SCHEMA, ensure_ascii=False),
        )
        data = generate_questions(system_prompt, user_prompt)
        st.session_state.data = data

# ------------------------------
# Sonuç Göster
# ------------------------------
if st.session_state.get("data"):
    st.subheader("📋 Üretilen Sorular")
    st.code(json.dumps(st.session_state.data, ensure_ascii=False, indent=2), language="json")
