# Hardware Direct Theme Guide

This app uses a custom Hardware Direct branded theme that can be reused across other Streamlit projects.

## 🎨 Theme Files

- **`.streamlit/config.toml`** - Color scheme configuration
- **`hd_theme.py`** - Reusable theme module with custom CSS and components

## 🚀 Quick Start

### Apply to This Project
Theme is already applied! Just run:
```bash
streamlit run app.py
```

### Apply to Other Streamlit Projects

1. Copy these files to your project:
   ```
   .streamlit/config.toml
   hd_theme.py
   ```

2. Import and apply in your app:
   ```python
   from hd_theme import apply_hd_theme, add_logo

   st.set_page_config(page_title="Your App", layout="wide")
   apply_hd_theme()
   add_logo(text="HD", subtitle="Your App Name")
   ```

## 🖼️ Adding a Custom Logo

### Option 1: Use Text-based Logo (Current)
```python
add_logo(text="HD", subtitle="ProMaster → Cin7 Importer")
```

### Option 2: Use Image Logo
1. Add your logo file to the project (e.g., `logo.png`)
2. Update the code:
   ```python
   add_logo(logo_path="logo.png", subtitle="ProMaster → Cin7 Importer")
   ```

### Option 3: Use Hardware Direct Logo
To use the official Hardware Direct logo:
1. Download the logo from https://www.hardwaredirect.co.nz/ (with permission)
2. Save as `hd_logo.png` in your project
3. Update the code:
   ```python
   add_logo(logo_path="hd_logo.png", subtitle="ProMaster → Cin7 Importer")
   ```

## 🎨 Available Components

### Metric Cards
```python
from hd_theme import metric_card

col1, col2, col3 = st.columns(3)
with col1:
    metric_card("Total Orders", "1,234", "Last 30 days")
```

### Dark/Orange Cards
```python
from hd_theme import dark_card, orange_card

dark_card("<h3>Important Info</h3><p>Details here...</p>")
orange_card("<h3>Call to Action</h3><p>Click here!</p>")
```

### Badges
```python
from hd_theme import badge

st.markdown(f"Status: {badge('Active', 'success')}", unsafe_allow_html=True)
st.markdown(f"Priority: {badge('High', 'danger')}", unsafe_allow_html=True)
```

## 🎨 Color Palette

- **Primary Orange:** `#F47920`
- **Dark Background:** `#2B2B2B`
- **Black:** `#1A1A1A`
- **White:** `#FFFFFF`
- **Light Gray:** `#F8F9FA`
- **Text:** `#2B2B2B`

## 📝 Customization

### Change Colors
Edit `.streamlit/config.toml`:
```toml
[theme]
primaryColor = "#F47920"  # Change this
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F8F9FA"
textColor = "#2B2B2B"
```

### Modify Styles
Edit `hd_theme.py` in the `apply_hd_theme()` function to adjust:
- Button styles
- Card designs
- Typography
- Spacing
- Animations

## 🔧 Troubleshooting

**Logo not showing?**
- Make sure image file exists in project root
- Check file path is correct
- Try using absolute path: `logo_path="C:/path/to/logo.png"`

**Colors not applying?**
- Restart Streamlit after changing `config.toml`
- Clear browser cache
- Check console for errors

**Sidebar dark but text not white?**
- Make sure `apply_hd_theme()` is called after `st.set_page_config()`
- Check browser dev tools for CSS conflicts

## 📦 Dependencies

No extra dependencies needed! Theme uses only:
- Streamlit (already installed)
- Standard Python libraries

## 🎯 Examples

Check `app.py` for working examples of:
- ✅ Logo implementation
- ✅ Metric cards
- ✅ Themed buttons
- ✅ Styled dataframes
- ✅ Custom layouts
