import streamlit as st

st.set_page_config(page_title="Version 2", layout="wide")

st.markdown("""
<style>
h1 { color: #0082C3; }
</style>
""", unsafe_allow_html=True)

st.title("🚀 Version 2 Features")

tabs = st.tabs(["Features", "History", "Migration", "Performance", "FAQ"])

with tabs[0]:
    st.header("Key Features")
    st.write("✅ UK Size Extraction")
    st.write("✅ AI Matching (Groq)")
    st.write("✅ Performance Optimizations")

with tabs[1]:
    st.header("Release History")
    st.write("**Version 2.0** - April 15, 2026")

with tabs[2]:
    st.header("Migration Guide")
    st.info("✅ Backward compatible with Version 1")

with tabs[3]:
    st.header("Performance")
    col1, col2, col3 = st.columns(3)
    col1.metric("Speed", "+50%")
    col2.metric("Memory", "-30%")
    col3.metric("Load Time", "2s")

with tabs[4]:
    st.header("FAQ")
    with st.expander("Is Version 2 compatible?"):
        st.write("Yes, fully backward compatible!")
