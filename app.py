import streamlit as st
import pandas as pd
import json
import io
import zipfile

# Web Page Title သတ်မှတ်ခြင်း
st.set_page_config(page_title="Excel to Separate JSONs Converter", page_icon="📊")

# ✨ Icon Error မတက်စေဘဲ မြန်မာစာလုံးဝမပျက်စေမယ့် CSS Styling စနစ်သစ်
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Padauk:wght@400;700&family=Pyidaungsu&display=swap');
    
    html, body, p, h1, h2, h3, h4, span, button, label, .stMarkdown {
        font-family: 'Pyidaungsu', 'Padauk', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif !important;
    }
    
    button svg, div svg, [data-testid="stFileUploaderIcon"] {
        font-family: inherit !important;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("📊 Excel to Sheet-by-Sheet JSON Converter")
st.write("Excel Sheet တစ်ခုချင်းစီကို JSON ဖိုင်သီးသန့်ခွဲထုတ်ပြီး ZIP အဖြစ် ဒေါင်းလုဒ်ရယူပါ။")

# File Uploader ထည့်ခြင်း
uploaded_file = st.file_uploader("Excel ဖိုင်တင်ရန် (.xlsx)", type=["xlsx"])

if uploaded_file is not None:
    try:
        # Excel ဖိုင်ထဲက Sheet နာမည်အားလုံးကို ဖတ်ခြင်း
        excel_file = pd.ExcelFile(uploaded_file)
        sheet_names = excel_file.sheet_names
        
        # Memory ပေါ်မှာတင် ZIP file အလွတ်တစ်ခု ဖန်တီးခြင်း
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # Sheet တစ်ခုချင်းစီကို ပတ်ဖတ်ပြီး JSON သီးသန့်စီ ပြောင်းမယ်
            for sheet in sheet_names:
                df = pd.read_excel(uploaded_file, sheet_name=sheet)
                df = df.fillna("") # အကွက်လွတ်များကို ရှင်းထုတ်
                
                sheet_data = df.to_dict(orient='records')
                
                # Minified JSON string အဖြစ် ပြောင်းလဲခြင်း
                json_string = json.dumps(
                    sheet_data, 
                    ensure_ascii=False, 
                    separators=(',', ':'),
                    default=str
                )
                
                # Sheet နာမည်အတိုင်း .json ဖိုင်ဆောက်ပြီး ZIP ထဲသို့ ထည့်ခြင်း
                filename = f"{sheet}.json"
                zip_file.writestr(filename, json_string)
                
        # Buffer position ကို အစသို့ ပြန်ရွှေ့ခြင်း
        zip_buffer.seek(0)
        
        st.success(f"🎉 JSON ခွဲထုတ်ခြင်း အောင်မြင်ပါသည်။ Sheet ပေါင်း {len(sheet_names)} ခုကို ဖိုင်သီးသန့်စီ ခွဲပေးထားပါသည်။")
        
        # Display sheet names that will be inside the ZIP
        st.write("📁 **ZIP ဖိုင်ထဲတွင် ပါဝင်မည့် ဖိုင်များ:**")
        for sheet in sheet_names:
            st.write(f"📄 `{sheet}.json`")
            
        # ZIP file ကို Download ချမည့် ခလုတ်ပြုလုပ်ခြင်း
        st.download_button(
            label="📥 Download All Sheets JSON (ZIP)",
            data=zip_buffer,
            file_name="excel_sheets_json.zip",
            mime="application/zip"
        )
        
    except Exception as e:
        st.error(f"❌ အမှားအယွင်းတစ်ခု ရှိနေပါသည်: {e}")
