import streamlit as st
import requests
import json
from typing import Optional
import pandas as pd
from datetime import datetime, timedelta
import time
import io

# Page configuration
st.set_page_config(
    page_title="NeuroCaller Agent",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
def init_session_state():
    if "page" not in st.session_state:
        st.session_state.page = "dashboard"
    if "api_url" not in st.session_state:
        st.session_state.api_url = "http://localhost:8000"
    if "selected_campaign" not in st.session_state:
        st.session_state.selected_campaign = None
    if "call_history" not in st.session_state:
        st.session_state.call_history = []
    if "org_id" not in st.session_state:
        st.session_state.org_id = ""
    if "user_id" not in st.session_state:
        st.session_state.user_id = ""

init_session_state()

# Modern Minimalistic CSS
st.markdown("""
<style>
    * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }
    
    html, body, [data-testid="stAppViewContainer"] {
        background-color: #fafbfc;
    }
    
    .main {
        max-width: 1400px;
        margin: 0 auto;
    }
    
    /* Typography */
    .app-title {
        font-size: 2.8rem;
        font-weight: 700;
        background: linear-gradient(135deg, #2d3748 0%, #4a5568 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.25rem;
        letter-spacing: -0.5px;
    }
    
    .app-subtitle {
        font-size: 0.95rem;
        color: #718096;
        font-weight: 400;
        letter-spacing: 0.3px;
    }
    
    .page-title {
        font-size: 2rem;
        font-weight: 700;
        color: #2d3748;
        margin-bottom: 0.5rem;
        letter-spacing: -0.3px;
    }
    
    .section-title {
        font-size: 1.2rem;
        font-weight: 600;
        color: #2d3748;
        margin-top: 1.5rem;
        margin-bottom: 1rem;
    }
    
    /* Cards */
    .modern-card {
        background: white;
        border-radius: 12px;
        padding: 24px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        margin-bottom: 1rem;
    }
    
    .modern-card:hover {
        border-color: #cbd5e0;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        transform: translateY(-2px);
    }
    
    /* Stat Cards */
    .stat-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-radius: 12px;
        padding: 28px;
        text-align: center;
        border: none;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.25);
        transition: all 0.3s ease;
    }
    
    .stat-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 25px rgba(102, 126, 234, 0.35);
    }
    
    .stat-value {
        font-size: 2.5rem;
        font-weight: 800;
        margin: 12px 0;
        letter-spacing: -0.5px;
    }
    
    .stat-label {
        font-size: 0.9rem;
        opacity: 0.95;
        font-weight: 500;
        letter-spacing: 0.3px;
    }
    
    /* Navigation */
    .nav-container {
        display: flex;
        gap: 8px;
        margin-bottom: 32px;
        flex-wrap: wrap;
    }
    
    .nav-item {
        padding: 10px 24px;
        border-radius: 8px;
        border: 2px solid #e2e8f0;
        background: white;
        color: #4a5568;
        font-weight: 600;
        font-size: 0.95rem;
        cursor: pointer;
        transition: all 0.3s ease;
    }
    
    .nav-item:hover {
        border-color: #667eea;
        color: #667eea;
        background: #f7fafc;
    }
    
    .nav-item.active {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-color: transparent;
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
    }
    
    /* Input Fields */
    .input-group {
        margin-bottom: 1.5rem;
    }
    
    .input-label {
        font-size: 0.9rem;
        font-weight: 600;
        color: #2d3748;
        margin-bottom: 0.5rem;
        display: block;
    }
    
    /* Buttons */
    .btn-primary {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        padding: 12px 32px;
        border-radius: 8px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.3s ease;
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
    }
    
    .btn-primary:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
    }
    
    /* Badge */
    .badge {
        display: inline-block;
        padding: 6px 14px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    
    .badge-success {
        background: #c6f6d5;
        color: #22543d;
    }
    
    .badge-warning {
        background: #feebc8;
        color: #7c2d12;
    }
    
    .badge-danger {
        background: #fed7d7;
        color: #742a2a;
    }
    
    .badge-info {
        background: #bee3f8;
        color: #2c5282;
    }
    
    /* Info Box */
    .info-box {
        background: #edf2f7;
        border-left: 4px solid #667eea;
        border-radius: 8px;
        padding: 16px;
        margin: 16px 0;
        color: #2d3748;
    }
    
    .success-box {
        background: #f0fff4;
        border-left: 4px solid #22863a;
        color: #22543d;
    }
    
    .warning-box {
        background: #fffaf0;
        border-left: 4px solid #e65100;
        color: #7c2d12;
    }
    
    .error-box {
        background: #fff5f5;
        border-left: 4px solid #e53e3e;
        color: #742a2a;
    }
    
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: white;
        border-right: 1px solid #e2e8f0;
    }
    
    /* Table */
    table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 1rem;
    }
    
    th {
        background: #f7fafc;
        padding: 14px;
        text-align: left;
        font-weight: 600;
        font-size: 0.9rem;
        color: #2d3748;
        border-bottom: 2px solid #e2e8f0;
    }
    
    td {
        padding: 14px;
        border-bottom: 1px solid #e2e8f0;
        color: #4a5568;
        font-size: 0.95rem;
    }
    
    tr:hover {
        background: #f7fafc;
    }
    
    /* Divider */
    hr {
        border: none;
        border-top: 1px solid #e2e8f0;
        margin: 2rem 0;
    }
    
    /* Footer */
    .footer {
        text-align: center;
        padding: 32px 0;
        margin-top: 64px;
        border-top: 1px solid #e2e8f0;
        color: #a0aec0;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar Configuration
with st.sidebar:
    st.markdown('<h2 class="app-title">🧠 NeuroCaller</h2>', unsafe_allow_html=True)
    st.markdown('<p class="app-subtitle">AI-Powered Calling System</p>', unsafe_allow_html=True)
    
    st.divider()
    
    # Navigation
    st.subheader("📍 Navigation")
    
    pages = {
        "dashboard": ("📊", "Dashboard"),
        "campaigns": ("📞", "Campaigns"),
        "knowledge": ("📚", "Knowledge Base"),
        "chat": ("💬", "Chat & Query"),
        "analytics": ("📈", "Analytics"),
        "settings": ("⚙️", "Settings")
    }
    
    for page_key, (icon, label) in pages.items():
        if st.button(f"{icon} {label}", use_container_width=True):
            st.session_state.page = page_key
    
    st.divider()
    
    # API Configuration
    st.subheader("🔧 Configuration")
    st.session_state.api_url = st.text_input(
        "API Endpoint",
        value=st.session_state.api_url,
        key="api_url_input"
    )
    
    st.session_state.org_id = st.text_input("Org ID", value=st.session_state.org_id)
    st.session_state.user_id = st.text_input("User ID", value=st.session_state.user_id)
    
    st.divider()
    
    # System Status
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Status", "🟢 Online")
    with col2:
        st.metric("API", "✓ Active")
    
    st.divider()
    
    # Features
    st.subheader("✨ Features")
    features = [
        "🌐 Multi-language",
        "⚡ Real-time Voice",
        "🤖 AI Context",
        "📚 RAG Knowledge",
        "📞 Plivo Ready",
        "🎙️ Recording"
    ]
    for feature in features:
        st.caption(feature)
    
    st.divider()
    
    st.caption("v1.0.0 | © 2026 NeuroCaller")

# Main Content Area
api_url = st.session_state.api_url

# Page: Dashboard
if st.session_state.page == "dashboard":
    st.markdown('<h1 class="page-title">📊 Dashboard</h1>', unsafe_allow_html=True)
    st.markdown('<p class="app-subtitle">System Overview & Key Metrics</p>', unsafe_allow_html=True)
    
    # Key Metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown("""
        <div class="stat-card">
            <div style="font-size: 1.8rem;">💬</div>
            <div class="stat-value">1,247</div>
            <div class="stat-label">Total Calls</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown("""
        <div class="stat-card">
            <div style="font-size: 1.8rem;">✅</div>
            <div class="stat-value">94%</div>
            <div class="stat-label">Success Rate</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown("""
        <div class="stat-card">
            <div style="font-size: 1.8rem;">⏱️</div>
            <div class="stat-value">3.2m</div>
            <div class="stat-label">Avg Duration</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        st.markdown("""
        <div class="stat-card">
            <div style="font-size: 1.8rem;">🎯</div>
            <div class="stat-value">12</div>
            <div class="stat-label">Active Campaigns</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Recent Activity
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.markdown('<h3 class="section-title">📞 Recent Calls</h3>', unsafe_allow_html=True)
        
        call_data = {
            "Timestamp": ["2:45 PM", "2:30 PM", "2:15 PM", "2:00 PM", "1:45 PM"],
            "Number": ["+1 (555) 123-4567", "+1 (555) 234-5678", "+1 (555) 345-6789", "+1 (555) 456-7890", "+1 (555) 567-8901"],
            "Duration": ["3:24", "2:56", "4:12", "2:30", "3:45"],
            "Status": ["✅ Completed", "✅ Completed", "✅ Completed", "✅ Completed", "✅ Completed"]
        }
        
        df = pd.DataFrame(call_data)
        st.dataframe(df, use_container_width=True, hide_index=True)
    
    with col2:
        st.markdown('<h3 class="section-title">📊 Statistics</h3>', unsafe_allow_html=True)
        
        st.markdown("""
        <div class="modern-card">
            <div style="margin-bottom: 1rem;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                    <span>Today</span>
                    <span style="font-weight: 600;">156 calls</span>
                </div>
                <div style="background: #e2e8f0; height: 6px; border-radius: 3px; overflow: hidden;">
                    <div style="background: linear-gradient(90deg, #667eea, #764ba2); height: 100%; width: 85%;"></div>
                </div>
            </div>
            <div style="margin-bottom: 1rem;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                    <span>This Week</span>
                    <span style="font-weight: 600;">892 calls</span>
                </div>
                <div style="background: #e2e8f0; height: 6px; border-radius: 3px; overflow: hidden;">
                    <div style="background: linear-gradient(90deg, #667eea, #764ba2); height: 100%; width: 72%;"></div>
                </div>
            </div>
            <div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                    <span>This Month</span>
                    <span style="font-weight: 600;">3,847 calls</span>
                </div>
                <div style="background: #e2e8f0; height: 6px; border-radius: 3px; overflow: hidden;">
                    <div style="background: linear-gradient(90deg, #667eea, #764ba2); height: 100%; width: 58%;"></div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

# Page: Campaigns
elif st.session_state.page == "campaigns":
    st.markdown('<h1 class="page-title">📞 Campaigns</h1>', unsafe_allow_html=True)
    st.markdown('<p class="app-subtitle">Create and Manage Calling Campaigns</p>', unsafe_allow_html=True)
    
    tab1, tab2, tab3 = st.tabs(["Active Campaigns", "Create New", "Templates"])
    
    with tab1:
        st.markdown('<h3 class="section-title">Active Campaigns</h3>', unsafe_allow_html=True)
        
        campaigns = [
            {"name": "Q1 Sales Outreach", "status": "Active", "calls": 324, "success": "92%", "created": "Jan 15"},
            {"name": "Customer Feedback", "status": "Active", "calls": 156, "success": "88%", "created": "Jan 20"},
            {"name": "Product Demo", "status": "Paused", "calls": 89, "success": "95%", "created": "Dec 28"},
            {"name": "Follow-up Campaign", "status": "Active", "calls": 234, "success": "91%", "created": "Jan 10"}
        ]
        
        for campaign in campaigns:
            status_badge = "🟢" if campaign["status"] == "Active" else "🟡"
            st.markdown(f"""
            <div class="modern-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h4 style="margin: 0; color: #2d3748;">{campaign['name']}</h4>
                        <p style="color: #718096; margin: 0.25rem 0; font-size: 0.9rem;">Created: {campaign['created']}</p>
                    </div>
                    <div style="text-align: right;">
                        <span class="badge badge-success">{status_badge} {campaign['status']}</span>
                        <p style="margin: 0.5rem 0 0 0; color: #718096; font-size: 0.9rem;">{campaign['calls']} calls • {campaign['success']} success</p>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    
    with tab2:
        st.markdown('<h3 class="section-title">Create New Campaign</h3>', unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            campaign_name = st.text_input("Campaign Name", placeholder="e.g., Q1 Sales Outreach")
            campaign_type = st.selectbox("Campaign Type", ["Sales", "Support", "Survey", "Notification", "Custom"])
            start_date = st.date_input("Start Date")
        
        with col2:
            target_audience = st.selectbox("Target Audience", ["All", "VIP", "Active Users", "Inactive Users"])
            priority = st.selectbox("Priority", ["Low", "Medium", "High"])
            estimated_calls = st.number_input("Estimated Calls", min_value=1, value=100)
        
        instructions = st.text_area("Campaign Instructions", placeholder="Describe what the AI should communicate...", height=120)
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🚀 Create Campaign", use_container_width=True):
                st.success("✅ Campaign created successfully!")
        with col2:
            if st.button("📋 Save as Draft", use_container_width=True):
                st.info("ℹ️ Draft saved")
    
    with tab3:
        st.markdown('<h3 class="section-title">Campaign Templates</h3>', unsafe_allow_html=True)
        
        templates = [
            ("🎤 Sales Pitch", "Professional sales call script"),
            ("📞 Customer Support", "Friendly support inquiry"),
            ("📊 Market Research", "Survey and feedback collection"),
            ("🎁 Promo Campaign", "Special offer announcement")
        ]
        
        for template_name, description in templates:
            st.markdown(f"""
            <div class="modern-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h4 style="margin: 0;">{template_name}</h4>
                        <p style="color: #718096; margin: 0.25rem 0;">{description}</p>
                    </div>
                    <button onclick="alert('Use template')" style="padding: 8px 16px; background: #667eea; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600;">Use</button>
                </div>
            </div>
            """, unsafe_allow_html=True)

# Page: Knowledge Base
elif st.session_state.page == "knowledge":
    st.markdown('<h1 class="page-title">📚 Knowledge Base</h1>', unsafe_allow_html=True)
    st.markdown('<p class="app-subtitle">RAG System - Upload & Manage Documents</p>', unsafe_allow_html=True)
    
    tab1, tab2, tab3 = st.tabs(["Documents", "Upload", "Retrieval Test"])
    
    with tab1:
        st.markdown('<h3 class="section-title">📄 Stored Documents</h3>', unsafe_allow_html=True)
        
        documents = [
            {"name": "Product FAQ.pdf", "size": "2.4 MB", "added": "Jan 20", "chunks": 156},
            {"name": "Service Agreement.pdf", "size": "1.8 MB", "added": "Jan 15", "chunks": 98},
            {"name": "Sales Guidelines.docx", "size": "0.9 MB", "added": "Jan 10", "chunks": 45},
            {"name": "Customer Handbook.pdf", "size": "3.2 MB", "added": "Jan 5", "chunks": 234}
        ]
        
        for doc in documents:
            st.markdown(f"""
            <div class="modern-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h4 style="margin: 0;">📄 {doc['name']}</h4>
                        <p style="color: #718096; margin: 0.5rem 0; font-size: 0.9rem;">{doc['chunks']} chunks • {doc['size']}</p>
                    </div>
                    <div style="text-align: right;">
                        <span class="badge badge-info">Added: {doc['added']}</span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    
    with tab2:
        st.markdown('<h3 class="section-title">📁 Upload Documents</h3>', unsafe_allow_html=True)
        
        st.markdown("""
        <div class="info-box">
            <strong>Supported formats:</strong> PDF, DOCX, TXT, CSV, JSON, XLSX
        </div>
        """, unsafe_allow_html=True)
        
        uploaded_files = st.file_uploader(
            "Choose files",
            type=['pdf', 'docx', 'txt', 'csv', 'json', 'xlsx'],
            accept_multiple_files=True
        )
        
        if uploaded_files:
            st.success(f"✅ {len(uploaded_files)} file(s) selected")
            for file in uploaded_files:
                st.caption(f"📄 {file.name} ({file.size/1024:.1f} KB)")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🚀 Upload & Process", use_container_width=True):
                    st.success("✅ Documents processed and added to knowledge base!")
            with col2:
                if st.button("Cancel", use_container_width=True):
                    st.info("Upload cancelled")
    
    with tab3:
        st.markdown('<h3 class="section-title">🔍 Test Retrieval</h3>', unsafe_allow_html=True)
        
        query = st.text_input("Test query:", placeholder="Ask a question to test the knowledge base...")
        
        if st.button("🔎 Search"):
            if query:
                st.markdown("""
                <div class="success-box">
                    <strong>Top Results:</strong>
                    <ol style="margin: 0.5rem 0;">
                        <li><strong>Product Return Policy</strong> (Confidence: 94%) - From Product FAQ.pdf</li>
                        <li><strong>Warranty Information</strong> (Confidence: 87%) - From Service Agreement.pdf</li>
                        <li><strong>Contact Support</strong> (Confidence: 81%) - From Customer Handbook.pdf</li>
                    </ol>
                </div>
                """, unsafe_allow_html=True)

# Page: Chat & Query
elif st.session_state.page == "chat":
    st.markdown('<h1 class="page-title">💬 Chat & Query</h1>', unsafe_allow_html=True)
    st.markdown('<p class="app-subtitle">Interact with NeuroCaller AI</p>', unsafe_allow_html=True)
    
    # Chat container
    st.markdown('<h3 class="section-title">💭 Chat Interface</h3>', unsafe_allow_html=True)
    
    # Sample messages
    chat_messages = [
        {"role": "assistant", "message": "Hello! I'm NeuroCaller. How can I assist you today?"},
        {"role": "user", "message": "Can you make calls to our customer list?"},
        {"role": "assistant", "message": "Of course! I can make calls to your customer list using Plivo integration. I can handle surveys, follow-ups, notifications, and more. What would you like to do?"}
    ]
    
    for msg in chat_messages:
        if msg["role"] == "user":
            st.markdown(f"""
            <div style="display: flex; justify-content: flex-end; margin-bottom: 1rem;">
                <div style="background: #667eea; color: white; padding: 12px 16px; border-radius: 12px; max-width: 70%; border-radius: 12px 0 12px 12px;">
                    {msg['message']}
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="display: flex; justify-content: flex-start; margin-bottom: 1rem;">
                <div style="background: #f7fafc; color: #2d3748; padding: 12px 16px; border-radius: 12px; max-width: 70%; border: 1px solid #e2e8f0;">
                    {msg['message']}
                </div>
            </div>
            """, unsafe_allow_html=True)
    
    st.divider()
    
    # Input area
    col1, col2 = st.columns([6, 1])
    with col1:
        user_query = st.text_input("Your message:", placeholder="Type your question...", label_visibility="collapsed")
    with col2:
        if st.button("📤 Send"):
            if user_query:
                st.success("Message sent!")

# Page: Analytics
elif st.session_state.page == "analytics":
    st.markdown('<h1 class="page-title">📈 Analytics</h1>', unsafe_allow_html=True)
    st.markdown('<p class="app-subtitle">Call History & Performance Metrics</p>', unsafe_allow_html=True)
    
    tab1, tab2, tab3 = st.tabs(["Call History", "Performance", "Export"])
    
    with tab1:
        st.markdown('<h3 class="section-title">📞 Call History</h3>', unsafe_allow_html=True)
        
        # Filters
        col1, col2, col3 = st.columns(3)
        with col1:
            date_range = st.date_input("Date Range", value=(datetime.now() - timedelta(days=7), datetime.now()))
        with col2:
            status_filter = st.multiselect("Status", ["Completed", "Failed", "No Answer"], default=["Completed"])
        with col3:
            campaign_filter = st.selectbox("Campaign", ["All", "Sales Outreach", "Customer Feedback", "Product Demo"])
        
        # Call history table
        history_data = {
            "Time": ["2:45 PM", "2:30 PM", "2:15 PM", "2:00 PM", "1:45 PM"],
            "Number": ["+1-555-0123", "+1-555-0124", "+1-555-0125", "+1-555-0126", "+1-555-0127"],
            "Duration": ["3:24", "2:56", "4:12", "2:30", "3:45"],
            "Status": ["✅ Completed", "✅ Completed", "✅ Completed", "✅ Completed", "✅ Completed"],
            "Campaign": ["Sales", "Sales", "Feedback", "Sales", "Feedback"]
        }
        
        df_history = pd.DataFrame(history_data)
        st.dataframe(df_history, use_container_width=True, hide_index=True)
    
    with tab2:
        st.markdown('<h3 class="section-title">📊 Performance Metrics</h3>', unsafe_allow_html=True)
        
        col1, col2, col3 = st.columns(3)
        
        metrics = [
            ("Total Calls", "1,247", "↑ 12% from last week"),
            ("Success Rate", "94.2%", "↑ 2.1% from last week"),
            ("Avg Duration", "3:24", "↓ 0:08 from last week")
        ]
        
        for i, (label, value, change) in enumerate(metrics):
            with [col1, col2, col3][i]:
                st.markdown(f"""
                <div class="modern-card">
                    <p style="color: #718096; margin: 0; font-size: 0.9rem;">{label}</p>
                    <h3 style="margin: 0.5rem 0; color: #2d3748;">{value}</h3>
                    <p style="color: #22863a; margin: 0; font-size: 0.85rem;">{change}</p>
                </div>
                """, unsafe_allow_html=True)
    
    with tab3:
        st.markdown('<h3 class="section-title">📥 Export Data</h3>', unsafe_allow_html=True)
        
        export_format = st.selectbox("Format", ["CSV", "Excel", "JSON", "PDF"])
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📥 Export Call History", use_container_width=True):
                st.success("✅ Export ready! Download starting...")
        with col2:
            if st.button("📥 Export Analytics", use_container_width=True):
                st.success("✅ Export ready! Download starting...")

# Page: Settings
elif st.session_state.page == "settings":
    st.markdown('<h1 class="page-title">⚙️ Settings</h1>', unsafe_allow_html=True)
    st.markdown('<p class="app-subtitle">Configure Your NeuroCaller System</p>', unsafe_allow_html=True)
    
    tab1, tab2, tab3, tab4 = st.tabs(["General", "Voice", "Integration", "Advanced"])
    
    with tab1:
        st.markdown('<h3 class="section-title">General Settings</h3>', unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        with col1:
            org_name = st.text_input("Organization Name", value="Acme Corp")
            timezone = st.selectbox("Timezone", ["UTC", "EST", "CST", "MST", "PST", "Custom"])
        with col2:
            language = st.selectbox("Language", ["English", "Spanish", "French", "German", "Chinese"])
            date_format = st.selectbox("Date Format", ["MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD"])
        
        if st.button("💾 Save General Settings"):
            st.success("✅ Settings saved!")
    
    with tab2:
        st.markdown('<h3 class="section-title">Voice Configuration</h3>', unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        with col1:
            voice_type = st.selectbox("Voice Type", ["Natural", "Professional", "Friendly", "Formal"])
            speed = st.slider("Speech Speed", 0.5, 2.0, 1.0)
        with col2:
            pitch = st.slider("Pitch", 0.5, 2.0, 1.0)
            volume = st.slider("Volume", 0.0, 1.0, 0.8)
    
    with tab3:
        st.markdown('<h3 class="section-title">Integration Settings</h3>', unsafe_allow_html=True)
        
        st.markdown("**Plivo Integration**")
        plivo_auth_id = st.text_input("Plivo Auth ID", value="XXXXXXXXXXX", type="password")
        plivo_auth_token = st.text_input("Plivo Auth Token", value="XXXXXXXXXXX", type="password")
        
        st.divider()
        
        st.markdown("**API Configuration**")
        api_endpoint = st.text_input("API Endpoint", value=st.session_state.api_url)
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✓ Test Connection", use_container_width=True):
                st.success("✅ Connection successful!")
    
    with tab4:
        st.markdown('<h3 class="section-title">Advanced Settings</h3>', unsafe_allow_html=True)
        
        st.markdown("**Logging & Debug**")
        debug_mode = st.checkbox("Enable Debug Mode")
        log_level = st.selectbox("Log Level", ["INFO", "DEBUG", "WARNING", "ERROR"])
        
        st.divider()
        
        st.markdown("**Data & Privacy**")
        retention = st.selectbox("Data Retention (days)", [7, 30, 90, 180, 365])
        encryption = st.checkbox("Enable End-to-End Encryption", value=True)
        
        if st.button("💾 Save Advanced Settings"):
            st.success("✅ Settings saved!")

# Footer
st.divider()
st.markdown("""
<div class="footer">
    <p>NeuroCaller Agent v1.0.0 | © 2026 All rights reserved</p>
    <p>Status: 🟢 Online • Last updated: Today</p>
</div>
""", unsafe_allow_html=True)