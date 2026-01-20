"""
Hardware Direct Theme Module
Reusable styling for Streamlit apps matching Hardware Direct branding

Usage:
    import streamlit as st
    from hd_theme import apply_hd_theme

    apply_hd_theme()
"""

import streamlit as st


def apply_hd_theme():
    """Apply Hardware Direct custom CSS styling to Streamlit app"""

    st.markdown("""
    <style>
        /* ========================================
           HARDWARE DIRECT CUSTOM THEME
           ======================================== */

        /* Orange Primary Buttons */
        .stButton>button {
            background-color: #F47920;
            color: white;
            border-radius: 8px;
            font-weight: 600;
            border: none;
            padding: 0.75rem 1.5rem;
            transition: all 0.3s ease;
            box-shadow: 0 2px 4px rgba(244, 121, 32, 0.2);
        }

        .stButton>button:hover {
            background-color: #D86A1A;
            box-shadow: 0 4px 8px rgba(244, 121, 32, 0.3);
            transform: translateY(-1px);
        }

        .stButton>button:disabled {
            background-color: #CCCCCC;
            color: #666666;
            cursor: not-allowed;
            box-shadow: none;
        }

        /* Dark Section Cards */
        .dark-card {
            background: linear-gradient(135deg, #2B2B2B 0%, #1A1A1A 100%);
            padding: 2rem;
            border-radius: 12px;
            color: white;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            margin: 1rem 0;
        }

        /* Orange Accent Cards */
        .orange-card {
            background: linear-gradient(135deg, #F47920 0%, #D86A1A 100%);
            padding: 1.5rem;
            border-radius: 12px;
            color: white;
            box-shadow: 0 4px 12px rgba(244, 121, 32, 0.3);
            margin: 1rem 0;
        }

        /* Metric Cards */
        .metric-card {
            background: white;
            padding: 1.5rem;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            border-left: 4px solid #F47920;
            margin: 0.5rem 0;
        }

        .metric-card h3 {
            color: #2B2B2B;
            font-size: 2rem;
            margin: 0;
            font-weight: 700;
        }

        .metric-card p {
            color: #666;
            margin: 0.5rem 0 0 0;
            font-size: 0.9rem;
        }

        /* Headers with Orange Accent */
        h1 {
            color: #2B2B2B;
            font-weight: 700;
        }

        h2 {
            color: #2B2B2B;
            font-weight: 600;
            border-bottom: 3px solid #F47920;
            padding-bottom: 0.5rem;
            margin-top: 2rem;
        }

        h3 {
            color: #2B2B2B;
            font-weight: 600;
        }

        /* Sidebar Styling */
        [data-testid="stSidebar"] {
            background-color: #2B2B2B;
        }

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] label {
            color: white;
        }

        [data-testid="stSidebar"] .stButton>button {
            width: 100%;
        }

        /* Data Tables */
        .dataframe {
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        }

        /* Input Fields */
        .stTextInput>div>div>input,
        .stSelectbox>div>div>select,
        .stDateInput>div>div>input {
            border-radius: 6px;
            border: 2px solid #E0E0E0;
            transition: border-color 0.3s ease;
        }

        .stTextInput>div>div>input:focus,
        .stSelectbox>div>div>select:focus,
        .stDateInput>div>div>input:focus {
            border-color: #F47920;
            box-shadow: 0 0 0 2px rgba(244, 121, 32, 0.1);
        }

        /* Success/Error Messages */
        .stSuccess {
            background-color: #D4EDDA;
            border-left: 4px solid #28A745;
            border-radius: 6px;
        }

        .stError {
            background-color: #F8D7DA;
            border-left: 4px solid #DC3545;
            border-radius: 6px;
        }

        .stWarning {
            background-color: #FFF3CD;
            border-left: 4px solid #FFC107;
            border-radius: 6px;
        }

        .stInfo {
            background-color: #D1ECF1;
            border-left: 4px solid #F47920;
            border-radius: 6px;
        }

        /* Expander Styling */
        .streamlit-expanderHeader {
            background-color: #F8F9FA;
            border-radius: 8px;
            font-weight: 600;
        }

        /* File Uploader */
        [data-testid="stFileUploader"] {
            border: 2px dashed #F47920;
            border-radius: 12px;
            padding: 2rem;
            background-color: #FFF8F4;
        }

        /* Orange Accent Text Class */
        .orange-text {
            color: #F47920;
            font-weight: 600;
        }

        /* Badge Styles */
        .badge {
            display: inline-block;
            padding: 0.35rem 0.75rem;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
            margin: 0.25rem;
        }

        .badge-success {
            background-color: #28A745;
            color: white;
        }

        .badge-warning {
            background-color: #FFC107;
            color: #2B2B2B;
        }

        .badge-danger {
            background-color: #DC3545;
            color: white;
        }

        .badge-orange {
            background-color: #F47920;
            color: white;
        }
    </style>
    """, unsafe_allow_html=True)


def metric_card(title, value, subtitle=""):
    """Create a styled metric card

    Args:
        title: The metric label
        value: The metric value
        subtitle: Optional subtitle text
    """
    st.markdown(f"""
    <div class="metric-card">
        <h3>{value}</h3>
        <p><strong>{title}</strong></p>
        {f'<p style="font-size: 0.8rem; color: #999;">{subtitle}</p>' if subtitle else ''}
    </div>
    """, unsafe_allow_html=True)


def dark_card(content):
    """Create a dark-themed card section

    Args:
        content: HTML content to display in the card
    """
    st.markdown(f"""
    <div class="dark-card">
        {content}
    </div>
    """, unsafe_allow_html=True)


def orange_card(content):
    """Create an orange-themed card section

    Args:
        content: HTML content to display in the card
    """
    st.markdown(f"""
    <div class="orange-card">
        {content}
    </div>
    """, unsafe_allow_html=True)


def badge(text, style="orange"):
    """Create a badge element

    Args:
        text: Badge text
        style: Badge style - 'success', 'warning', 'danger', or 'orange' (default)
    """
    return f'<span class="badge badge-{style}">{text}</span>'


def add_logo(logo_path=None, text="Hardware Direct", subtitle="ProMaster Importer"):
    """Add a logo to the sidebar or main area

    Args:
        logo_path: Path to logo image file (optional). If None, uses text-based logo
        text: Main logo text
        subtitle: Subtitle text below logo
    """
    if logo_path:
        st.sidebar.image(logo_path, use_container_width=True)
    else:
        # Text-based logo matching Hardware Direct style
        st.sidebar.markdown(f"""
        <div style="padding: 1.5rem 0; text-align: center;">
            <div style="display: inline-flex; align-items: center; gap: 0.5rem;">
                <div style="
                    width: 12px;
                    height: 12px;
                    background: #F47920;
                    border-radius: 50%;
                "></div>
                <h2 style="
                    margin: 0;
                    color: white;
                    font-size: 1.5rem;
                    font-weight: 700;
                    letter-spacing: -0.5px;
                ">{text}</h2>
            </div>
            <p style="
                margin: 0.5rem 0 0 0;
                color: #999;
                font-size: 0.85rem;
                font-weight: 400;
            ">{subtitle}</p>
        </div>
        <hr style="border-color: #444; margin: 1rem 0;">
        """, unsafe_allow_html=True)
