#!/usr/bin/env python3
"""
Simple Streamlit front-end to display announcements from MongoDB.
Shows title, school, date, URL, and source base URL for each announcement.
OPTIMIZED VERSION with scraper type filtering and performance improvements.
"""

import os
import requests
from datetime import datetime, timezone, timedelta
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
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# Define the start date for filtering announcements
start_date = datetime(2025, 1, 1, tzinfo=timezone.utc)

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
    """Connect to MongoDB with enhanced connection pooling"""
    client = MongoClient(
        MONGO_URI, 
        maxPoolSize=50, 
        minPoolSize=5, 
        maxIdleTimeMS=30000,
        serverSelectionTimeoutMS=5000,  # Faster timeout
        connectTimeoutMS=5000,
        socketTimeoutMS=5000
    )
    return client[DB_NAME]

# PERFORMANCE OPTIMIZATION: Cache count queries for 60 seconds
@st.cache_data(ttl=60)
def get_filtered_count_cached(query_str):
    """Get count of documents matching query - cached for performance"""
    try:
        db = get_db()
        query = eval(query_str)  # Convert string back to dict for caching
        return db.articles.count_documents(query)
    except Exception as e:
        st.error(f"Error counting documents: {e}")
        return 0

def get_filtered_count(db, query):
    """Get count wrapper that uses caching"""
    # Convert query dict to string for caching key
    query_str = str(query)
    return get_filtered_count_cached(query_str)

@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_organizations_data(mongo_uri, db_name):
    """Get all organizations data - cached for performance"""
    client = MongoClient(mongo_uri)
    db = client[db_name]
    orgs_cursor = db.orgs.find({}, {"name": 1, "color": 1, "scrapers": 1})
    return list(orgs_cursor)

@st.cache_data(ttl=300)  # Cache for 5 minutes
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

def send_slack_notification(failed_scrapers, is_daily_report=False):
    """Send Slack notification for completely failed scrapers"""
    if not SLACK_WEBHOOK_URL or not failed_scrapers:
        return False
    
    failed_count = len(failed_scrapers)
    
    # Build the message
    if is_daily_report:
        message = f"📊 *Daily Scraper Report: {failed_count} Failed*\n\n"
        username = "Campus Scraper Daily Report"
    else:
        message = f"🚨 *{failed_count} Campus Scraper{'s' if failed_count != 1 else ''} Failed*\n\n"
        username = "Campus Scraper Monitor"
    
    for scraper in failed_scrapers:
        school = scraper['School']
        name = scraper['Name']
        reason = scraper['Health Reason']
        url = scraper['URL']
        
        message += f"• *{school}* - {name}\n"
        message += f"  ❌ {reason}\n"
        message += f"  🔗 <{url}|View Source>\n\n"
    
    message += f"Check the dashboard: https://campusdata.onrender.com/"
    
    payload = {
        "text": message,
        "username": username,
        "icon_emoji": ":warning:" if not is_daily_report else ":clipboard:"
    }
    
    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error sending Slack notification: {e}")
        return False

def check_and_send_daily_report(db, organizations_data):
    """Check for failed scrapers and send daily report if needed"""
    # Check if we've already sent a report today
    today = datetime.now(timezone.utc).date()
    
    # Try to get the last report date from a tracking collection
    try:
        last_report = db.slack_reports.find_one({"type": "daily_scraper_report"})
        if last_report and last_report.get("date"):
            last_report_date = last_report["date"]
            if isinstance(last_report_date, datetime):
                last_report_date = last_report_date.date()
            elif isinstance(last_report_date, str):
                last_report_date = datetime.fromisoformat(last_report_date).date()
            
            # If we already sent a report today, skip
            if last_report_date >= today:
                return False, "Already sent today"
    except Exception as e:
        print(f"Error checking last report date: {e}")
    
    # Get failed scrapers using optimized data
    failed_scrapers = []
    
    for org in organizations_data:
        school_name = org.get("name", "Unknown School")
        scrapers = org.get("scrapers", [])
        
        for scraper in scrapers:
            last_run = scraper.get("last_run")
            
            # Same simplified health check logic
            health_status = "❌ Unhealthy"
            health_reason = "No recent activity"
            
            if last_run and isinstance(last_run, datetime):
                current_time = datetime.now(timezone.utc)
                
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                
                hours_since_run = (current_time - last_run).total_seconds() / 3600
                
                if hours_since_run <= 24:
                    health_status = "✅ Healthy"
                    health_reason = "Running normally"
                else:
                    health_status = "❌ Unhealthy"
                    health_reason = f"Last run {int(hours_since_run)}h ago"
            else:
                health_status = "❌ Unhealthy"
                health_reason = "No run data available"
            
            # Add to failed list if unhealthy
            if health_status == "❌ Unhealthy":
                failed_scrapers.append({
                    "School": school_name,
                    "Name": scraper.get("name", "").replace(" announcements", ""),
                    "Health Reason": health_reason,
                    "URL": scraper.get("url", "No URL")
                })
    
    # Send notification if there are failures
    if failed_scrapers:
        success = send_slack_notification(failed_scrapers, is_daily_report=True)
        if success:
            # Record that we sent a report today
            try:
                db.slack_reports.replace_one(
                    {"type": "daily_scraper_report"},
                    {
                        "type": "daily_scraper_report",
                        "date": datetime.now(timezone.utc),
                        "failed_count": len(failed_scrapers),
                        "scrapers": failed_scrapers
                    },
                    upsert=True
                )
            except Exception as e:
                print(f"Error recording report date: {e}")
            
            return True, f"Sent daily report for {len(failed_scrapers)} failed scrapers"
        else:
            return False, "Failed to send daily report"
    else:
        # Record that we checked today (even with no failures)
        try:
            db.slack_reports.replace_one(
                {"type": "daily_scraper_report"},
                {
                    "type": "daily_scraper_report", 
                    "date": datetime.now(timezone.utc),
                    "failed_count": 0,
                    "scrapers": []
                },
                upsert=True
            )
        except Exception as e:
            print(f"Error recording report date: {e}")
        
        return True, "All scrapers healthy - no notification needed"

# PERFORMANCE OPTIMIZATION: Cache paginated data
@st.cache_data(ttl=120)  # Cache for 2 minutes
def get_paginated_announcements(query_str, page, page_size):
    """Get paginated announcements with caching"""
    try:
        db = get_db()
        query = eval(query_str)  # Convert string back to dict
        start_idx = page * page_size
        
        # Use MongoDB projection to only fetch needed fields for better performance
        projection = {
            "_id": 0,
            "title": 1,
            "org": 1, 
            "date": 1,
            "scraper": 1,
            "url": 1,
            "content": 1,
            "llm_response": 1
        }
        
        cursor = db.articles.find(query, projection).sort("date", -1).skip(start_idx).limit(page_size)
        return list(cursor)
    except Exception as e:
        st.error(f"Error fetching announcements: {e}")
        return []

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
        # NEW: Create a dropdown for scraper type selection
        scraper_type_options = ["All"] + scraper_types
        selected_scraper_type = st.selectbox("Filter by Announcement Type", scraper_type_options, help="Filter by the type of announcements (e.g., provost, president, etc.)")

    # Build the query based on the selected school
    query = {}
    if selected_school != "All":
        query["org"] = selected_school

    # NEW: Add scraper type filter to query
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

    # Get count using cached version
    num_announcements = get_filtered_count(db, query)

    st.write(f"Number of announcements: **{num_announcements:,}** (from {start_date.strftime('%B %d, %Y')} onwards)")
    
    # Pagination logic - improved (UPDATED to include scraper type)
    PAGE_SIZE = 20
    total_pages = max((num_announcements - 1) // PAGE_SIZE + 1, 1) if num_announcements > 0 else 1
    
    # Initialize pagination state with unique key based on filters (UPDATED)
    filter_state_key = f"{selected_school}_{selected_scraper_type}_{show_govt_related}_{show_lawsuit_related}_{show_funding_related}_{show_protest_related}_{show_layoff_related}_{show_president_related}_{show_provost_related}_{show_faculty_related}_{show_trustees_related}_{show_trump_related}_{search_term}"
    
    # Reset to page 0 when filters change
    if "last_filter_state" not in st.session_state:
        st.session_state["last_filter_state"] = filter_state_key
        st.session_state["ann_page"] = 0
    elif st.session_state["last_filter_state"] != filter_state_key:
        st.session_state["ann_page"] = 0
        st.session_state["last_filter_state"] = filter_state_key
    
    # Initialize page if not exists
    if "ann_page" not in st.session_state:
        st.session_state["ann_page"] = 0
    
    # Clamp page number if needed
    st.session_state["ann_page"] = max(0, min(st.session_state["ann_page"], total_pages - 1))
    
    col_download, col_clear = st.columns([1, 3])
    
    # Add download button for CSV
    with col_download:
        if num_announcements > 0:
            if st.button("📥 Generate CSV", help="Click to generate and download CSV (may take a moment for large datasets)"):
                with st.spinner("Generating CSV file..."):
                    # For CSV generation, we need all data - use direct query without caching
                    all_cursor = db.articles.find(query, {"_id": 0}).sort("date", -1)
                    all_announcements = list(all_cursor)
                    csv = convert_to_csv(all_announcements, scraper_mapping)
                    st.download_button(
                        label="📥 Download CSV",
                        data=csv,
                        file_name="announcements_data.csv",
                        mime="text/csv",
                    )

    with col_clear:
        if st.button("🗑️ Clear All Filters", help="Reset all category filters"):
            # Clear session state and rerun
            for key in list(st.session_state.keys()):
                if key.startswith(('show_', 'search_term', 'ann_page', 'last_filter_state')):
                    del st.session_state[key]
            st.rerun()

    # PERFORMANCE OPTIMIZATION: Use cached paginated data
    query_str = str(query)  # Convert to string for caching
    paged_announcements = get_paginated_announcements(query_str, st.session_state["ann_page"], PAGE_SIZE)

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

        # OPTIMIZED: Get school info and scraper type using cached mapping
        scraper_path = ann.get("scraper", "")
        school_name = ann.get("org", "Unknown School")
        school_color = "#000000"  # Default
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
        
        # Show search snippet if search term is provided and content exists - FIXED STYLING FOR DARK MODE
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
                    
                    highlighted_snippet = search_pattern.sub(f"<mark style='background-color: #ffeb3b; color: #000000; padding: 2px;'>{search_term}</mark>", snippet)
                    snippets.append(highlighted_snippet)
                
                match_count = len(matches)
                match_text = "match" if match_count == 1 else "matches"
                
                snippets_html = "<br/><br/>".join([f"<strong>Match {i+1}:</strong><br/><em style='color: inherit;'>{snippet}</em>" for i, snippet in enumerate(snippets)])
                
                st.markdown(f"""
                <div style="background-color: rgba(255, 255, 255, 0.1); padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 4px solid #ff6b6b; color: inherit;">
                    <strong style="color: inherit;">🔍 Search Results ({match_count} {match_text}):</strong><br/>
                    <div style="color: inherit;">{snippets_html}</div>
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

    # FIXED pagination controls with proper state management
    col_prev, col_page, col_next = st.columns([1,2,1])
    
    with col_prev:
        prev_disabled = st.session_state["ann_page"] == 0
        if st.button("⬅️ Prev", key="ann_prev", disabled=prev_disabled):
            if st.session_state["ann_page"] > 0:
                st.session_state["ann_page"] -= 1
                st.rerun()
    
    with col_page:
        st.markdown(f"<div style='text-align:center;'>Page <b>{st.session_state['ann_page']+1}</b> of <b>{total_pages}</b></div>", unsafe_allow_html=True)
    
    with col_next:
        next_disabled = st.session_state["ann_page"] >= total_pages - 1
        if st.button("Next ➡️", key="ann_next", disabled=next_disabled):
            if st.session_state["ann_page"] < total_pages - 1:
                st.session_state["ann_page"] += 1
                st.rerun()

def convert_to_csv(announcements, scraper_mapping):
    """Convert announcements data to CSV format using cached scraper mapping."""
    processed_data = []
    
    for ann in announcements:
        processed_ann = {
            "title": ann.get("title", ""),
            "school": ann.get("org", ""),
            "date": ann.get("date"),
            "url": ann.get("url", ""),
        }
        
        # OPTIMIZED: Add scraper type information to CSV using cached mapping
        scraper_path = ann.get("scraper", "")
        scraper_type = "Unknown Type"
        if scraper_path in scraper_mapping:
            scraper_type = scraper_mapping[scraper_path]["name"]
        
        processed_ann["announcement_type"] = scraper_type
        
        # Add LLM response fields if available
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
    return csv_buffer.getvalue()

# PERFORMANCE OPTIMIZATION: Cache scraper status data
@st.cache_data(ttl=60)  # Cache for 1 minute
def get_scraper_status_data(organizations_data):
    """Get processed scraper status data with caching"""
    all_scrapers_data = []
    health_counts = {"healthy": 0, "unhealthy": 0}
    failed_scrapers = []
    
    # Extract all scrapers from all schools into a flat list with school info
    for org in organizations_data:
        school_name = org.get("name", "Unknown School")
        scrapers = org.get("scrapers", [])
        
        for scraper in scrapers:
            # Convert UTC last run date to local time and format it
            last_run = scraper.get("last_run")
            if isinstance(last_run, datetime):
                local_last_run = utc_to_local(last_run)
                last_run_str = local_last_run.strftime("%Y-%m-%d %I:%M:%S %p")
            else:
                last_run_str = "" if last_run is None else str(last_run)
                
            # Extract path suffix (everything after the last dot)
            path = scraper.get("path", "No path")
            if path != "No path":
                path_suffix = path.split('.')[-1]
            else:
                path_suffix = path

            # path number is the last digit of the path suffix if it exists
            path_number = path_suffix[-1] if path_suffix and path_suffix[-1].isdigit() else 1

            # === SIMPLIFIED HEALTH CHECK LOGIC ===
            health_status = "❌ Unhealthy"
            health_reason = "No recent activity"
            
            # Simple check: Has the scraper run in the last 24 hours?
            if last_run and isinstance(last_run, datetime):
                current_time = datetime.now(timezone.utc)
                
                # Make last_run timezone-aware if it's naive (assume UTC)
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                
                # Calculate hours since last run
                hours_since_run = (current_time - last_run).total_seconds() / 3600
                
                if hours_since_run <= 24:  # Ran within last 24 hours
                    health_status = "✅ Healthy"
                    health_reason = "Running normally"
                else:
                    health_status = "❌ Unhealthy"
                    health_reason = f"Last run {int(hours_since_run)}h ago"
            else:
                health_status = "❌ Unhealthy"
                health_reason = "No run data available"
            
            # Count for summary
            if health_status == "✅ Healthy":
                health_counts["healthy"] += 1
            else:
                health_counts["unhealthy"] += 1
                # Add to failed scrapers list for potential Slack notification
                failed_scrapers.append({
                    "School": school_name,
                    "Name": scraper.get("name", "").replace(" announcements", ""),
                    "Health Reason": health_reason,
                    "URL": scraper.get("url", "No URL")
                })

            # Add to the display list - REMOVED the problematic columns
            all_scrapers_data.append({
                "School": school_name,
                "Name": scraper.get("name", "").replace(" announcements", ""),
                "Path": path_number,
                "Health": health_status,
                "Health Reason": health_reason,
                "URL": scraper.get("url", "No URL"),
                "Last Run": last_run_str
            })
    
    return all_scrapers_data, health_counts, failed_scrapers

def display_scraper_status(db):
    """Display the scraper status tab with SIMPLIFIED health check."""
    st.markdown("### URLs")
    
    # Get cached organizations data
    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)
    
    # === AUTO-CHECK FOR DAILY REPORT ===
    # Check and potentially send daily report when this tab is loaded
    if SLACK_WEBHOOK_URL:
        try:
            sent, message = check_and_send_daily_report(db, organizations_data)
            if sent and "failed scrapers" in message:
                st.info(f"📊 {message}")
            elif sent and "healthy" in message:
                st.success(f"✅ {message}")
        except Exception as e:
            st.warning(f"⚠️ Daily report check failed: {e}")
    
    if not organizations_data:
        st.warning("No scraper information found in the database.")
        return
    
    # Count total scrapers
    total_scrapers = sum(len(org.get("scrapers", [])) for org in organizations_data)
    
    # PERFORMANCE OPTIMIZATION: Use cached scraper status data
    all_scrapers_data, health_counts, failed_scrapers = get_scraper_status_data(organizations_data)
    
    # Display summary with health statistics
    st.write(f"Total URLs: **{total_scrapers}** across **{len(organizations_data)}** schools")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("✅ Healthy", health_counts["healthy"])
    with col2:
        st.metric("❌ Unhealthy", health_counts["unhealthy"])
    with col3:
        health_percentage = (health_counts["healthy"] / total_scrapers * 100) if total_scrapers > 0 else 0
        st.metric("Health %", f"{health_percentage:.1f}%")
    with col4:
        # Manual Slack notification button (in addition to daily auto-reports)
        if failed_scrapers and st.button("📢 Send Manual Alert", help=f"Send immediate notification about {len(failed_scrapers)} failed scrapers"):
            with st.spinner("Sending Slack notification..."):
                success = send_slack_notification(failed_scrapers, is_daily_report=False)
                if success:
                    st.success(f"✅ Manual Slack notification sent for {len(failed_scrapers)} failed scrapers")
                else:
                    st.error("❌ Failed to send Slack notification")
    
    # Convert to DataFrame and sort by health status (healthy first), then by school
    df = pd.DataFrame(all_scrapers_data)
    
    # Create sort columns for healthy/unhealthy status
    df['health_priority'] = df['Health'].map({
        "✅ Healthy": 0,
        "❌ Unhealthy": 1
    })

    # Sort by multiple columns separately
    df = df.sort_values(by=['health_priority', 'School', 'Path']).drop('health_priority', axis=1)

    # Ensure 'Path' column is string type for Arrow compatibility
    df["Path"] = df["Path"].astype(str)

    # Use Streamlit's native dataframe with health status column
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=800,
        column_config={
            "Health": st.column_config.TextColumn(
                "Health Status",
                help="✅ Healthy: Ran within last 24 hours | ❌ Unhealthy: Not running recently",
                width="small"
            ),
            "Health Reason": st.column_config.TextColumn(
                "Reason",
                help="Explanation of health status"
            ),
            "URL": st.column_config.LinkColumn(
                "URL",
                help="Source URL for the scraper",
                display_text="Link"
            )
        }
    )

# PERFORMANCE OPTIMIZATION: Cache schools summary data
@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_schools_summary_data(mongo_uri, db_name, organizations_data, start_date):
    """Get schools summary data with caching"""
    client = MongoClient(mongo_uri)
    db = client[db_name]
    
    schools_data = []
    
    for org in organizations_data:
        school_name = org.get("name", "Unknown School")
        school_color = org.get("color", "#000000")
        
        # Count the number of scrapers
        scrapers = org.get("scrapers", [])
        scraper_count = len(scrapers)
        
        # Find the most recent announcement for this school
        latest_announcement = db.articles.find_one(
            {"org": school_name},
            {"title": 1, "date": 1, "url": 1},
            sort=[("date", -1)]
        )
        
        # Extract announcement details
        latest_title = "No announcements"
        latest_date = "N/A"
        latest_url = ""
        sort_date = datetime(1970, 1, 1)  # Default old date for sorting
        
        if latest_announcement:
            latest_title = latest_announcement.get("title", "No title")
            
            date_value = latest_announcement.get("date")
            if isinstance(date_value, datetime):
                # Convert UTC to local
                local_date = utc_to_local(date_value)
                latest_date = local_date.strftime("%Y-%m-%d")
                sort_date = date_value  # Store actual datetime for sorting
            else:
                latest_date = str(date_value) if date_value else "Unknown date"
                
            latest_url = latest_announcement.get("url", "")
        
        # Count total announcements for this school
        announcement_count = db.articles.count_documents({"org": school_name, "date": {"$gte": start_date}})
        
        # Add data to the list
        schools_data.append({
            "School": school_name,
            "Color": school_color,
            "Scrapers": scraper_count,
            "Announcements": announcement_count,
            "Latest Date": latest_date,
            "Sort Date": sort_date,  # Hidden column for sorting
            "Latest Title": latest_title,
            "URL": latest_url
        })
    
    return schools_data

def display_schools_summary(db):
    """Display a summary table of all schools with their most recent announcements."""
    st.markdown("### Schools Summary")
    
    # Get cached organizations data
    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)
    
    if not organizations_data:
        st.warning("No schools found in the database.")
        return
    
    # PERFORMANCE OPTIMIZATION: Use cached schools summary data
    schools_data = get_schools_summary_data(MONGO_URI, DB_NAME, organizations_data, start_date)
    
    # Convert to DataFrame
    df = pd.DataFrame(schools_data)
    
    # Sort by 'Sort Date' in descending order (most recent first)
    df = df.sort_values(by="Sort Date", ascending=False)
    
    # Create a DataFrame with the columns we want to display
    display_df = pd.DataFrame({
        "School": [f"{school['School']}" for school in df.to_dict('records')],
        "Scrapers": [school["Scrapers"] for school in df.to_dict('records')],
        "Announcements": [school["Announcements"] for school in df.to_dict('records')],
        "Latest Date": [school["Latest Date"] for school in df.to_dict('records')],
        "Latest Title": [school["Latest Title"] for school in df.to_dict('records')],
        "URL": [school["URL"] for school in df.to_dict('records')]
    })

    # Add a note about the total number of schools
    st.write(f"Total schools: **{len(organizations_data)}**")
    st.write(f"Total announcements (since {start_date.strftime('%B %d, %Y')}): **{df['Announcements'].sum():,}**")
    
    # Use st.dataframe with basic configuration
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=800,
        column_config={
            "URL": st.column_config.LinkColumn(
                "URL", 
                help="Link to the most recent announcement.", 
                display_text="Link"
            )
        }
    )

def main():
    # Set page config - MUST be the first Streamlit command
    st.set_page_config(
        page_title="Campus Announcements Tracker [DRAFT]",
        page_icon="🎓",
        layout="centered",
        initial_sidebar_state="expanded",
        menu_items={"About": "This is a draft version of the Campus Announcements Tracker."}
    )
    
    st.title("Campus Announcements [DRAFT]")
    st.markdown("Announcements from the provosts' and presidents' offices at select universities.")

    db = get_db()
    
    # Create tabs for different views
    tab1, tab2, tab3 = st.tabs(["Announcements", "Schools", "URLs"])
    
    with tab1:
        display_announcements(db)
    
    with tab2:
        display_schools_summary(db)
        
    with tab3:
        display_scraper_status(db)

if __name__ == "__main__":
    main()