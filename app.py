#!/usr/bin/env python3
"""
Simple Streamlit front-end to display announcements from MongoDB.
Shows title, school, date, URL, and source base URL for each announcement.
"""
import os
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

# Define the start date for filtering announcements
start_date = datetime(2025, 1, 1)

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

# Connect to MongoDB
def get_db():
    client = MongoClient(MONGO_URI)
    return client[DB_NAME]


def display_announcements(db):
    """Display the announcements view."""
    st.markdown('⚠️ Please note that this is an unedited **first draft** proof-of-concept. Classifications **WILL BE** inaccurate. ⚠️', unsafe_allow_html=True)

    # Fetch all unique schools from the database
    schools_cursor = db.schools.find({}, {"_id": 0, "name": 1})
    schools = [{"name": school.get("name")} for school in schools_cursor]

    # Create a dropdown for school selection with alphabetically sorted options
    school_names = [school["name"] for school in schools]
    school_names.sort()  # Sort school names alphabetically
    school_options = ["All"] + school_names
    selected_school = st.selectbox("Filter by School", school_options)

    st.markdown('>_Check any box to filter for items identified by our LLM as related to that category.<br/>Hover on each question mark for more information about the criteria._', unsafe_allow_html=True)

    # Create a columns layout for the checkboxes
    col1, col2 = st.columns(2)

    llm_prompt = (
        "Analyze this university announcement and determine if it:\n"
        "1. Is related to the university responding to federal government or federal administration actions\n"
        "2. Mentions a lawsuit or legal action\n"
        "3. Discusses funding cuts or funding issues\n"
        "4. Relates to campus protests or disruptions\n\n"
        "For each category, provide whether it's related (true/false) and if true, a brief reason."
    )

    with col1:
        show_govt_related = st.checkbox("👨‍⚖️ Government Related", 
            help="LLM Prompt: Items where the university is supporting or opposing federal government or administration actions")
        show_lawsuit_related = st.checkbox("⚖️ Lawsuit Related", 
            help="LLM Prompt: Items mentioning lawsuits or legal actions related to the university")        
        
    with col2:
        show_funding_related = st.checkbox("💰 Funding Related", 
            help="LLM Prompt: Items discussing funding cuts or financial issues")
        show_protest_related = st.checkbox("🪧 Protest Related", 
            help="LLM Prompt: Items mentioning campus protests or disruptions")

    # Add a text search bar for content search
    search_term = st.text_input("🔍 Search announcement content", value="", help="Enter keywords to search announcement content (case-insensitive)")

    # Build the query based on the selected school
    query = {}
    if selected_school != "All":
        query["school"] = selected_school

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

    # Combine filters with OR if any are selected
    if filter_conditions:
        query["$or"] = filter_conditions

    # Add date filter for announcements after the start date
    query["date"] = {"$gte": start_date}

    # Add content search filter if search_term is provided
    if search_term.strip():
        query["content"] = {"$regex": search_term, "$options": "i"}

    # Fetch announcements for current page (remove pagination here)
    cursor = db.announcements.find(query, {"_id": 0}).sort("date", -1)
    announcements = list(cursor)
    num_announcements = len(announcements)

    st.write(f"Number of announcements: **{num_announcements}** (from {start_date.strftime('%B %d, %Y')} onwards)")
    
    # Add download button for CSV
    if announcements:
        # Create a download button
        csv = convert_to_csv(announcements, db)
        st.download_button(
            label="📥 Download data as CSV",
            data=csv,
            file_name="announcements_data.csv",
            mime="text/csv",
        )

    # --- Pagination logic ---
    PAGE_SIZE = 10
    total_pages = max((num_announcements - 1) // PAGE_SIZE + 1, 1)
    if "ann_page" not in st.session_state:
        st.session_state["ann_page"] = 0
    # Clamp page number if needed
    st.session_state["ann_page"] = min(st.session_state["ann_page"], total_pages - 1)
    start_idx = st.session_state["ann_page"] * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    paged_announcements = announcements[start_idx:end_idx]

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
        school_name = ann.get("school", "Unknown School")
        school_color = "#000000"  # Default to black if no color is found
        school_doc = db.schools.find_one({"name": school_name}, {"color": 1})
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

        # LLM Response Section
        if ann.get("llm_response"):
            llm_response = ann.get("llm_response")

            # Check each category and display if related
            categories_found = []

            if llm_response.get("government_related", {}).get("related"):
                categories_found.append(("👨‍⚖️ Government", llm_response["government_related"]["reason"]))

            if llm_response.get("lawsuit_related", {}).get("related"):
                categories_found.append(("⚖️ Lawsuit", llm_response["lawsuit_related"]["reason"]))

            if llm_response.get("funding_related", {}).get("related"):
                categories_found.append(("💰 Funding", llm_response["funding_related"]["reason"]))

            if llm_response.get("protest_related", {}).get("related"):
                categories_found.append(("🪧 Protest", llm_response["protest_related"]["reason"]))

            # Display all found categories
            for category, reason in categories_found:
                st.markdown(f"🤖 **LLM Says ({category}):** {reason}")

        st.markdown("<hr style=\"margin-top:0.5em;margin-bottom:0.5em;\">", unsafe_allow_html=True)

    # Pagination controls below announcements
    col_prev, col_page, col_next = st.columns([1,2,1])
    with col_prev:
        if st.button("⬅️ Prev", key="ann_prev", disabled=st.session_state["ann_page"] == 0):
            st.session_state["ann_page"] = max(st.session_state["ann_page"] - 1, 0)
            st.stop()
    with col_page:
        st.markdown(f"<div style='text-align:center;'>Page <b>{st.session_state['ann_page']+1}</b> of <b>{total_pages}</b></div>", unsafe_allow_html=True)
    with col_next:
        if st.button("Next ➡️", key="ann_next", disabled=st.session_state["ann_page"] >= total_pages - 1):
            st.session_state["ann_page"] = min(st.session_state["ann_page"] + 1, total_pages - 1)
            st.stop()


def convert_to_csv(announcements, db):
    """Convert announcements data to CSV format, excluding content column."""
    # Create a list to store processed announcements
    processed_data = []
    
    for ann in announcements:
        # Extract base data
        processed_ann = {
            "title": ann.get("title", ""),
            "school": ann.get("school", ""),
            "date": ann.get("date"),
            "url": ann.get("url", ""),
            # "base_url": ann.get("base_url", "")
        }
        
        # Add LLM response fields if available
        llm_response = ann.get("llm_response", {})
        
        # Add government related info
        govt_data = llm_response.get("government_related", {})
        processed_ann["govt_related"] = govt_data.get("related", False)
        processed_ann["govt_reason"] = govt_data.get("reason", "") if govt_data.get("related") else ""
        
        # Add lawsuit related info
        lawsuit_data = llm_response.get("lawsuit_related", {})
        processed_ann["lawsuit_related"] = lawsuit_data.get("related", False)
        processed_ann["lawsuit_reason"] = lawsuit_data.get("reason", "") if lawsuit_data.get("related") else ""
        
        # Add funding related info
        funding_data = llm_response.get("funding_related", {})
        processed_ann["funding_related"] = funding_data.get("related", False)
        processed_ann["funding_reason"] = funding_data.get("reason", "") if funding_data.get("related") else ""
        
        # Add protest related info
        protest_data = llm_response.get("protest_related", {})
        processed_ann["protest_related"] = protest_data.get("related", False)
        processed_ann["protest_reason"] = protest_data.get("reason", "") if protest_data.get("related") else ""
        
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
    """Display the scraper status tab."""
    st.markdown("### URLs")
    
    # Fetch all schools with scraper information
    schools = list(db.schools.find(
        {"scrapers": {"$exists": True}}, 
        {"name": 1, "scrapers": 1, "last_run": 1}
    ).sort("name", 1))
    
    if not schools:
        st.warning("No scraper information found in the database.")
        return
    
    # Count total scrapers
    total_scrapers = sum(len(school.get("scrapers", [])) for school in schools)
    st.write(f"Total URLs: **{total_scrapers}** across **{len(schools)}** schools")
    
    # Create a list to hold all scrapers data
    all_scrapers_data = []
    
    # Extract all scrapers from all schools into a flat list with school info
    for school in schools:
        school_name = school.get("name", "Unknown School")
        scrapers = school.get("scrapers", [])
        
        for scraper in scrapers:
            # Convert UTC last run date to local time and format it
            last_run = scraper.get("last_run")
            if isinstance(last_run, datetime):
                # Convert UTC to local
                local_last_run = utc_to_local(last_run)
                last_run_str = local_last_run.strftime("%Y-%m-%d %I:%M:%S %p")
            else:
                last_run_str = "" if last_run is None else str(last_run)
                
            # Get last run count (indicates successful runs)
            last_run_count = scraper.get("last_run_count", 0)
            
            # Convert UTC last non-empty run date to local time and format it
            last_nonempty_run = scraper.get("last_nonempty_run")
            if isinstance(last_nonempty_run, datetime):
                # Convert UTC to local
                local_last_nonempty_run = utc_to_local(last_nonempty_run)
                last_nonempty_run_str = local_last_nonempty_run.strftime("%Y-%m-%d %I:%M:%S %p")
            else:
                last_nonempty_run_str = "" if last_nonempty_run is None else str(last_nonempty_run)
                
            # Get last non-empty run count (indicates successful runs with content)
            last_nonempty_run_count = scraper.get("last_nonempty_run_count", "")
            
            # Extract path suffix (everything after the last dot)
            path = scraper.get("path", "No path")
            if path != "No path":
                path_suffix = path.split('.')[-1]
            else:
                path_suffix = path

            # path number is the last digit of the path suffix if it exists
            path_number = path_suffix[-1] if path_suffix and path_suffix[-1].isdigit() else 1

            # Add to the list
            all_scrapers_data.append({
                "School": school_name,
                "Name": scraper.get("name", "").replace(" announcements", ""),
                "Path": path_number,
                "URL": scraper.get("url", "No URL"),
                "Last Run": last_run_str,
                "Last Run Count": last_run_count,
                "Last Success": last_nonempty_run_str,
                "Success Count": last_nonempty_run_count
            })
    
    # Convert to DataFrame
    df = pd.DataFrame(all_scrapers_data)\
        .sort_values(by=["Last Success", "School", "Path"], ascending=[False, True, True])

    # Use Streamlit's native dataframe
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=800,
        column_config={
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


def display_schools_summary(db):
    """Display a summary table of all schools with their most recent announcements."""
    st.markdown("### Schools Summary")
    
    # Get all schools from the database
    schools = list(db.schools.find({}, {"name": 1, "color": 1, "scrapers": 1}).sort("name", 1))
    
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
        latest_announcement = db.announcements.find_one(
            {"school": school_name},
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
        announcement_count = db.announcements.count_documents({"school": school_name, "date": {"$gte": start_date}})
        
        
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
