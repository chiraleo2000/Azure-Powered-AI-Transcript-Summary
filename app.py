"""
AI Summary Meeting Application - Desktop Web UI
Full-width web application interface
"""
import gradio as gr
import os
import base64
import time

# App version - change this on every deployment to bust browser caches
APP_VERSION = "0.1.34"

# Summary format constants (avoid duplicate literals - S1192)
SUMMARY_FMT_MEETING = "รายงานการประชุมภายใน"
SUMMARY_FMT_EXECUTIVE = "บทสรุปสำหรับผู้บริหาร"
SUMMARY_DEFAULT_INSTRUCTIONS = (
    "สรุปครบถ้วน กระชับ เป็นทางการ ครอบคลุมทุกประเด็น"
    " พร้อม Action Items ผู้รับผิดชอบ และกำหนดเวลา"
)

# Import from organized modules
from src.ui.styles import ENHANCED_CSS, SESSION_PERSISTENCE_JS
from app_func import (
    auto_refresh_ai_summary,
    auto_refresh_status,
    check_ai_summary_status,
    check_current_job_status,
    check_session_validity,
    create_summary_zip_archive,
    create_transcript_zip_archive,
    delete_user_account,
    export_user_data,
    login_user_with_session,
    logout_user_with_session,
    on_user_login,
    refresh_ai_summary_history,
    refresh_transcription_history,
    register_user,
    request_password_reset_ui,
    reset_password_with_token_ui,
    restore_session_on_load,
    submit_ai_summary_new,
    submit_transcription,
    update_marketing_consent,
    view_cloud_storage_stats,
)
from backend import ALLOWED_LANGS, AUDIO_FORMATS, transcription_manager, allowed_file, User
from session_manager import session_manager
from error_logger import error_logger, get_error_display


def get_embedded_logo():
    """Return embedded base64 logo HTML"""
    logo_path = os.path.join("static", "logo_betime-white.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return f'<img src="data:image/png;base64,{data}" alt="Logo" style="max-height: 80px;">'
    os.makedirs("static", exist_ok=True)
    return ""


def create_simplified_interface():
    """Create the main Gradio interface - Desktop Web App"""
    
    logo_html = get_embedded_logo()
    
    with gr.Blocks(
        theme=gr.themes.Soft(
            primary_hue="sky",
            secondary_hue="slate",
            neutral_hue="slate",
            font=["Sarabun", "system-ui", "sans-serif"]
        ),
        css=ENHANCED_CSS,
        title="🎙️ AI Meeting Summary",
        head=f"""
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<script>
(function(){{
    var APP_VER = "{APP_VERSION}";
    var STORED_VER = sessionStorage.getItem('app_version');
    if (STORED_VER && STORED_VER !== APP_VER) {{
        console.log('App version changed: ' + STORED_VER + ' -> ' + APP_VER + ', reloading...');
        sessionStorage.setItem('app_version', APP_VER);
        location.reload(true);
    }} else {{
        sessionStorage.setItem('app_version', APP_VER);
    }}
}})();
</script>
""" + SESSION_PERSISTENCE_JS,
        fill_width=True
    ) as demo:
        
        # State
        current_user = gr.State(None)
        session_id_state = gr.State("")
        job_state = gr.State({})
        summary_job_state = gr.State({})
        
        # Header - Vertical stacked layout (logo on top)
        gr.HTML(f"""
        <div class="logo-container">
            {logo_html}
            <h1>🎙️ AI Meeting Summary</h1>
            <p>แปลงเสียงเป็นข้อความ และสรุปด้วย AI อัจฉริยะ</p>
        </div>
        """)
        
        # Hidden session input
        session_id_input = gr.Textbox(visible=False, elem_id="session_id_hidden")
        
        # Top bar with user stats and error log
        with gr.Row():
            with gr.Column(scale=2):
                # User stats
                user_stats_display = gr.Textbox(
                    label="", lines=1, interactive=False, show_label=False,
                    placeholder="👤 กรุณาเข้าสู่ระบบ...",
                    elem_classes=["user-stats"]
                )
            
            # Error log removed — accessible via server logs only
        
        # ==================== AUTH SECTION ====================
        with gr.Column(visible=True, elem_classes=["auth-section"]) as auth_section:
            gr.Markdown("## 🔐 เข้าสู่ระบบ")
            
            with gr.Tabs() as auth_tabs:
                # Login
                with gr.Tab("🔓 เข้าสู่ระบบ"):
                    login_email = gr.Textbox(label="อีเมลหรือชื่อผู้ใช้", placeholder="กรอกอีเมลหรือชื่อผู้ใช้")
                    login_password = gr.Textbox(label="รหัสผ่าน", type="password", placeholder="กรอกรหัสผ่าน")
                    login_btn = gr.Button("🔓 เข้าสู่ระบบ", variant="primary", size="lg")
                    login_status = gr.Textbox(label="", show_label=False, interactive=False)
                    forgot_password_btn = gr.Button("🔑 ลืมรหัสผ่าน?", size="sm", variant="secondary")
                
                # Register
                with gr.Tab("📝 สมัครสมาชิก"):
                    reg_email = gr.Textbox(label="อีเมล", placeholder="กรอกอีเมล")
                    reg_username = gr.Textbox(label="ชื่อผู้ใช้", placeholder="เลือกชื่อผู้ใช้")
                    reg_password = gr.Textbox(label="รหัสผ่าน", type="password", placeholder="อย่างน้อย 8 ตัวอักษร")
                    reg_confirm_password = gr.Textbox(label="ยืนยันรหัสผ่าน", type="password")
                    
                    gr.Markdown("### 📋 ความยินยอม")
                    gdpr_consent = gr.Checkbox(label="ยินยอมประมวลผลข้อมูล (จำเป็น)", value=False)
                    data_retention_consent = gr.Checkbox(label="ยอมรับการเก็บข้อมูล (จำเป็น)", value=False)
                    marketing_consent = gr.Checkbox(label="รับข้อมูลการตลาด (ไม่บังคับ)", value=False)
                    
                    register_btn = gr.Button("📝 สร้างบัญชี", variant="primary", size="lg")
                    register_status = gr.Textbox(label="", show_label=False, interactive=False)
                    login_after_register = gr.Button("🔓 ไปยังหน้าเข้าสู่ระบบ", visible=False, variant="secondary")
        
        # Password Reset (hidden by default)
        with gr.Column(visible=False) as password_reset_section:
            gr.Markdown("## 🔑 รีเซ็ตรหัสผ่าน")
            reset_email_input = gr.Textbox(label="อีเมลหรือชื่อผู้ใช้", placeholder="กรอกอีเมลที่ลงทะเบียน")
            request_reset_btn = gr.Button("🔍 ค้นหาบัญชี", variant="primary")
            reset_status_message = gr.Markdown("", visible=False)
            user_id_hidden = gr.Textbox(visible=False)
            
            with gr.Column(visible=False) as reset_password_form:
                gr.Markdown("### 🔐 ตั้งรหัสผ่านใหม่")
                new_password_input = gr.Textbox(label="รหัสผ่านใหม่", type="password")
                confirm_new_password_input = gr.Textbox(label="ยืนยันรหัสผ่านใหม่", type="password")
                reset_password_btn = gr.Button("🔐 รีเซ็ตรหัสผ่าน", variant="primary")
                reset_final_message = gr.Markdown("", visible=False)
                back_to_login_btn = gr.Button("← กลับ", visible=False, variant="secondary")
            
            cancel_reset_btn = gr.Button("✖ ยกเลิก", variant="secondary")
        
        # ==================== MAIN APP ====================
        with gr.Column(visible=False) as main_app:
            
            with gr.Row():
                with gr.Column(scale=4):
                    gr.Markdown("")
                with gr.Column(scale=1):
                    logout_btn = gr.Button("ออกจากระบบ", variant="secondary", elem_classes=["logout-btn"])
            
            with gr.Tabs():
                # ============ TRANSCRIPTION TAB ============
                with gr.Tab("🎙️ แปลงเสียง"):
                    gr.Markdown("## แปลงเสียงเป็นข้อความ")
                    
                    with gr.Row():
                        # Left - Input
                        with gr.Column(scale=1):
                            gr.Markdown("### 📥 อัปโหลดไฟล์")
                            file_upload = gr.File(
                                label="📂 อัปโหลดไฟล์เสียง/วิดีโอ",
                                type="filepath",
                                file_types=[".wav", ".mp3", ".ogg", ".m4a", ".mp4", ".mov", ".avi", ".mkv"]
                            )
                            
                            audio_player = gr.Audio(label="🔊 ตัวอย่างเสียง", visible=False)
                            video_player = gr.Video(label="🎥 ตัวอย่างวิดีโอ", visible=False)
                            
                            gr.Markdown("### ⚙️ การตั้งค่า")
                            
                            # Main settings (always visible)
                            language = gr.Dropdown(
                                choices=[(v, k) for k, v in ALLOWED_LANGS.items()],
                                label="🌐 ภาษา",
                                value="th-TH"
                            )
                            
                            diarization_enabled = gr.Checkbox(label="🎭 แยกผู้พูด", value=False)
                            speakers = gr.Slider(1, 10, 2, step=1, label="👥 จำนวนผู้พูดสูงสุด", visible=False)
                            timestamps = gr.Checkbox(label="⏱️ แสดงเวลา", value=False)
                            
                            # Advanced audio settings (hidden by default)
                            with gr.Accordion("🎚️ ตั้งค่าขั้นสูง", open=False):
                                audio_format = gr.Dropdown(
                                    choices=AUDIO_FORMATS, value="wav", label="รูปแบบไฟล์", visible=False
                                )
                                audio_processing = gr.Dropdown(
                                    choices=[
                                        ("มาตรฐาน", "standard"),
                                        ("คุณภาพสูง", "advanced"),
                                        ("เร็ว", "minimal")
                                    ],
                                    value="standard", label="🔊 ระดับคุณภาพเสียง"
                                )
                                profanity = gr.Dropdown(
                                    choices=[
                                        ("ซ่อนคำหยาบ", "masked"),
                                        ("ลบคำหยาบ", "removed"),
                                        ("แสดงตามจริง", "raw")
                                    ],
                                    value="masked", label="🤐 การจัดการคำหยาบ"
                                )
                                punctuation = gr.Dropdown(
                                    choices=[
                                        ("อัตโนมัติ", "automatic"),
                                        ("ตามคำบอก", "dictated"),
                                        ("ไม่ใช้", "none")
                                    ],
                                    value="automatic", label="เครื่องหมายวรรคตอน"
                                )
                                
                                # Advanced vocabulary option
                                lexical = gr.Checkbox(label="📖 รูปแบบคำศัพท์", value=False)
                            
                            submit_btn = gr.Button("🚀 เริ่มแปลงเสียง", variant="primary", size="lg")
                        
                        # Right - Results
                        with gr.Column(scale=1):
                            gr.Markdown("### 📊 ผลลัพธ์")
                            
                            auto_refresh_status_display = gr.HTML(
                                value="", visible=True, elem_classes=["auto-refresh-indicator"]
                            )
                            
                            status_display = gr.Textbox(
                                lines=3, interactive=False, show_label=False,
                                placeholder="อัปโหลดไฟล์แล้วคลิก 'เริ่มแปลงเสียง'...",
                                elem_classes=["status-display"]
                            )
                            
                            job_info = gr.Textbox(lines=1, interactive=False, show_label=False)
                            
                            refresh_btn = gr.Button("🔄 ตรวจสอบสถานะ", variant="secondary")
                            
                            transcript_output = gr.Textbox(
                                label="ข้อความที่แปลงแล้ว",
                                lines=15, interactive=False,
                                elem_classes=["status-display"]
                            )
                            
                            download_info = gr.HTML(value="", elem_classes=["download-info"])
                            download_btn = gr.DownloadButton(
                                label="📥 ดาวน์โหลดไฟล์", visible=False,
                                variant="primary", size="lg",
                                elem_classes=["download-btn"]
                            )
                
                # ============ AI SUMMARY TAB ============
                with gr.Tab("🤖 สรุป AI"):
                    gr.Markdown("## สรุปการประชุมด้วย AI")
                    
                    with gr.Row():
                        # Left - Input
                        with gr.Column(scale=1):
                            gr.Markdown("### 📥 ข้อมูลนำเข้า")
                            
                            # Input method selection via sub-tabs
                            with gr.Tabs():
                                with gr.Tab("📝 วางข้อความ"):
                                    transcript_text_input = gr.Textbox(
                                        label="วางข้อความที่นี่",
                                        lines=10,
                                        placeholder="วางข้อความการประชุมหรือบทสนทนาที่นี่..."
                                    )
                                
                                with gr.Tab("📁 อัปโหลดไฟล์"):
                                    transcript_file_input = gr.File(
                                        label="อัปโหลดไฟล์ข้อความ",
                                        file_types=[".txt"]
                                    )
                            
                            # Additional documents (always visible)
                            document_files_input = gr.File(
                                label="📄 เอกสารเพิ่มเติม (ไม่บังคับ)",
                                file_count="multiple",
                                file_types=[".pdf", ".docx", ".pptx", ".xlsx", ".txt"]
                            )
                            
                            gr.Markdown("### 🎯 การตั้งค่า")
                            
                            with gr.Row():
                                # Simple dropdown for summary type
                                summary_format = gr.Dropdown(
                                    choices=[
                                        ("รายงานการประชุม", SUMMARY_FMT_MEETING),
                                        (SUMMARY_FMT_EXECUTIVE, SUMMARY_FMT_EXECUTIVE),
                                        ("รายงานประชุมภายนอก", "รายงานการประชุมภายนอก"),
                                        ("สรุปการเรียนรู้/สัมมนา", "บทสรุปการเรียนรู้หรืองานสัมมนา"),
                                        ("สรุปทั่วไป", "ทั่วไป"),
                                        ("รูปแบบกำหนดเอง", "custom_format"),
                                        ("ไม่ใช้รูปแบบ (ข้อความล้วน)", "no_format")
                                    ],
                                    value=SUMMARY_FMT_MEETING,
                                    label="📋 รูปแบบการสรุป"
                                )
                                
                                output_language = gr.Dropdown(
                                    choices=[
                                        ("ตามภาษาต้นฉบับ", "Auto-Detect"),
                                        ("ไทย", "ไทย"),
                                        ("English", "English"),
                                        ("中文", "中文")
                                    ],
                                    value="Auto-Detect",
                                    label="🌐 ภาษาผลลัพธ์"
                                )
                            
                            # Custom instructions - always visible as optional input
                            ai_instructions = gr.Textbox(
                                label="💬 คำสั่งเพิ่มเติม (ไม่บังคับ)",
                                lines=3,
                                placeholder=SUMMARY_DEFAULT_INSTRUCTIONS,
                                visible=True,
                                info="หากไม่กรอก จะใช้คำสั่งเริ่มต้นตามรูปแบบที่เลือก"
                            )
                            
                            # Hidden advanced options
                            with gr.Accordion("⚙️ ตัวเลือกเพิ่มเติม", open=False):
                                include_timestamps = gr.Checkbox(label="⏱️ รวมเวลา", value=True)
                                include_action_items = gr.Checkbox(label="✅ เน้น Action Items", value=True)
                            
                            generate_summary_btn = gr.Button("🚀 สร้างสรุป AI", variant="primary", size="lg")
                        
                        # Right - Results
                        with gr.Column(scale=1):
                            gr.Markdown("### 📊 ผลลัพธ์")
                            
                            ai_auto_refresh_status = gr.HTML(
                                value="", visible=True, elem_classes=["auto-refresh-indicator"]
                            )
                            
                            ai_status_display = gr.Textbox(
                                lines=2, interactive=False, show_label=False,
                                placeholder="ให้ข้อความแล้วคลิก 'สร้างสรุป AI'...",
                                elem_classes=["status-display"]
                            )
                            
                            ai_job_info = gr.Textbox(lines=1, interactive=False, show_label=False)
                            
                            check_ai_status_btn = gr.Button("🔄 ตรวจสอบสถานะ", variant="secondary")
                            
                            ai_summary_output = gr.Textbox(
                                label="สรุป AI",
                                lines=18, interactive=False,
                                elem_classes=["status-display"]
                            )
                            
                            ai_download_info = gr.HTML(value="", elem_classes=["download-info"])
                            ai_download_btn = gr.DownloadButton(
                                label="📥 ดาวน์โหลดสรุป AI", visible=False,
                                variant="primary", size="lg",
                                elem_classes=["download-btn"]
                            )
                
                # ============ HISTORY TAB ============
                with gr.Tab("📚 ประวัติ") as history_tab:
                    gr.Markdown("### 📋 ประวัติการใช้บริการ")
                    
                    with gr.Tabs():
                        with gr.Tab("🎙️ การแปลงเสียง"):
                            with gr.Row():
                                refresh_transcription_history_btn = gr.Button("🔄 รีเฟรช", variant="primary")
                                download_all_transcripts_btn = gr.Button("📦 ดาวน์โหลดทั้งหมด", variant="secondary")
                                show_all_transcriptions_checkbox = gr.Checkbox(label="แสดงทั้งหมด", value=False)
                            
                            transcription_history_table = gr.Dataframe(
                                headers=["วันที่", "ชื่อไฟล์", "ภาษา", "วิธีการ", "สถานะ", "ระยะเวลา", "รหัส", "ดาวน์โหลด"],
                                col_count=(8, "fixed"),
                                row_count=(20, "dynamic"),
                                interactive=False,
                                elem_classes=["history-table"]
                            )
                            
                            transcript_zip_download = gr.File(visible=False)
                            transcript_downloads = [gr.File(visible=False) for _ in range(50)]
                        
                        with gr.Tab("🤖 สรุป AI"):
                            with gr.Row():
                                refresh_ai_summary_history_btn = gr.Button("🔄 รีเฟรช", variant="primary")
                                download_all_summaries_btn = gr.Button("📦 ดาวน์โหลดทั้งหมด", variant="secondary")
                                show_all_summaries_checkbox = gr.Checkbox(label="แสดงทั้งหมด", value=False)
                            
                            ai_summary_history_table = gr.Dataframe(
                                headers=["วันที่", "แหล่งข้อมูล", "ภาษา", "สถานะ", "ระยะเวลา", "รหัส", "ดาวน์โหลด"],
                                col_count=(7, "fixed"),
                                row_count=(20, "dynamic"),
                                interactive=False,
                                elem_classes=["history-table"]
                            )
                            
                            summary_zip_download = gr.File(visible=False)
                            summary_downloads = [gr.File(visible=False) for _ in range(50)]
                
                # ============ SETTINGS TAB ============
                with gr.Tab("⚙️ ตั้งค่า"):
                    gr.Markdown("### 🔒 ความเป็นส่วนตัว")
                    
                    with gr.Row():
                        with gr.Column():
                            gr.Markdown("#### 📊 ส่งออกข้อมูล")
                            export_btn = gr.Button("📦 ส่งออกข้อมูลของฉัน", variant="primary")
                            export_status = gr.Textbox(show_label=False, interactive=False)
                            export_file = gr.File(visible=False)
                        
                        with gr.Column():
                            gr.Markdown("#### 📧 การตลาด")
                            marketing_consent_checkbox = gr.Checkbox(label="รับข้อมูลการตลาด", value=False)
                            update_consent_btn = gr.Button("✅ อัปเดต", variant="secondary")
                            consent_status = gr.Textbox(show_label=False, interactive=False)
                    
                    gr.Markdown("---")
                    gr.Markdown("#### ⚠️ ลบบัญชี")
                    gr.Markdown("*การดำเนินการนี้จะลบข้อมูลทั้งหมดอย่างถาวร*")
                    deletion_confirmation = gr.Textbox(label="พิมพ์ 'DELETE MY ACCOUNT' เพื่อยืนยัน")
                    delete_account_btn = gr.Button("🗑️ ลบบัญชี", variant="stop")
                    deletion_status = gr.Textbox(show_label=False, interactive=False)
                    
                    gr.Markdown("---")
                    gr.Markdown("#### ☁️ Cloud Storage")
                    refresh_cloud_stats_btn = gr.Button("🔄 ดูสถิติ", variant="primary")
                    cloud_stats_display = gr.Textbox(lines=6, interactive=False, show_label=False)
                
                # ============ HELP TAB ============
                with gr.Tab("❓ ช่วยเหลือ"):
                    gr.Markdown("""
## 📖 คู่มือการใช้งาน AI Meeting Summary

### 🎙️ แปลงเสียงเป็นข้อความ
1. **อัปโหลดไฟล์** - รองรับไฟล์เสียง (MP3, WAV, OGG, M4A) และวิดีโอ (MP4, MOV, AVI)
2. **เลือกภาษา** - ภาษาไทย, อังกฤษ, จีน และอื่นๆ
3. **ตั้งค่าเพิ่มเติม** (ไม่บังคับ):
   - 🔊 คุณภาพเสียง: มาตรฐาน / คุณภาพสูง / เร็ว
   - 🤐 การจัดการคำหยาบ: ซ่อน / ลบ / แสดงตามจริง
   - 🎭 แยกผู้พูด: สำหรับการประชุมหลายคน
4. **คลิก "เริ่มแปลงเสียง"** และรอผลลัพธ์

---

### 🤖 สรุปด้วย AI
1. **ใส่ข้อมูล** - วางข้อความหรืออัปโหลดไฟล์ข้อความ
2. **เลือกรูปแบบการสรุป**:
   - 📋 รายงานการประชุม - สำหรับบันทึกการประชุมภายใน
   - 📊 บทสรุปสำหรับผู้บริหาร - สรุปสั้นกระชับ
   - 🤝 รายงานประชุมภายนอก - สำหรับการประชุมกับภายนอก
   - 📚 สรุปการเรียนรู้ - สำหรับสัมมนาและการอบรม
3. **เพิ่มคำสั่งเพิ่มเติม** (ไม่บังคับ) เช่น "เน้น action items"
4. **คลิก "สร้างสรุป AI"**

---

### 📚 ประวัติการใช้งาน
- ดูประวัติการแปลงเสียงและสรุป AI ทั้งหมด
- ดาวน์โหลดผลลัพธ์ย้อนหลังได้

---

### ⚙️ ตั้งค่าบัญชี
- ส่งออกข้อมูลของคุณ
- ตั้งค่าความยินยอมทางการตลาด
- ลบบัญชี (ถาวร)

---

### 💡 เคล็ดลับ
- ใช้ไฟล์เสียงคุณภาพดี จะได้ผลลัพธ์แม่นยำกว่า
- สำหรับการประชุมยาว แนะนำใช้โหมด "คุณภาพสูง"
- AI จะสรุปตามข้อมูลที่มี หากข้อมูลไม่ครบจะระบุว่า "ไม่ระบุ"

---

### 📞 ติดต่อสอบถาม
หากมีปัญหาในการใช้งาน กรุณาติดต่อผู้ดูแลระบบ
                    """)
        
        # ==================== TIMERS ====================
        transcript_timer = gr.Timer(10.0)
        ai_timer = gr.Timer(10.0)
        session_check_timer = gr.Timer(5.0)
        
        # ==================== EVENT HANDLERS ====================
        
        # File preview helper
        def update_players(fp):
            from src.utils.file_helpers import normalize_filepath, get_file_type
            fp = normalize_filepath(fp)
            if not fp or not os.path.exists(fp):
                return gr.update(visible=False), gr.update(visible=False)
            
            file_type = get_file_type(fp)
            if file_type == 'audio':
                return gr.update(visible=True, value=fp), gr.update(visible=False)
            if file_type == 'video':
                return gr.update(visible=False), gr.update(visible=True, value=fp)
            return gr.update(visible=False), gr.update(visible=False)
        
        file_upload.change(update_players, inputs=file_upload, outputs=[audio_player, video_player])
        
        # Auth handlers
        login_btn.click(
            login_user_with_session,
            inputs=[login_email, login_password],
            outputs=[login_status, current_user, session_id_state, session_id_input,
                    auth_section, main_app, user_stats_display]
        ).then(
            on_user_login, inputs=[current_user], outputs=[marketing_consent_checkbox]
        ).then(
            lambda u: ("", "") if u else (gr.update(), gr.update()),
            inputs=[current_user], outputs=[login_email, login_password]
        ).then(
            # Auto-load transcription history after login
            refresh_transcription_history,
            inputs=[current_user, show_all_transcriptions_checkbox, session_id_state],
            outputs=[transcription_history_table, user_stats_display, transcript_zip_download] + transcript_downloads
        ).then(
            # Auto-load AI summary history after login
            refresh_ai_summary_history,
            inputs=[current_user, show_all_summaries_checkbox, session_id_state],
            outputs=[ai_summary_history_table, user_stats_display, summary_zip_download] + summary_downloads
        )
        
        register_btn.click(
            register_user,
            inputs=[reg_email, reg_username, reg_password, reg_confirm_password,
                    gdpr_consent, data_retention_consent, marketing_consent],
            outputs=[register_status, login_after_register]
        )
        
        login_after_register.click(lambda: (gr.update(selected=0), ""), outputs=[auth_tabs, register_status])
        
        logout_btn.click(
            logout_user_with_session,
            inputs=[session_id_state],
            outputs=[current_user, session_id_state, session_id_input, login_status,
                    auth_section, main_app, user_stats_display]
        )
        
        # Session restoration with auto-load history
        # CRITICAL: js parameter reads stored session ticket from localStorage BEFORE Python runs
        # This fixes the race condition where demo.load fires before JS window.load event
        demo.load(
            restore_session_on_load,
            inputs=[session_id_input],
            outputs=[current_user, session_id_state, auth_section, main_app, user_stats_display],
            js="(sessionId) => { try { const t = localStorage.getItem('ai_conference_ticket'); if (t) { const la = localStorage.getItem('ai_conference_last_activity'); if (la && (Date.now() - parseInt(la)) > 3600000) { localStorage.removeItem('ai_conference_ticket'); localStorage.removeItem('ai_conference_last_activity'); return ''; } return t; } } catch(e) {} return sessionId || ''; }"
        ).then(
            # Auto-load transcription history after session restore
            refresh_transcription_history,
            inputs=[current_user, show_all_transcriptions_checkbox, session_id_state],
            outputs=[transcription_history_table, user_stats_display, transcript_zip_download] + transcript_downloads
        ).then(
            # Auto-load AI summary history after session restore
            refresh_ai_summary_history,
            inputs=[current_user, show_all_summaries_checkbox, session_id_state],
            outputs=[ai_summary_history_table, user_stats_display, summary_zip_download] + summary_downloads
        )
        
        session_check_timer.tick(
            check_session_validity,
            inputs=[session_id_state],
            outputs=[current_user, auth_section, main_app, user_stats_display]
        )
        
        # Backup: restore session when JavaScript updates the hidden session input
        # Handles edge cases like WebSocket reconnects or delayed JS execution
        session_id_input.change(
            restore_session_on_load,
            inputs=[session_id_input],
            outputs=[current_user, session_id_state, auth_section, main_app, user_stats_display]
        )
        
        # Password reset handlers
        forgot_password_btn.click(
            lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[password_reset_section, auth_section]
        )
        
        cancel_reset_btn.click(
            lambda: (gr.update(visible=False), gr.update(visible=True), "", gr.update(visible=False),
                    gr.update(visible=False), "", "", "", gr.update(visible=False), gr.update(visible=False)),
            outputs=[password_reset_section, auth_section, reset_email_input, reset_status_message,
                    reset_password_form, user_id_hidden, new_password_input, confirm_new_password_input,
                    reset_final_message, back_to_login_btn]
        )
        
        request_reset_btn.click(
            request_password_reset_ui,
            inputs=[reset_email_input],
            outputs=[reset_status_message, reset_password_form, user_id_hidden]
        )
        
        reset_password_btn.click(
            reset_password_with_token_ui,
            inputs=[user_id_hidden, new_password_input, confirm_new_password_input],
            outputs=[reset_final_message, back_to_login_btn]
        )
        
        back_to_login_btn.click(
            lambda: (gr.update(visible=False), gr.update(visible=True), "", gr.update(visible=False),
                    gr.update(visible=False), "", "", "", gr.update(visible=False), gr.update(visible=False), gr.update(selected=0)),
            outputs=[password_reset_section, auth_section, reset_email_input, reset_status_message,
                    reset_password_form, user_id_hidden, new_password_input, confirm_new_password_input,
                    reset_final_message, back_to_login_btn, auth_tabs]
        )
        
        # Diarization toggle
        diarization_enabled.change(lambda e: gr.update(visible=e), inputs=[diarization_enabled], outputs=[speakers])
        
        # Transcription handlers
        submit_btn.click(
            submit_transcription,
            inputs=[file_upload, language, audio_format, diarization_enabled,
                    speakers, profanity, punctuation, timestamps, lexical,
                    audio_processing, current_user, session_id_state],
            outputs=[status_display, transcript_output, download_info, download_btn, job_info,
                    job_state, auto_refresh_status_display, user_stats_display]
        )
        
        refresh_btn.click(
            check_current_job_status,
            inputs=[job_state, current_user, session_id_state],
            outputs=[status_display, transcript_output, download_info, download_btn, job_info,
                    auto_refresh_status_display, user_stats_display]
        )
        
        transcript_timer.tick(
            auto_refresh_status,
            inputs=[job_state, current_user, session_id_state],
            outputs=[status_display, transcript_output, download_info, download_btn, job_info,
                    auto_refresh_status_display, user_stats_display]
        )
        
        # AI Summary handlers
        
        # Update placeholder text based on format selection
        FORMAT_PLACEHOLDERS = {
            SUMMARY_FMT_MEETING: "สรุปครบถ้วน เป็นทางการ ระบุผู้รับผิดชอบ มติ/การตัดสินใจ แบ่งช่วงเวลา พร้อม Next Steps จัดกลุ่มตามผู้รับผิดชอบ",
            SUMMARY_FMT_EXECUTIVE: "สรุปกระชับ เน้นมติสำคัญ Action Items ประเด็นติดตาม เหมาะสำหรับผู้บริหาร",
            "รายงานการประชุมภายนอก": "สรุปทางการ ระบุหน่วยงาน ผู้เข้าร่วม มติร่วม ข้อตกลง แบ่งช่วงเวลา พร้อม Next Steps",
            "บทสรุปการเรียนรู้หรืองานสัมมนา": "สรุปประเด็นเรียนรู้ ผู้บรรยาย เครื่องมือ/ลิงก์อ้างอิง Use Cases พร้อมแหล่งข้อมูลเพิ่มเติม",
            "ทั่วไป": SUMMARY_DEFAULT_INSTRUCTIONS,
            "custom_format": "ระบุรูปแบบที่ต้องการ เช่น 'สรุปเฉพาะ action items เป็นตาราง' หรือ 'เขียนเป็นบล็อก'",
            "no_format": "ระบุคำสั่งเพิ่มเติม เช่น 'เน้นตัวเลขและสถิติ' หรือ 'สรุปตามลำดับเวลา'"
        }
        
        def update_instructions_placeholder(selected_format):
            placeholder = FORMAT_PLACEHOLDERS.get(selected_format,
                SUMMARY_DEFAULT_INSTRUCTIONS)
            label = "💬 คำสั่งกำหนดเอง (จำเป็น)" if selected_format == "custom_format" else "💬 คำสั่งเพิ่มเติม (ไม่บังคับ)"
            return gr.update(placeholder=placeholder, label=label)
        
        summary_format.change(
            update_instructions_placeholder,
            inputs=[summary_format],
            outputs=[ai_instructions]
        )
        
        generate_summary_btn.click(
            submit_ai_summary_new,
            inputs=[transcript_text_input, transcript_file_input, document_files_input,
                    ai_instructions, summary_format, output_language,
                    include_timestamps, include_action_items, current_user, session_id_state],
            outputs=[ai_status_display, ai_summary_output, ai_download_info, ai_download_btn,
                    ai_job_info, summary_job_state, ai_auto_refresh_status, user_stats_display]
        )
        
        check_ai_status_btn.click(
            check_ai_summary_status,
            inputs=[summary_job_state, current_user, session_id_state],
            outputs=[ai_status_display, ai_summary_output, ai_download_info, ai_download_btn,
                    ai_job_info, ai_auto_refresh_status, user_stats_display]
        )
        
        ai_timer.tick(
            auto_refresh_ai_summary,
            inputs=[summary_job_state, current_user, session_id_state],
            outputs=[ai_status_display, ai_summary_output, ai_download_info, ai_download_btn,
                    ai_job_info, ai_auto_refresh_status, user_stats_display]
        )
        
        # History handlers
        refresh_transcription_history_btn.click(
            refresh_transcription_history,
            inputs=[current_user, show_all_transcriptions_checkbox, session_id_state],
            outputs=[transcription_history_table, user_stats_display, transcript_zip_download] + transcript_downloads
        )
        
        show_all_transcriptions_checkbox.change(
            refresh_transcription_history,
            inputs=[current_user, show_all_transcriptions_checkbox, session_id_state],
            outputs=[transcription_history_table, user_stats_display, transcript_zip_download] + transcript_downloads
        )
        
        download_all_transcripts_btn.click(
            create_transcript_zip_archive,
            inputs=[current_user, session_id_state],
            outputs=[transcript_zip_download]
        )
        
        refresh_ai_summary_history_btn.click(
            refresh_ai_summary_history,
            inputs=[current_user, show_all_summaries_checkbox, session_id_state],
            outputs=[ai_summary_history_table, user_stats_display, summary_zip_download] + summary_downloads
        )
        
        show_all_summaries_checkbox.change(
            refresh_ai_summary_history,
            inputs=[current_user, show_all_summaries_checkbox, session_id_state],
            outputs=[ai_summary_history_table, user_stats_display, summary_zip_download] + summary_downloads
        )
        
        download_all_summaries_btn.click(
            create_summary_zip_archive,
            inputs=[current_user, session_id_state],
            outputs=[summary_zip_download]
        )
        
        # History tab auto-load on select
        # When user navigates to History tab, automatically load both transcription and AI summary history
        history_tab.select(
            refresh_transcription_history,
            inputs=[current_user, show_all_transcriptions_checkbox, session_id_state],
            outputs=[transcription_history_table, user_stats_display, transcript_zip_download] + transcript_downloads
        ).then(
            refresh_ai_summary_history,
            inputs=[current_user, show_all_summaries_checkbox, session_id_state],
            outputs=[ai_summary_history_table, user_stats_display, summary_zip_download] + summary_downloads
        )
        
        # Settings handlers
        export_btn.click(export_user_data, inputs=[current_user, session_id_state], outputs=[export_status, export_file])
        update_consent_btn.click(
            update_marketing_consent,
            inputs=[current_user, marketing_consent_checkbox, session_id_state],
            outputs=[consent_status]
        )
        delete_account_btn.click(
            delete_user_account,
            inputs=[current_user, deletion_confirmation, session_id_state],
            outputs=[deletion_status, current_user, auth_section, main_app]
        )
        refresh_cloud_stats_btn.click(
            view_cloud_storage_stats,
            inputs=[current_user, session_id_state],
            outputs=[cloud_stats_display]
        )
        
        # Error log handlers
    
    return demo


# Create and launch
demo = create_simplified_interface()

if __name__ == "__main__":
    # Support PORT env var for cloud deployments (Azure, Docker port mapping)
    port = int(os.environ.get("PORT", os.environ.get("GRADIO_SERVER_PORT", "7860")))
    print(f"🚀 Starting AI Meeting Summary on port {port}...")
    print("🔐 Session management enabled")
    print("☁️ Cloud storage enabled")
    demo.launch(
        server_name="0.0.0.0",  # nosec B104 - required for Azure App Service container
        server_port=port,
        share=False,
        show_error=True
    )
