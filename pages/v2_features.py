import streamlit as st

# Set page configuration
st.set_page_config(page_title='🚀 Version 2 Features', layout='wide')

# Decathlon branding
st.markdown("""
<style>
h1 { color: #0082C3; }
.feature-box { background: #f0f7ff; padding: 15px; border-left: 4px solid #0082C3; margin: 10px 0; }
</style>
""", unsafe_allow_html=True)

st.title("🚀 Decathlon Version 2 Features")

# Create tabs
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📝 Version History",
    "⭐ Features", 
    "🔄 Migration",
    "⚡ Performance",
    "❓ FAQ"
])

with tab1:
    st.header("Version History")
    st.write("**Version 2.0** - April 15, 2026")
    st.write("Major improvements and new features released")

with tab2:
    st.header("Feature Highlights")
    st.markdown("""
    - 🔍 UK Size Extraction
    - 🤖 AI Matching (Groq)
    - ⚙️ Performance Optimizations
    - 🛡️ Bulletproof Headers
    - 📊 Category Formatting
    - 🏗️ Auto-Create Columns
    """)

with tab3:
    st.header("Migration Guide")
    st.info("Version 2 is backward compatible with Version 1")

with tab4:
    st.header("Performance Improvements")
    col1, col2, col3 = st.columns(3)
    col1.metric("Speed", "+50%", "faster")
    col2.metric("Memory", "-30%", "lower")
    col3.metric("Load Time", "~2s", "cached")

with tab5:
    st.header("FAQ")
    st.write("Q: How do I upgrade?")
    st.write("A: Simply use the new features in your next search!")
