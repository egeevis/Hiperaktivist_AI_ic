import os
import io
import json
from typing import List, Dict, Any

import streamlit as st
from dotenv import load_dotenv
load_dotenv()

try:
    from docx import Document
except ImportError:
    Document = None

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    from jsonschema import validate as jsonschema_validate
except ImportError:
    def jsonschema_validate(instance, schema):
        return True

from openai import OpenAI

# ---------------- CONFIG ----------------
st.set_page_config(page_title="Hiperaktivist – 20 Soru Tasarımcısı", page_icon="❓", layout="wide")
st.title("Hiperaktivist • İç Sistem: 20 Soru Tasarımcısı")
st.caption("Eğitim + Teknik & Yöntem dosyalarına sadık kalarak, analiz için 20 soru üretir.")

# ---------------- HELPERS ----------------
def read_file(file) -> str:
    """Return plain text from supported file types."""
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
    try:
        return file.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""

def chunk_text(text: str, max_chars: int = 6000) -> List[str]:
    text = text.strip()
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
- GA Teknikleri eğitim sahibinin üslubuna saygılı biçimde harmanlanmalı.
- Sorular açık uçlu, deneyim ve içgörü çıkarıcı olmalı.
- Çıktıyı SADECE geçerli JSON olarak ver.
- Dil ve ton: {language}
- Soru sayısı: {num_questions}
""".strip()

USER_TEMPLATE = """
# EĞİTİM ÖZETİ
{education_summary}

# TEKNİK &aaaaa YÖNTEMLER ÖZETİ
{techniques_summary}

# TEKNİK AĞIRLIKLARI
{technique_weights}

# JSON ŞEMA
{json_schema}
""".strip()

def summarize_text(client, model: str, text: str, label: str) -> str:
    """Büyük metinleri parça parça özetleyip final özet döndürür."""
    chunks = chunk_text(text, max_chars=6000)
    partial_summaries = []

    # 1️⃣ Chunk bazlı özetleme
    for idx, chunk in enumerate(chunks, start=1):
        prompt = f"Bu metin bölümünü 5-6 maddeyle kısa ve öz şekilde özetle.\n\nBölüm {idx}/{len(chunks)}:\n{chunk}"
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Kısa, net ve bilgi kaybı olmadan özetleyen bir yardımcı yazarsın."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
            partial_summaries.append(resp.choices[0].message.content.strip())
        except Exception as e:
            partial_summaries.append(f"(Özetlenemedi: {e})")

    # 2️⃣ Tüm özetleri final özetle birleştirme
    combined_text = "\n".join(partial_summaries)
    final_prompt = f"Aşağıdaki parça özetleri kullanarak '{label}' başlıklı 10-12 maddelik nihai bir özet hazırla:\n\n{combined_text}"
    
    try:
        final_resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Metinleri birleştirip kısa, net ve bilgi kaybı olmadan özetleyen bir yardımcı yazarsın."},
                {"role": "user", "content": final_prompt},
            ],
            temperature=0.2,
        )
        return final_resp.choices[0].message.content.strip()
    except Exception as e:
        return f"(Final özetlenemedi: {e})"


def generate_questions(client, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.4) -> Dict[str, Any]:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content
    try:
        return json.loads(content)
    except Exception:
        return {"raw": content}

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
    st.download_button("JSON indir", data=json.dumps(data, ensure_ascii=False, indent=2), file_name="sorular.json", mime="application/json")

# ---------------- SIDEBAR ----------------
st.sidebar.header("Ayarlar")
openai_key = st.sidebar.text_input("OpenAI API Key", type="password", value=os.environ.get("OPENAI_API_KEY", ""))
model = st.sidebar.text_input("Model", value="gpt-4o-mini")
num_questions = st.sidebar.number_input("Soru sayısı", min_value=5, max_value=40, value=20, step=1)
temperature = st.sidebar.slider("Temperature", 0.0, 1.0, 0.4, 0.05)
language = st.sidebar.selectbox("Dil", ["Türkçe", "English"], index=0)
tech_weights = technique_weight_sidebar()

# ---------------- FILE INPUT ----------------
col1, col2 = st.columns(2)
with col1:
    edu_file = st.file_uploader("Eğitim Dosyası", type=["docx", "pdf", "txt", "md"], key="edu")
with col2:
    ty_file = st.file_uploader("Teknik & Yöntemler", type=["docx", "pdf", "txt", "md"], key="ty")

edu_text, ty_text = "", ""
if edu_file:
    edu_text = read_file(edu_file)
if ty_file:
    ty_text = read_file(ty_file)

# ---------------- PROCESS ----------------
client = OpenAI(api_key=openai_key) if openai_key else None

if st.button("⚙️ Soruları Üret", type="primary", use_container_width=True, disabled=not (client and edu_text and ty_text)):
    with st.spinner("Özetleniyor ve sorular üretiliyor…"):
        edu_summary = summarize_text(client, model, edu_text, "Eğitim Özeti")
        ty_summary = summarize_text(client, model, ty_text, "Teknik & Yöntemler Özeti")

        system_prompt = SYSTEM_TEMPLATE.format(language=language, num_questions=num_questions)
        user_prompt = USER_TEMPLATE.format(
            education_summary=edu_summary,
            techniques_summary=ty_summary,
            technique_weights=json.dumps(tech_weights, ensure_ascii=False),
            json_schema=json.dumps(QUESTION_SCHEMA, ensure_ascii=False),
        )

        data = generate_questions(client, model, system_prompt, user_prompt, temperature)
        try:
            jsonschema_validate(data, QUESTION_SCHEMA)
            valid = True
        except Exception as e:
            valid = False
            st.error(f"JSON şema doğrulaması başarısız: {e}")

        st.session_state["data"] = data
        st.session_state["valid"] = valid

if "data" in st.session_state:
    st.markdown("---")
    st.subheader("📋 Üretilen Sorular")
    if not st.session_state.get("valid", True):
        st.warning("Şema doğrulaması uyarıları var.")
    questions_table(st.session_state["data"])
