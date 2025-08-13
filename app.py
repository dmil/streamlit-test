#!#!/usr/bin/env python3
"""
Public-facing Streamlit app to display campus announcements from MongoDB.
Password protected with announcements-only view.
"""

import os
import hashlib
from datetime import datetime, timezone
import streamlit as st
from pymongo import MongoClient
import pandas as pd
import io
import pytz
from tzlocal import get_localzone
import json

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(override=True)

# Configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "campus_data")
# Password for public access (store as environment variable)
PUBLIC_PASSWORD = os.environ.get("PUBLIC_PASSWORD", "campus2025")

# Define the start date for filtering announcements
start_date = datetime(2025, 1, 1, tzinfo=timezone.utc)

# Licensing information template (you can customize this)
LICENSING_INFO = """
# Data Licensing Information

[YOUR LICENSING INFORMATION GOES HERE]

This CSV file contains campus announcement data collected from publicly available university websites.

Data Collection Period: January 1, 2025 onwards
Generated on: {generation_date}
Total Records: {record_count}

For questions about data usage and licensing, please contact: [YOUR CONTACT INFO]
"""

def check_password():
    """Returns `True` if the user entered the correct password."""
    
    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == PUBLIC_PASSWORD:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't store password
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # First run, show input for password
        st.markdown("## 🔐 Access Required")
        st.markdown("Please enter the password to access the Campus Announcements database:")
        st.text_input(
            "Password", 
            type="password", 
            on_change=password_entered, 
            key="password",
            help="Contact the administrator if you need access"
        )
        st.markdown("---")
        st.markdown("*This is a research database containing campus announcements from university websites.*")
        return False
    elif not st.session_state["password_correct"]:
        # Password not correct, show input + error
        st.markdown("## 🔐 Access Required")
        st.text_input(
            "Password", 
            type="password", 
            on_change=password_entered, 
            key="password"
        )
        st.error("😞 Password incorrect. Please try again.")
        return False
    else:
        # Password correct
        return True

def utc_to_local(utc_dt):
    """Function to convert UTC datetime to local time"""
    if utc_dt is None:
        return None
    if not isinstance(utc_dt, datetime):
        return utc_dt
    
    # If datetime doesn't have tzinfo, assume it's UTC
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    
    # Convert to local timezone
    local_tz = get_localzone()
    local_dt = utc_dt.astimezone(local_tz)
    return local_dt

@st.cache_resource
def get_db():
    """Connect to MongoDB with connection pooling"""
    client = MongoClient(MONGO_URI, maxPoolSize=50, minPoolSize=5, maxIdleTimeMS=30000)
    return client[DB_NAME]

@st.cache_data(ttl=300)
def get_filtered_count(mongo_uri, db_name, query_str):
    """Get count of documents matching query - cached for performance"""
    client = MongoClient(mongo_uri)
    db = client[db_name]
    query = json.loads(query_str)
    return db.articles.count_documents(query)

@st.cache_data(ttl=300)
def get_organizations_data(mongo_uri, db_name):
    """Get all organizations data - cached for performance"""
    client = MongoClient(mongo_uri)
    db = client[db_name]
    orgs_cursor = db.orgs.find({}, {"name": 1, "color": 1, "scrapers": 1})
    return list(orgs_cursor)

@st.cache_data(ttl=300)
def get_scraper_mapping(organizations_data):
    """Create mapping from scraper path to scraper info - cached"""
    scraper_mapping = {}
    scraper_types = set()
    
    for org in organizations_data:
        org_name = org.get("name", "Unknown School")
        org_color = org.get("color", "#000000")
        scrapers = org.get("scrapers", [])
        
        for scraper in scrapers:
            path = scraper.get("path", "")
            name = scraper.get("name", "")
            
            if path:
                scraper_mapping[path] = {
                    "name": name,
                    "org_name": org_name,
                    "org_color": org_color
                }
            
            if name:
                scraper_types.add(name)
    
    return scraper_mapping, sorted(list(scraper_types))

def get_scraper_paths_by_type(organizations_data, scraper_type):
    """Get all scraper paths that match a specific type name"""
    matching_paths = []
    for org in organizations_data:
        scrapers = org.get("scrapers", [])
        for scraper in scrapers:
            if scraper.get("name") == scraper_type:
                path = scraper.get("path")
                if path:
                    matching_paths.append(path)
    return matching_paths

def display_announcements(db):
    """Display the announcements view with optimized pagination."""
    st.markdown('⚠️ Please note that this is an unedited **first draft** proof-of-concept. Classifications **WILL BE** inaccurate. ⚠️', unsafe_allow_html=True)

    # Get cached organizations data once
    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)
    scraper_mapping, scraper_types = get_scraper_mapping(organizations_data)
    
    # Extract school names from organizations data
    school_names = sorted([org["name"] for org in organizations_data])

    st.markdown('>_Check any box to filter for items identified by our LLM as related to that category.<br/>Hover on each question mark for more information about the criteria._', unsafe_allow_html=True)

    # Create a columns layout for the checkboxes
    col1, col2, col3 = st.columns(3)

    with col1:
        show_govt_related = st.checkbox("👨‍⚖️ Government Related", 
            key="show_govt_related_ann",
            help="LLM Prompt: Items where the university is responding to federal government or administration actions")
        show_lawsuit_related = st.checkbox("⚖️ Lawsuit Related", 
            key="show_lawsuit_related_ann",
            help="LLM Prompt: Items mentioning lawsuits or legal actions related to the university")
        show_funding_related = st.checkbox("💰 Funding Related", 
            key="show_funding_related_ann",
            help="LLM Prompt: Items discussing funding cuts or financial issues")
        show_protest_related = st.checkbox("🪧 Protest Related", 
            key="show_protest_related_ann",
            help="LLM Prompt: Items mentioning campus protests or disruptions")

    with col2:
        show_layoff_related = st.checkbox("✂️ Layoff Related", 
            key="show_layoff_related_ann",
            help="LLM Prompt: Items discussing layoffs, job cuts, staff reductions, or employment terminations")
        show_president_related = st.checkbox("🎓 President Related", 
            key="show_president_related_ann",
            help="LLM Prompt: Items related to the president of the school")
        show_provost_related = st.checkbox("📚 Provost Related", 
            key="show_provost_related_ann",
            help="LLM Prompt: Items related to the provost of the school")
        show_faculty_related = st.checkbox("👨‍🏫 Faculty Related", 
            key="show_faculty_related_ann",
            help="LLM Prompt: Items related to faculty members, faculty actions, or faculty governance")
    
    with col3:
        show_trustees_related = st.checkbox("🏛️ Trustees Related", 
            key="show_trustees_related_ann",
            help="LLM Prompt: Items related to the board of trustees or trustee actions")
        show_trump_related = st.checkbox("🇺🇸 Trump Related", 
            key="show_trump_related_ann",
            help="LLM Prompt: Items related to Donald Trump (mentions, policies, reactions to Trump, etc.)")
    
    # Add a text search bar for content search
    search_term = st.text_input("🔍 Search announcement content", value="", key="search_term", help="Enter keywords to search announcement content (case-insensitive)")

    # Create two columns for filters
    filter_col1, filter_col2 = st.columns(2)
    
    with filter_col1:
        # Create a dropdown for school selection with alphabetically sorted options
        school_options = ["All"] + school_names
        selected_school = st.selectbox("Filter by School", school_options)
    
    with filter_col2:
        # Create a dropdown for scraper type selection
        scraper_type_options = ["All"] + scraper_types
        selected_scraper_type = st.selectbox("Filter by Announcement Type", scraper_type_options, help="Filter by the type of announcements (e.g., provost, president, etc.)")

    # Build the query based on the selected school
    query = {}
    if selected_school != "All":
        query["org"] = selected_school

    # Add scraper type filter to query
    if selected_scraper_type != "All":
        # Get all scraper paths that match the selected type
        matching_paths = get_scraper_paths_by_type(organizations_data, selected_scraper_type)
        if matching_paths:
            query["scraper"] = {"$in": matching_paths}

    # Add filters based on selected categories
    filter_conditions = []
    if show_govt_related:
        filter_conditions.append({"llm_response.government_related.related": True})
    if show_lawsuit_related:
        filter_conditions.append({"llm_response.lawsuit_related.related": True})
    if show_funding_related:
        filter_conditions.append({"llm_response.funding_related.related": True})
    if show_protest_related:
        filter_conditions.append({"llm_response.protest_related.related": True})
    if show_layoff_related:
        filter_conditions.append({"llm_response.layoff_related.related": True})
    if show_president_related:
        filter_conditions.append({"llm_response.president_related.related": True})
    if show_provost_related:
        filter_conditions.append({"llm_response.provost_related.related": True})
    if show_faculty_related:
        filter_conditions.append({"llm_response.faculty_related.related": True})
    if show_trustees_related:
        filter_conditions.append({"llm_response.trustees_related.related": True})
    if show_trump_related:
        filter_conditions.append({"llm_response.trump_related.related": True})

    # Combine filters with OR if any are selected
    if filter_conditions:
        query["$or"] = filter_conditions

    # Add date filter for announcements after the start date
    query["date"] = {"$gte": start_date}

    # Add content search filter if search_term is provided
    if search_term.strip():
        query["content"] = {"$regex": search_term, "$options": "i"}

    # Use cached count query for performance
    query_str = json.dumps(query, default=str)
    num_announcements = get_filtered_count(MONGO_URI, DB_NAME, query_str)

    st.write(f"Number of announcements: **{num_announcements}** (from {start_date.strftime('%B %d, %Y')} onwards)")
    
    # Pagination logic - optimized
    PAGE_SIZE = 20
    total_pages = max((num_announcements - 1) // PAGE_SIZE + 1, 1)
    
    # Initialize pagination state
    if "ann_page" not in st.session_state:
        st.session_state["ann_page"] = 0
    
    # Reset to page 0 when filters change
    filter_state_key = f"{selected_school}_{selected_scraper_type}_{show_govt_related}_{show_lawsuit_related}_{show_funding_related}_{show_protest_related}_{show_layoff_related}_{show_president_related}_{show_provost_related}_{show_faculty_related}_{show_trustees_related}_{show_trump_related}_{search_term}"
    if "last_filter_state" not in st.session_state:
        st.session_state["last_filter_state"] = filter_state_key
    elif st.session_state["last_filter_state"] != filter_state_key:
        st.session_state["ann_page"] = 0
        st.session_state["last_filter_state"] = filter_state_key
    
    # Clamp page number if needed
    st.session_state["ann_page"] = min(st.session_state["ann_page"], total_pages - 1)
    
    col_download, col_clear = st.columns([1, 3])
    
    # Add download button for CSV
    with col_download:
        if num_announcements > 0:
            if st.button("📥 Generate CSV", help="Click to generate and download CSV (may take a moment for large datasets)"):
                with st.spinner("Generating CSV file..."):
                    all_cursor = db.articles.find(query, {"_id": 0}).sort("date", -1)
                    all_announcements = list(all_cursor)
                    csv_with_license = convert_to_csv_with_license(all_announcements, scraper_mapping)
                    st.download_button(
                        label="📥 Download CSV",
                        data=csv_with_license,
                        file_name="campus_announcements_data.csv",
                        mime="text/csv",
                    )

    with col_clear:
        if st.button("🗑️ Clear All Filters", help="Reset all category filters"):
            st.components.v1.html("""
                <script>
                    window.parent.location.reload();
                </script>
            """, height=0)

    # Only fetch current page data for performance
    start_idx = st.session_state["ann_page"] * PAGE_SIZE
    
    # Use MongoDB skip() and limit() for true pagination
    cursor = db.articles.find(query, {"_id": 0}).sort("date", -1).skip(start_idx).limit(PAGE_SIZE)
    paged_announcements = list(cursor)

    # Display announcements for current page
    for ann in paged_announcements:
        title = ann.get("title", "No Title")

        # Convert UTC date to local time and format it
        date_value = ann.get("date")
        if isinstance(date_value, datetime):
            local_date = utc_to_local(date_value)
            date_str = local_date.strftime("%Y-%m-%d %I:%M:%S %p")
        else:
            date_str = str(date_value)

        # Get school info and scraper type using cached mapping
        scraper_path = ann.get("scraper", "")
        school_name = ann.get("org", "Unknown School")
        school_color = "#000000"
        scraper_type_display = "Unknown Type"
        
        if scraper_path in scraper_mapping:
            scraper_info = scraper_mapping[scraper_path]
            scraper_type_display = scraper_info["name"]
            school_color = scraper_info["org_color"]

        # Announcement URL
        url = ann.get("url", "")

        st.subheader(title)
        content = ann.get("content", "")
        announcement_html = f"""
        <p style="margin-bottom: 0.5em;">
            <strong>School:</strong> <span style="background-color:{school_color}; padding:2px 4px; border-radius:4px; color:#ffffff;">{school_name}</span><br>
            <strong>Type:</strong> {scraper_type_display}<br>
            <strong>Date:</strong> {date_str}<br>
            <strong>Content Scraped:</strong> {'✅' if content else '👎'}<br>
            <strong>Announcement URL:</strong><br/> <a href="{url}">{url}</a>
        </p>
        """
        st.markdown(announcement_html, unsafe_allow_html=True)
        
        # Show search snippet if search term is provided and content exists
        if search_term.strip() and content:
            import re
            search_pattern = re.compile(re.escape(search_term), re.IGNORECASE)
            matches = list(search_pattern.finditer(content))
            
            if matches:
                snippets = []
                for i, match in enumerate(matches):
                    start_pos = max(0, match.start() - 100)
                    end_pos = min(len(content), match.end() + 100)
                    snippet = content[start_pos:end_pos]
                    
                    if start_pos > 0:
                        snippet = "..." + snippet
                    if end_pos < len(content):
                        snippet = snippet + "..."
                    
                    highlighted_snippet = search_pattern.sub(f"<mark style='background-color: yellow; padding: 2px;'>{search_term}</mark>", snippet)
                    snippets.append(highlighted_snippet)
                
                match_count = len(matches)
                match_text = "match" if match_count == 1 else "matches"
                
                snippets_html = "<br/><br/>".join([f"<strong>Match {i+1}:</strong><br/><em>{snippet}</em>" for i, snippet in enumerate(snippets)])
                
                st.markdown(f"""
                <div style="background-color: #f0f2f6; padding: 10px; border-radius: 5px; margin: 5px 0; border-left: 4px solid #ff6b6b;">
                    <strong>🔍 Search Results ({match_count} {match_text}):</strong><br/>
                    {snippets_html}
                </div>
                """, unsafe_allow_html=True)

        # LLM Response Section - only show selected categories
        if ann.get("llm_response"):
            llm_response = ann.get("llm_response")
            categories_found = []

            if show_govt_related and llm_response.get("government_related", {}).get("related"):
                categories_found.append(("👨‍⚖️ Government", llm_response["government_related"].get("reason", "")))

            if show_lawsuit_related and llm_response.get("lawsuit_related", {}).get("related"):
                categories_found.append(("⚖️ Lawsuit", llm_response["lawsuit_related"].get("reason", "")))

            if show_funding_related and llm_response.get("funding_related", {}).get("related"):
                categories_found.append(("💰 Funding", llm_response["funding_related"].get("reason", "")))

            if show_protest_related and llm_response.get("protest_related", {}).get("related"):
                categories_found.append(("🪧 Protest", llm_response["protest_related"].get("reason", "")))

            if show_layoff_related and llm_response.get("layoff_related", {}).get("related"):
                categories_found.append(("✂️ Layoffs", llm_response["layoff_related"].get("reason", "")))

            if show_president_related and llm_response.get("president_related", {}).get("related"):
                categories_found.append(("🎓 President", llm_response["president_related"].get("reason", "")))

            if show_provost_related and llm_response.get("provost_related", {}).get("related"):
                categories_found.append(("📚 Provost", llm_response["provost_related"].get("reason", "")))

            if show_faculty_related and llm_response.get("faculty_related", {}).get("related"):
                categories_found.append(("👨‍🏫 Faculty", llm_response["faculty_related"].get("reason", "")))

            if show_trustees_related and llm_response.get("trustees_related", {}).get("related"):
                categories_found.append(("🏛️ Trustees", llm_response["trustees_related"].get("reason", "")))

            if show_trump_related and llm_response.get("trump_related", {}).get("related"):
                categories_found.append(("🇺🇸 Trump", llm_response["trump_related"].get("reason", "")))

            # Display only selected categories that are found
            for category, reason in categories_found:
                st.markdown(f"🤖 **AI Classification ({category}):** {reason}")

        st.markdown("<hr style=\"margin-top:0.5em;margin-bottom:0.5em;\">", unsafe_allow_html=True)

    # Optimized pagination controls
    col_prev, col_page, col_next = st.columns([1,2,1])
    with col_prev:
        if st.button("⬅️ Prev", key="ann_prev", disabled=st.session_state["ann_page"] == 0):
            st.session_state["ann_page"] = max(st.session_state["ann_page"] - 1, 0)
            st.rerun()
    with col_page:
        st.markdown(f"<div style='text-align:center;'>Page <b>{st.session_state['ann_page']+1}</b> of <b>{total_pages}</b></div>", unsafe_allow_html=True)
    with col_next:
        if st.button("Next ➡️", key="ann_next", disabled=st.session_state["ann_page"] >= total_pages - 1):
            st.session_state["ann_page"] = min(st.session_state["ann_page"] + 1, total_pages - 1)
            st.rerun()

def convert_to_csv_with_license(announcements, scraper_mapping):
    """Convert announcements data to CSV format with licensing header."""
    processed_data = []
    
    for ann in announcements:
        processed_ann = {
            "title": ann.get("title", ""),
            "school": ann.get("org", ""),
            "date": ann.get("date"),
            "url": ann.get("url", ""),
        }
        
        scraper_path = ann.get("scraper", "")
        scraper_type = "Unknown Type"
        if scraper_path in scraper_mapping:
            scraper_type = scraper_mapping[scraper_path]["name"]
        
        processed_ann["announcement_type"] = scraper_type
        
        llm_response = ann.get("llm_response", {})
        
        classification_fields = [
            "government_related", "government_supportive", "government_opposing",
            "lawsuit_related", "funding_related", "protest_related", "layoff_related",
            "president_related", "provost_related", "faculty_related", "trustees_related", "trump_related"
        ]
        
        for field_name in classification_fields:
            field_data = llm_response.get(field_name, {})
            processed_ann[f"{field_name}"] = field_data.get("related", False)
            processed_ann[f"{field_name}_reason"] = field_data.get("reason", "") if field_data.get("related") else ""
        
        processed_data.append(processed_ann)
    
    df = pd.DataFrame(processed_data)
    
    if 'date' in df.columns:
        df['date'] = df['date'].apply(lambda x: utc_to_local(x).strftime('%Y-%m-%d %I:%M:%S %p') if isinstance(x, datetime) else str(x))
    
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    csv_content = csv_buffer.getvalue()
    
    generation_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record_count = len(processed_data)
    
    license_header = LICENSING_INFO.format(
        generation_date=generation_date,
        record_count=record_count
    )
    
    license_lines = [f"# {line}" for line in license_header.strip().split('\n')]
    license_header_csv = '\n'.join(license_lines) + '\n\n'
    
    return license_header_csv + csv_content

def main():
    """Main function"""
    st.set_page_config(
        page_title="Campus Announcements Tracker",
        page_icon="🎓",
        layout="centered",
        initial_sidebar_state="collapsed",
        menu_items={
            "About": "Campus Announcements Tracker - A research database of university announcements."
        }
    )
    
    if not check_password():
        return
    
    st.title("🎓 Campus Announcements")
    st.markdown("Announcements from the provosts' and presidents' offices at select universities.")
    
    with st.sidebar:
        st.markdown("### Navigation")
        if st.button("🔓 Logout"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
        
        st.markdown("---")
        st.markdown("### About")
        st.markdown("This database contains campus announcements collected from publicly available university websites.")
        st.markdown(f"**Data from:** {start_date.strftime('%B %d, %Y')} onwards")
        st.markdown("**Classifications:** AI-generated (may contain inaccuracies)")

    db = get_db()
    display_announcements(db)
    
    st.markdown("---")
    st.markdown("*This is a research tool. Classifications are AI-generated and may contain inaccuracies.*")

if __name__ == "__main__":
    main()