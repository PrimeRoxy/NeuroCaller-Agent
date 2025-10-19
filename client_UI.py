import streamlit as st
import requests
import json
from typing import Optional

# Page configuration
st.set_page_config(
    page_title="NeuroCaller Agent",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for engaging UI
st.markdown("""
<style>
    .main-header {
        font-size: 3rem;
        font-weight: bold;
        text-align: center;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        text-align: center;
        color: #666;
        font-size: 1.2rem;
        margin-bottom: 2rem;
    }
    .stTextArea textarea {
        border-radius: 10px;
    }
    .upload-box {
        border: 2px dashed #667eea;
        border-radius: 10px;
        padding: 20px;
        text-align: center;
        background-color: #f8f9ff;
    }
    .response-box {
        background-color: #ffffff;
        border-left: 4px solid #667eea;
        border-radius: 10px;
        padding: 20px;
        margin-top: 20px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        font-size: 1.1rem;
        line-height: 1.6;
    }
</style>
""", unsafe_allow_html=True)

# Header
st.markdown('<p class="main-header">🧠 NeuroCaller Agent</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Intelligent AI-Powered Calling Assistant</p>', unsafe_allow_html=True)

# Sidebar for configuration
with st.sidebar:
    st.header("⚙️ Configuration")
    api_url = st.text_input(
        "API Endpoint",
        value="http://localhost:8000/api/ask_neurocaller",
        help="Enter your NeuroCaller API endpoint"
    )
    
    st.divider()
    
    st.header("📊 Features")
    st.markdown("""
    - 🌐 Multi-language support
    - ⚡ Real-time interaction
    - 🤖 Human-like behavior
    - 📚 RAG-powered knowledge
    - 📞 Plivo calling integration
    - 🎙️ Call recording & transcription
    """)
    
    st.divider()
    
    st.header("ℹ️ About")
    st.info("NeuroCaller uses advanced AI to handle automated calling with natural conversation capabilities.")

# Main content area
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("💬 Ask NeuroCaller")
    
    # Query input
    query = st.text_area(
        "Enter your query:",
        placeholder="E.g., 'Call the customer list and ask about their satisfaction with our service'",
        height=150,
        help="Describe what you want NeuroCaller to do"
    )
    
    # File upload (optional)
    st.markdown("### 📎 Upload File (Optional)")
    uploaded_file = st.file_uploader(
        "Upload a document, image, or audio file",
        type=['pdf', 'txt', 'docx', 'png', 'jpg', 'jpeg', 'mp3', 'wav', 'csv', 'xlsx'],
        help="Optional: Upload files for context or analysis"
    )
    
    # Submit button
    col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 2])
    with col_btn1:
        submit_btn = st.button("🚀 Submit", type="primary", use_container_width=True)
    with col_btn2:
        clear_btn = st.button("🗑️ Clear", use_container_width=True)

with col2:
    st.subheader("📋 Quick Actions")
    
    quick_actions = {
        "📞 Make Test Call": "Make a test call to verify the system",
        "📊 Analyze Call Data": "Analyze recent call transcripts and metrics",
        "👥 Customer Outreach": "Call customer list for feedback survey",
        "📝 Generate Script": "Create a calling script for sales campaign"
    }
    
    for action, description in quick_actions.items():
        if st.button(action, use_container_width=True):
            query = description

# Clear functionality
if clear_btn:
    st.rerun()

# Process query
if submit_btn:
    if not query:
        st.error("⚠️ Please enter a query before submitting.")
    else:
        with st.spinner("🔄 Processing your request..."):
            try:
                # Prepare the request
                files = {}
                data = {"query": query}
                
                if uploaded_file is not None:
                    files["file"] = (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)
                
                # Make API request
                response = requests.post(
                    api_url,
                    data=data,
                    files=files if files else None,
                    stream=True
                )
                
                if response.status_code == 200:
                    st.success("✅ Response received!")
                    
                    # Create response container
                    response_container = st.container()
                    
                    with response_container:
                        st.markdown("### 🤖 NeuroCaller Response")
                        response_text = st.empty()
                        
                        # Stream the response
                        full_response = ""
                        for line in response.iter_lines():
                            if line:
                                try:
                                    # Try to parse as JSON
                                    decoded_line = line.decode('utf-8').strip()
                                    chunk = json.loads(decoded_line)
                                    
                                    # Extract text from various possible JSON structures
                                    if isinstance(chunk, dict):
                                        if 'message' in chunk:
                                            full_response += chunk['message']
                                        elif 'text' in chunk:
                                            full_response += chunk['text']
                                        elif 'content' in chunk:
                                            full_response += chunk['content']
                                        else:
                                            # If none of the expected keys, convert to string
                                            full_response += str(chunk)
                                    else:
                                        full_response += str(chunk)
                                except json.JSONDecodeError:
                                    # If not JSON, just append the text
                                    decoded_text = line.decode('utf-8').strip()
                                    if decoded_text:
                                        full_response += decoded_text
                                
                                # Update the display with proper formatting
                                if full_response:
                                    with response_text.container():
                                        st.markdown(
                                            f"""
                                            <div style='background-color: #ffffff; 
                                                        border-left: 4px solid #667eea; 
                                                        border-radius: 10px; 
                                                        padding: 20px; 
                                                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                                                        font-size: 1.05rem;
                                                        line-height: 1.8;
                                                        color: #1f2937;'>
                                                {full_response}
                                            </div>
                                            """,
                                            unsafe_allow_html=True
                                        )
                        
                        # Show download button for response
                        st.download_button(
                            label="📥 Download Response",
                            data=full_response,
                            file_name="neurocaller_response.txt",
                            mime="text/plain"
                        )
                else:
                    st.error(f"❌ Error: {response.status_code} - {response.text}")
                    
            except requests.exceptions.ConnectionError:
                st.error("🔌 Connection Error: Cannot connect to the API. Make sure the server is running.")
            except Exception as e:
                st.error(f"❌ An error occurred: {str(e)}")

# Footer
st.divider()
col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    st.metric("Status", "🟢 Online", "Connected")
with col_f2:
    st.metric("Model", "Multi-Modal", "Active")
with col_f3:
    st.metric("Response Time", "~2s", "Avg")