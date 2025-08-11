##!/usr/bin/env python3
"""
Simple Streamlit front-end to display announcements from MongoDB.

Shows title, school, date, URL, and source base URL for each announcement.
OPTIMIZED VERSION with fast pagination using MongoDB skip/limit.
UPDATED: Simplified health checks and Slack notifications for failed scrapers.
"""

import os
import requests
from datetime import datetime, timezone
import streamlit as st
from pymongo import MongoClient
import pandas as pd
import io
import pytz
from tzlocal import get_localzone


# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(override=True)

# Configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "campus_data")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# Define the start date for filtering announcements
start_date = datetime(2025, 1, 1, tzinfo=timezone.utc)

# Function to convert UTC datetime to local time
def utc_to_local(utc_dt):
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

# Connect to MongoDB with connection pooling
@st.cache_resource
def get_db():
    client = MongoClient(MONGO_URI, maxPoolSize=10)
    return client[DB_NAME]

@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_filtered_count(mongo_uri, db_name, query_str):
    """Get count of documents matching query - cached for performance"""
    import json
    client = MongoClient(mongo_uri)
    db = client[db_name]
    query = json.loads(query_str)
    return db.articles.count_documents(query)

@st.cache_data(ttl=60)  # Cache for 1 minute  
def get_schools_list(mongo_uri, db_name):
    """Get list of schools - cached for performance"""
    client = MongoClient(mongo_uri)
    db = client[db_name]
    schools_cursor = db.orgs.find({}, {"_id": 0, "name": 1})
    return [{"name": org.get("name")} for org in schools_cursor]

def send_slack_notification(failed_scrapers):
    """Send Slack notification for completely failed scrapers"""
    if not SLACK_WEBHOOK_URL or not failed_scrapers:
        return False
    
    failed_count = len(failed_scrapers)
    
    # Build the message
    message = f"🚨 *{failed_count} Campus Scraper{'s' if failed_count != 1 else ''} Failed*\n\n"
    
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
        "username": "Campus Scraper Monitor",
        "icon_emoji": ":warning:"
    }
    
    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error sending Slack notification: {e}")
        return False

def display_announcements(db):
    """Display the announcements view with optimized pagination."""
    st.markdown('⚠️ Please note that this is an unedited **first draft** proof-of-concept. Classifications **WILL BE** inaccurate. ⚠️', unsafe_allow_html=True)

    # Use cached schools list
    schools = get_schools_list(MONGO_URI, DB_NAME)

    st.markdown('>_Check any box to filter for items identified by our LLM as related to that category.<br/>Hover on each question mark for more information about the criteria._', unsafe_allow_html=True)

    # Create a columns layout for the checkboxes (4 columns with 3 checkboxes each)
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

    # Create a dropdown for school selection with alphabetically sorted options
    school_names = [school["name"] for school in schools]
    school_names.sort()  # Sort school names alphabetically
    school_options = ["All"] + school_names
    selected_school = st.selectbox("Filter by School", school_options)

    # Build the query based on the selected school
    query = {}
    if selected_school != "All":
        query["org"] = selected_school

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

    # === CRITICAL OPTIMIZATION: Use cached count query ===
    import json
    query_str = json.dumps(query, default=str)  # Convert datetime to string for caching
    num_announcements = get_filtered_count(MONGO_URI, DB_NAME, query_str)

    st.write(f"Number of announcements: **{num_announcements}** (from {start_date.strftime('%B %d, %Y')} onwards)")
    
    # === PAGINATION LOGIC - OPTIMIZED ===
    PAGE_SIZE = 20
    total_pages = max((num_announcements - 1) // PAGE_SIZE + 1, 1)
    
    # Initialize pagination state
    if "ann_page" not in st.session_state:
        st.session_state["ann_page"] = 0
    
    # Reset to page 0 when filters change
    filter_state_key = f"{selected_school}_{show_govt_related}_{show_lawsuit_related}_{show_funding_related}_{show_protest_related}_{show_layoff_related}_{show_president_related}_{show_provost_related}_{show_faculty_related}_{show_trustees_related}_{show_trump_related}_{search_term}"
    if "last_filter_state" not in st.session_state:
        st.session_state["last_filter_state"] = filter_state_key
    elif st.session_state["last_filter_state"] != filter_state_key:
        st.session_state["ann_page"] = 0
        st.session_state["last_filter_state"] = filter_state_key
    
    # Clamp page number if needed
    st.session_state["ann_page"] = min(st.session_state["ann_page"], total_pages - 1)
    
    col_download, col_clear = st.columns([1, 3])
    
    # Add download button for CSV (only fetch all data when explicitly requested)
    with col_download:
        if num_announcements > 0:
            if st.button("📥 Generate CSV", help="Click to generate and download CSV (may take a moment for large datasets)"):
                # Only fetch all data when explicitly requested
                with st.spinner("Generating CSV file..."):
                    all_cursor = db.articles.find(query, {"_id": 0}).sort("date", -1)
                    all_announcements = list(all_cursor)
                    csv = convert_to_csv(all_announcements, db)
                    st.download_button(
                        label="📥 Download CSV",
                        data=csv,
                        file_name="announcements_data.csv",
                        mime="text/csv",
                    )

    with col_clear:
        if st.button("🗑️ Clear All Filters", help="Reset all category filters"):
            # Use JavaScript to refresh the page completely
            st.components.v1.html("""
                <script>
                    window.parent.location.reload();
                </script>
            """, height=0)

    # === CRITICAL OPTIMIZATION: Only fetch current page data ===
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
            # Convert UTC to local
            local_date = utc_to_local(date_value)
            date_str = local_date.strftime("%Y-%m-%d %I:%M:%S %p")
        else:
            date_str = str(date_value)

        # Get school name and color
        school_name = ann.get("org", "Unknown School")
        school_color = "#000000"  # Default to black if no color is found
        school_doc = db.orgs.find_one({"name": school_name}, {"color": 1})
        if school_doc and school_doc.get("color"):
            school_color = school_doc["color"]

        # Announcement URL
        url = ann.get("url", "")
        # Source base URL (if runner annotated it)
        base_url = ann.get("base_url", "")

        st.subheader(title)
        content = ann.get("content", "")
        announcement_html = f"""
        <p style="margin-bottom: 0.5em;">
            <strong>School:</strong> <span style="background-color:{school_color}; padding:2px 4px; border-radius:4px; color:#ffffff;">{school_name}</span><br>
            <strong>Date:</strong> {date_str}<br>
            <strong>Content Scraped:</strong> {'✅' if content else '👎'}<br>
            <strong>Announcement URL:</strong><br/> <a href="{url}">{url}</a>
        </p>
        """
        st.markdown(announcement_html, unsafe_allow_html=True)
        
        # Show search snippet if search term is provided and content exists
        if search_term.strip() and content:
            # Find all occurrences of the search term in the content (case-insensitive)
            import re
            search_pattern = re.compile(re.escape(search_term), re.IGNORECASE)
            matches = list(search_pattern.finditer(content))
            
            if matches:
                snippets = []
                for i, match in enumerate(matches):
                    # Extract snippet around each match
                    start_pos = max(0, match.start() - 100)  # 100 chars before match
                    end_pos = min(len(content), match.end() + 100)  # 100 chars after match
                    snippet = content[start_pos:end_pos]
                    
                    # Add ellipsis if we truncated
                    if start_pos > 0:
                        snippet = "..." + snippet
                    if end_pos < len(content):
                        snippet = snippet + "..."
                    
                    # Highlight the search term in the snippet
                    highlighted_snippet = search_pattern.sub(f"<mark style='background-color: yellow; padding: 2px;'>{search_term}</mark>", snippet)
                    snippets.append(highlighted_snippet)
                
                # Display all snippets
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

            # Check each category and display if related AND selected by user
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

    # === OPTIMIZED PAGINATION CONTROLS ===
    col_prev, col_page, col_next = st.columns([1,2,1])
    with col_prev:
        if st.button("⬅️ Prev", key="ann_prev", disabled=st.session_state["ann_page"] == 0):
            st.session_state["ann_page"] = max(st.session_state["ann_page"] - 1, 0)
            st.rerun()  # Use st.rerun() instead of st.stop()
    with col_page:
        st.markdown(f"<div style='text-align:center;'>Page <b>{st.session_state['ann_page']+1}</b> of <b>{total_pages}</b></div>", unsafe_allow_html=True)
    with col_next:
        if st.button("Next ➡️", key="ann_next", disabled=st.session_state["ann_page"] >= total_pages - 1):
            st.session_state["ann_page"] = min(st.session_state["ann_page"] + 1, total_pages - 1)
            st.rerun()  # Use st.rerun() instead of st.stop()


def convert_to_csv(announcements, db):
    """Convert announcements data to CSV format, excluding content column."""
    # Create a list to store processed announcements
    processed_data = []
    
    for ann in announcements:
        # Extract base data
        processed_ann = {
            "title": ann.get("title", ""),
            "school": ann.get("org", ""),
            "date": ann.get("date"),
            "url": ann.get("url", ""),
            # "base_url": ann.get("base_url", "")
        }
        
        # Add LLM response fields if available
        llm_response = ann.get("llm_response", {})
        
        # Add all classification fields
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
    
    # Convert to pandas DataFrame and then to CSV
    df = pd.DataFrame(processed_data)
    
    # Convert datetime objects from UTC to local time and then to strings
    if 'date' in df.columns:
        df['date'] = df['date'].apply(lambda x: utc_to_local(x).strftime('%Y-%m-%d %I:%M:%S %p') if isinstance(x, datetime) else str(x))
    
    # Convert to CSV
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    return csv_buffer.getvalue()


def display_scraper_status(db):
    """Display the scraper status tab with SIMPLIFIED health check."""
    st.markdown("### URLs")
    
    # Fetch all schools with scraper information
    schools = list(db.orgs.find(
        {"scrapers": {"$exists": True}}, 
        {"name": 1, "scrapers": 1, "last_run": 1}
    ).sort("name", 1))
    
    if not schools:
        st.warning("No scraper information found in the database.")
        return
    
    # Count total scrapers
    total_scrapers = sum(len(school.get("scrapers", [])) for school in schools)
    
    # Create a list to hold all scrapers data
    all_scrapers_data = []
    health_counts = {"healthy": 0, "unhealthy": 0}
    failed_scrapers = []  # For Slack notifications
    
    # Extract all scrapers from all schools into a flat list with school info
    for school in schools:
        school_name = school.get("name", "Unknown School")
        scrapers = school.get("scrapers", [])
        
        for scraper in scrapers:
            # Convert UTC last run date to local time and format it
            last_run = scraper.get("last_run")
            if isinstance(last_run, datetime):
                local_last_run = utc_to_local(last_run)
                last_run_str = local_last_run.strftime("%Y-%m-%d %I:%M:%S %p")
            else:
                last_run_str = "" if last_run is None else str(last_run)
                
            # Get last run count
            last_run_count = scraper.get("last_run_count", 0)
            
            # Convert UTC last non-empty run date to local time and format it
            last_nonempty_run = scraper.get("last_nonempty_run")
            if isinstance(last_nonempty_run, datetime):
                local_last_nonempty_run = utc_to_local(last_nonempty_run)
                last_nonempty_run_str = local_last_nonempty_run.strftime("%Y-%m-%d %I:%M:%S %p")
            else:
                last_nonempty_run_str = "" if last_nonempty_run is None else str(last_nonempty_run)
                
            # Get last non-empty run count
            last_nonempty_run_count = scraper.get("last_nonempty_run_count", "")
            
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
<<<<<<< HEAD
                    health_status = "✅ Healthy"
                    health_reason = "Running normally"
=======
                    # Check condition 1: Got new things (last_nonempty_run is recent)
                    got_new_content = False
                    if last_nonempty_run and isinstance(last_nonempty_run, datetime):
                        # Handle timezone-naive datetime for last_nonempty_run too
                        if last_nonempty_run.tzinfo is None:
                            last_nonempty_run = last_nonempty_run.replace(tzinfo=timezone.utc)
                        
                        hours_since_content = (current_time - last_nonempty_run).total_seconds() / 3600


                        hours_since_content = (datetime.now(timezone.utc) - last_nonempty_run).total_seconds() / 3600
                        if hours_since_content <= 168:  # Got content within last week
                            got_new_content = True
                    
                    # Check condition 2: Most recent item already in DB (run count > nonempty count indicates duplicate detection)
                    found_duplicates = False
                    if (last_run_count > 0 and last_nonempty_run_count and 
                        isinstance(last_nonempty_run_count, (int, float)) and 
                        last_run_count > last_nonempty_run_count):
                        found_duplicates = True
                    
                    # Health status based on conditions
                    if got_new_content:
                        health_status = "✅ Healthy"
                        health_reason = "Found new content recently"
                    elif found_duplicates:
                        health_status = "✅ Healthy"
                        health_reason = "Running & detecting existing content"
                    else:
                        health_status = "⚠️ Warning"
                        health_reason = "Running but no new content found"
>>>>>>> 4224c1db70b4af1ed5cf14a26f125b63da366840
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

            # Add to the display list
            all_scrapers_data.append({
                "School": school_name,
                "Name": scraper.get("name", "").replace(" announcements", ""),
                "Path": path_number,
                "Health": health_status,
                "Health Reason": health_reason,
                "URL": scraper.get("url", "No URL"),
                "Last Run": last_run_str,
                "Last Run Count": last_run_count,
                "Last Success": last_nonempty_run_str,
                "Success Count": last_nonempty_run_count
            })
    
    # Display summary with health statistics
    st.write(f"Total URLs: **{total_scrapers}** across **{len(schools)}** schools")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("✅ Healthy", health_counts["healthy"])
    with col2:
        st.metric("❌ Unhealthy", health_counts["unhealthy"])
    with col3:
        health_percentage = (health_counts["healthy"] / total_scrapers * 100) if total_scrapers > 0 else 0
        st.metric("Health %", f"{health_percentage:.1f}%")
    with col4:
        # Add Slack notification button
        if failed_scrapers and st.button("📢 Send Slack Alert", help=f"Send notification about {len(failed_scrapers)} failed scrapers"):
            with st.spinner("Sending Slack notification..."):
                success = send_slack_notification(failed_scrapers)
                if success:
                    st.success(f"✅ Slack notification sent for {len(failed_scrapers)} failed scrapers")
                else:
                    st.error("❌ Failed to send Slack notification")
    
    # Convert to DataFrame and sort by health status (healthy first), then by school
    df = pd.DataFrame(all_scrapers_data)
    
    # SIMPLIFIED: Create sort columns for only healthy/unhealthy
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

def main():
    # Set page config - MUST be the first Streamlit command
    st.set_page_config(
        page_title="Campus Announcements Tracker [DRAFT]",
        page_icon="🎓",
        layout="centered",
        initial_sidebar_state="expanded",
        menu_items={"About": "This is a draft version of the Campus Announcements Tracker."}
    )
    
    st.title("Campus Announcements [ DRAFT]")
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


def display_schools_summary(db):
    """Display a summary table of all schools with their most recent announcements."""
    st.markdown("### Schools Summary")
    
    # Get all schools from the database
    schools = list(db.orgs.find({}, {"name": 1, "color": 1, "scrapers": 1}).sort("name", 1))
    
    if not schools:
        st.warning("No schools found in the database.")
        return
    
    # Create a DataFrame to hold school information
    schools_data = []
    
    for school in schools:
        school_name = school.get("name", "Unknown School")
        school_color = school.get("color", "#000000")
        
        # Count the number of scrapers
        scrapers = school.get("scrapers", [])
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
    
    # Convert to DataFrame
    df = pd.DataFrame(schools_data)
    
    # Sort by 'Sort Date' in descending order (most recent first)
    df = df.sort_values(by="Sort Date", ascending=False)
    
    # Add school colors as a background in the School column for visual distinction
    # We'll use a separate column for display with Streamlit's native components
    
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
    st.write(f"Total schools: **{len(schools)}**")
    st.write(f"Total announcements (since {start_date.strftime('%B %d, %Y')}): **{df['Announcements'].sum()}**")
    
    # Use st.dataframe with basic configuration
    st.dataframe(
        display_df,#.drop(columns=['URL']),  # Exclude URL column from display as we've embedded the links
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
    


if __name__ == "__main__":
    main()