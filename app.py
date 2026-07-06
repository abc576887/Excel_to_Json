import streamlit as st
import pandas as pd
import json

# Web Page Title သတ်မှတ်ခြင်း
st.set_page_config(page_title="Excel to Minified JSON Converter", page_icon="📊")
st.title("📊 Excel to Minified JSON Converter")
st.write("Token သက်သာစေမယ့် Minified JSON Format သို့ အလွယ်တကူ ပြောင်းလဲပါ။")

# File Uploader ထည့်ခြင်း
uploaded_file = st.file_uploader("Excel ဖိုင်တင်ရန် (.xlsx)", type=["xlsx"])

if uploaded_file is not None:
    try:
        # Excel ဖိုင်ထဲက Sheet နာမည်အားလုံးကို ဖတ်ခြင်း
        excel_file = pd.ExcelFile(uploaded_file)
        sheet_names = excel_file.sheet_names
        
        # Sheet အားလုံးကို သိမ်းမယ့် Dictionary
        combined_data = {}
        
        # Sheet တစ်ခုချင်းစီကို Loop ပတ်ပြီး ဖတ်မယ်
        for sheet in sheet_names:
            df = pd.read_excel(uploaded_file, sheet_name=sheet)
            df = df.fillna("") # အကွက်လွတ်တွေကို ရှင်းထုတ်
            combined_data[sheet] = df.to_dict(orient='records')
            
        # Sheet တစ်ခုတည်းပဲ ပါရင် စာသားပိုကျစ်လျစ်အောင် ပတ်ထားတဲ့ Sheet Name Key ကို ဖြုတ်ပေးမယ်
        if len(sheet_names) == 1:
            final_json_data = combined_data[sheet_names[0]]
        else:
            final_json_data = combined_data
            
        # JSON ကို Space တွေ ဖြုတ်ပြီး Minified ပုံစံ String ပြောင်းခြင်း
        json_string = json.dumps(final_json_data, ensure_ascii=False, separators=(',', ':'))
        
        st.success(f"🎉 ပြောင်းလဲခြင်း အောင်မြင်ပါသည်။ (Sheet ပေါင်း {len(sheet_names)} ခု ပါဝင်သည်)")
        
        # ပြောင်းပြီးသား JSON ရဲ့ ပုံစံအကျဉ်းကို ပြပေးခြင်း
        st.subheader("Preview (JSON ပုံစံအကျဉ်း):")
        st.code(json_string[:500] + ("..." if len(json_string) > 500 else ""), language="json")
        
        # Download Button ပြုလုပ်ခြင်း
        st.download_button(
            label="📥 Download Minified JSON",
            data=json_string,
            file_name="converted_data.json",
            mime="application/json"
        )
        
    except Exception as e:
        st.error(f"❌ အမှားအယွင်းတစ်ခု ရှိနေပါသည်: {e}")